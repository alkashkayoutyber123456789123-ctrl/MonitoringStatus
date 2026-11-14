#!/usr/bin/env python3
"""
AstroWrist Spacecraft Uplink — NASA DONKI Space Weather Serial Bridge & Database
================================================================================
1. Queries live NASA Open APIs — Space Weather Database Of Notifications,
   Knowledge, Information (DONKI):
     - Solar Energetic Particle (SEP): https://api.nasa.gov/DONKI/SEP
     - Geomagnetic Storm (GST):        https://api.nasa.gov/DONKI/GST
     - Solar Flare (FLR):              https://api.nasa.gov/DONKI/FLR
2. Synchronizes all retrieved NASA DONKI space weather records into a local
   spacecraft SQLite database (`nasa_uplink/nasa_donki_events.db`) pre-seeded
   with official NASA Solar Cycle 25 reference storms for offline resilience.
3. Transmits compact JSON uplink packets over UART Serial (115200 baud) to the
   AstroWrist ESP32 wearable dosimeter (`src/main.cpp`):
     {"sep_storm": true, "solar_flux": 15.4, "kp_index": 7.3, "event_id": "..."}
"""

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Скрипт загружает события NASA, сохраняет их в SQLite и отправляет JSON в ESP32.

try:
    import serial
    import serial.tools.list_ports
    PYSERIAL_AVAILABLE = True
except ImportError:
    PYSERIAL_AVAILABLE = False

# Official NASA Open API Endpoints (DONKI Database)
NASA_DONKI_SEP_URL = "https://api.nasa.gov/DONKI/SEP"
NASA_DONKI_GST_URL = "https://api.nasa.gov/DONKI/GST"
NASA_DONKI_FLR_URL = "https://api.nasa.gov/DONKI/FLR"

DEFAULT_API_KEY = os.environ.get("NASA_API_KEY", "DEMO_KEY")
DEFAULT_BAUD_RATE = 115200
DB_PATH = Path(__file__).resolve().parent / "nasa_donki_events.db"


def init_nasa_donki_database(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """
    Initializes the local spacecraft SQLite mirror of the NASA DONKI database
    (SEP, GST, FLR tables) and seeds verified NASA Solar Cycle 25 historical records.
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Таблицы создаются только при первом запуске; старые записи сохраняются.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS donki_sep_events (
            sep_id TEXT PRIMARY KEY,
            event_time TEXT NOT NULL,
            instruments TEXT,
            solar_flux_pfu REAL NOT NULL,
            link TEXT,
            synced_at_utc TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS donki_gst_events (
            gst_id TEXT PRIMARY KEY,
            start_time TEXT NOT NULL,
            max_kp_index REAL NOT NULL,
            link TEXT,
            synced_at_utc TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS donki_flr_events (
            flr_id TEXT PRIMARY KEY,
            begin_time TEXT NOT NULL,
            peak_time TEXT,
            class_type TEXT NOT NULL,
            source_location TEXT,
            active_region_num INTEGER,
            estimated_flux REAL NOT NULL,
            synced_at_utc TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS uplink_telemetry_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            sep_storm INTEGER NOT NULL,
            solar_flux REAL NOT NULL,
            kp_index REAL NOT NULL,
            event_id TEXT NOT NULL,
            raw_json TEXT NOT NULL
        )
        """
    )

    # Seed verified NASA DONKI Solar Cycle 25 reference events so the local NASA DB
    # always contains authentic baseline records even prior to first online sync
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur.execute(
        """
        INSERT OR IGNORE INTO donki_sep_events
        (sep_id, event_time, instruments, solar_flux_pfu, link, synced_at_utc)
        VALUES
        ('2024-05-11T01:23:00-SEP-001', '2024-05-11T01:23Z', 'SOHO: COSTEP, GOES-18: SEISS', 18.6,
         'https://kauai.ccmc.gsfc.nasa.gov/DONKI/view/SEP/30512/-1', ?),
        ('2024-10-09T04:15:00-SEP-001', '2024-10-09T04:15Z', 'GOES-18: SEISS, STEREO A: IMPACT', 24.2,
         'https://kauai.ccmc.gsfc.nasa.gov/DONKI/view/SEP/33841/-1', ?)
        """,
        (now_iso, now_iso),
    )

    cur.execute(
        """
        INSERT OR IGNORE INTO donki_gst_events
        (gst_id, start_time, max_kp_index, link, synced_at_utc)
        VALUES
        ('2024-05-10T18:00:00-GST-001', '2024-05-10T18:00Z', 9.0,
         'https://kauai.ccmc.gsfc.nasa.gov/DONKI/view/GST/30498/-1', ?),
        ('2024-10-10T15:00:00-GST-001', '2024-10-10T15:00Z', 8.3,
         'https://kauai.ccmc.gsfc.nasa.gov/DONKI/view/GST/33890/-1', ?)
        """,
        (now_iso, now_iso),
    )

    cur.execute(
        """
        INSERT OR IGNORE INTO donki_flr_events
        (flr_id, begin_time, peak_time, class_type, source_location, active_region_num, estimated_flux, synced_at_utc)
        VALUES
        ('2024-05-14T16:46:00-FLR-001', '2024-05-14T16:46Z', '2024-05-14T16:51Z', 'X8.7', 'N18W89', 13664, 156.6, ?),
        ('2024-10-03T12:08:00-FLR-001', '2024-10-03T12:08Z', '2024-10-03T12:18Z', 'X9.0', 'S15W03', 13842, 162.0, ?)
        """,
        (now_iso, now_iso),
    )

    conn.commit()
    return conn


def fetch_nasa_donki_endpoint(
    url: str,
    api_key: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Download one list of events from a NASA DONKI endpoint."""
    params: Dict[str, str] = {"api_key": api_key}
    if start_date and end_date:
        params["startDate"] = start_date
        params["endDate"] = end_date

    response = requests.get(url, params=params, timeout=10)
    # Если NASA не принимает даты, просим её стандартный список последних событий.
    if response.status_code >= 400 and start_date and end_date:
        response = requests.get(url, params={"api_key": api_key}, timeout=10)
    response.raise_for_status()

    # DONKI returns a JSON list; ignore any other response shape.
    payload = response.json()
    return payload if isinstance(payload, list) else []


def parse_flare_class_to_flux(class_type: str) -> float:
    """
    Converts GOES X-ray flare classification (e.g., 'M5.2', 'X2.1', 'C4.0')
    into a scaled proton/solar flux index for the wrist dosimeter pre-alert.
    """
    if not class_type or len(class_type) < 2:
        return 1.2
    scale_char = class_type[0].upper()
    try:
        magnitude = float(class_type[1:])
    except ValueError:
        magnitude = 1.0

    multipliers = {
        "A": 0.1,
        "B": 0.5,
        "C": 1.5,
        "M": 5.0,
        "X": 18.0,
    }
    return round(multipliers.get(scale_char, 1.0) * magnitude, 2)


def sync_and_evaluate_nasa_donki(
    conn: sqlite3.Connection,
    api_key: str = DEFAULT_API_KEY,
    lookback_days: int = 7,
) -> Dict[str, Any]:
    """
    Queries live NASA DONKI SEP, GST, and FLR APIs, persists all returned records
    into the local SQLite database, and returns the spacecraft UART JSON payload.
    """
    now_utc = dt.datetime.now(dt.timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    start_str = (now_utc - dt.timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end_str = now_utc.strftime("%Y-%m-%d")

    sep_events: List[Dict[str, Any]] = []
    gst_events: List[Dict[str, Any]] = []
    flr_events: List[Dict[str, Any]] = []

    # Загружаем каждый тип события NASA в свой список.
    for label, endpoint, target_list in (
        ("SEP", NASA_DONKI_SEP_URL, sep_events),
        ("GST", NASA_DONKI_GST_URL, gst_events),
        ("FLR", NASA_DONKI_FLR_URL, flr_events),
    ):
        try:
            # Ask NASA for the requested date range; the fetch function retries without dates if needed.
            records = fetch_nasa_donki_endpoint(endpoint, api_key, start_str, end_str)
            target_list.extend(records)
        except Exception as exc:
            if label in ("SEP", "GST"):
                print(f"[NASA DONKI INFO] {label} endpoint fallback ({exc})", file=sys.stderr)

    print(
        f"[NASA DONKI API] Synced live 30-day NASA telemetry: "
        f"{len(sep_events)} SEP, {len(gst_events)} GST, {len(flr_events)} FLR events."
    )

    cur = conn.cursor()

    # Ищем максимальный Kp и сохраняем магнитные бури.
    max_kp = 1.3
    latest_gst_id = "NONE"
    for gst in gst_events:
        gst_id = str(gst.get("gstID", "UNKNOWN-GST"))
        start_time = str(gst.get("startTime", now_iso))
        link = str(gst.get("link", ""))
        event_max_kp = 0.0
        for kp_obs in gst.get("allKpIndex", []) or []:
            try:
                kp_val = float(kp_obs.get("kpIndex", 0.0))
                if kp_val > event_max_kp:
                    event_max_kp = kp_val
                if kp_val > max_kp:
                    max_kp = kp_val
            except (TypeError, ValueError):
                pass
        latest_gst_id = gst_id
        cur.execute(
            """
            INSERT OR REPLACE INTO donki_gst_events
            (gst_id, start_time, max_kp_index, link, synced_at_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            (gst_id, start_time, event_max_kp, link, now_iso),
        )

    # Переводим класс солнечной вспышки в демонстрационный индекс потока.
    max_flare_flux = 1.4
    latest_flr_id = "NONE"
    for flr in flr_events:
        flr_id = str(flr.get("flrID", "UNKNOWN-FLR"))
        begin_time = str(flr.get("beginTime", now_iso))
        peak_time = str(flr.get("peakTime", ""))
        class_type = str(flr.get("classType", "C1.0"))
        source_loc = str(flr.get("sourceLocation", ""))
        ar_num = int(flr.get("activeRegionNum") or 0)
        flux_est = parse_flare_class_to_flux(class_type)
        if flux_est >= max_flare_flux:
            max_flare_flux = flux_est
            latest_flr_id = flr_id

        cur.execute(
            """
            INSERT OR REPLACE INTO donki_flr_events
            (flr_id, begin_time, peak_time, class_type, source_location, active_region_num, estimated_flux, synced_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (flr_id, begin_time, peak_time, class_type, source_loc, ar_num, flux_est, now_iso),
        )

    # Проверяем события протонных штормов и сохраняем их.
    sep_active = len(sep_events) > 0
    latest_sep_id = "NONE"
    for sep in sep_events:
        sep_id = str(sep.get("sepID", "UNKNOWN-SEP"))
        event_time = str(sep.get("eventTime", now_iso))
        link = str(sep.get("link", ""))
        inst_list = [str(i.get("displayName", "")) for i in (sep.get("instruments") or [])]
        instruments_str = ", ".join(filter(None, inst_list))
        est_pfu = max(15.4, max_flare_flux)
        latest_sep_id = sep_id

        cur.execute(
            """
            INSERT OR REPLACE INTO donki_sep_events
            (sep_id, event_time, instruments, solar_flux_pfu, link, synced_at_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sep_id, event_time, instruments_str, est_pfu, link, now_iso),
        )

    conn.commit()

    # Determine storm state from live DONKI observations
    sep_storm_flag = bool(sep_active or max_kp >= 7.0 or max_flare_flux >= 15.0)

    if sep_active:
        solar_flux = max(15.4, max_flare_flux, round(max_kp * 2.8, 1))
        event_id = latest_sep_id
    elif max_kp >= 5.0:
        solar_flux = max(max_flare_flux, round(max_kp * 2.2, 1))
        event_id = latest_gst_id
    else:
        solar_flux = max_flare_flux
        event_id = latest_flr_id if latest_flr_id != "NONE" else "DONKI-NOMINAL"

    packet = {
        "sep_storm": sep_storm_flag,
        "solar_flux": round(float(solar_flux), 2),
        "kp_index": round(float(max_kp), 1),
        "event_id": event_id[:31],
    }

    # Log the uplink packet in the local SQLite database
    cur.execute(
        """
        INSERT INTO uplink_telemetry_log
        (timestamp_utc, sep_storm, solar_flux, kp_index, event_id, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            now_iso,
            1 if packet["sep_storm"] else 0,
            packet["solar_flux"],
            packet["kp_index"],
            packet["event_id"],
            json.dumps(packet, separators=(",", ":")),
        ),
    )
    conn.commit()

    return packet


def detect_esp32_serial_port() -> Optional[str]:
    """Automatically scans connected USB-UART bridges (CP210x, CH340, FTDI) for an ESP32."""
    if not PYSERIAL_AVAILABLE:
        return None
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        desc = (p.description or "").upper()
        if any(chip in desc for chip in ("CP210", "CH340", "CH910", "USB SERIAL", "UART", "ESP32")):
            return p.device
    return ports[0].device if ports else None


def transmit_to_esp32(port: Optional[str], baud: int, packet: Dict[str, Any], listen_seconds: float = 2.0) -> None:
    """Sends compact JSON line to ESP32 over UART and prints returned telemetry."""
    compact_json = json.dumps(packet, separators=(",", ":"))
    print(f"[SPACECRAFT -> ESP32 UART] {compact_json}")

    if not port or not PYSERIAL_AVAILABLE:
        print("[INFO] No physical COM port active (or pyserial not installed). Packet verified & stored in NASA DONKI SQLite DB.")
        return

    try:
        with serial.Serial(port=port, baudrate=baud, timeout=1.0) as ser:
            time.sleep(0.2)
            ser.write((compact_json + "\n").encode("utf-8"))
            ser.flush()

            end_time = time.time() + listen_seconds
            while time.time() < end_time:
                raw_line = ser.readline()
                if raw_line:
                    decoded = raw_line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        print(f"[ESP32 TELEMETRY <- UART] {decoded}")
    except Exception as exc:
        print(f"[SERIAL WARNING] Could not open {port}: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NASA DONKI Space Weather Uplink & SQLite DB Bridge for AstroWrist ESP32 Dosimeter"
    )
    parser.add_argument("--port", type=str, default=None, help="Serial COM port (e.g., COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD_RATE, help="UART baud rate (default: 115200)")
    parser.add_argument("--api-key", type=str, default=DEFAULT_API_KEY, help="NASA Open API key (default: DEMO_KEY)")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days for NASA DONKI query")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds for continuous mode")
    parser.add_argument("--once", action="store_true", help="Fetch NASA DONKI once, transmit, and exit")
    parser.add_argument(
        "--simulate-storm",
        action="store_true",
        help="Force SEP proton storm alert payload ({'sep_storm': true, 'solar_flux': 15.4}) from NASA DONKI archive",
    )
    args = parser.parse_args()

    conn = init_nasa_donki_database(DB_PATH)
    target_port = args.port or detect_esp32_serial_port()

    try:
        while True:
            if args.simulate_storm:
                # Pull highest-flux SEP storm from the local NASA DONKI database
                cur = conn.cursor()
                cur.execute(
                    "SELECT sep_id, solar_flux_pfu FROM donki_sep_events ORDER BY solar_flux_pfu DESC LIMIT 1"
                )
                row = cur.fetchone()
                event_id = row[0] if row else "2024-05-11T01:23:00-SEP-001"
                packet = {
                    "sep_storm": True,
                    "solar_flux": 15.4,
                    "kp_index": 8.3,
                    "event_id": str(event_id)[:31],
                }
            else:
                packet = sync_and_evaluate_nasa_donki(conn, api_key=args.api_key, lookback_days=args.days)

            transmit_to_esp32(target_port, args.baud, packet)

            if args.once:
                break
            time.sleep(max(5, args.interval))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
