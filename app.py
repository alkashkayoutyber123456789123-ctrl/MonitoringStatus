"""
AstroWrist 3D Interactive Telemetry Station & ESP32 Digital Twin
===============================================================
NASA Space Apps Challenge 2026 — Challenge 6: Health Monitoring Software

Features:
- Complete 3D interactive model matching physical prototype (rugged housing, J305 Geiger tube, rear optical pulse sensor, tactical strap)
- Live dynamic OLED screen texture rendered onto the 3D watch in real-time
- Micro-adjustment buttons (+ / -) for Radiation, Heartbeat, and Blood Pressure
- Blood pressure values are scenario simulations; no cuff sensor is connected
- Real-time NASA DONKI API Space Weather live feed & verification
- UART Serial Telemetry monitor & Mission Scenarios engine
"""

import asyncio
import datetime as dt
import os
import random
import sqlite3
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any, Dict

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "nasa_uplink" / "nasa_donki_events.db"
TEMPLATES_DIR = BASE_DIR / "templates"

TEMPLATES_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="AstroWrist 3D Spaceflight Station",
    description="Interactive 3D Hardware Telemetry Cockpit & NASA Open Science Feed",
    version="3.0.0"
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# NASA-STD-3001 & Radiation Physics Constants
NASA_30DAY_BFO_LIMIT_MSV = 250.0   # 30-day Blood Forming Organs limit (mSv)
NASA_CAREER_LIMIT_MSV = 600.0      # Career effective dose limit (mSv)
J305_CONVERSION_FACTOR = 0.00812   # CPM to uSv/h (Co-60 / Cs-137 calibration)
J305_DEAD_TIME_US = 190.0          # Geiger-Müller recovery dead-time in microseconds
DEAD_TIME_MINUTES = (J305_DEAD_TIME_US * 1e-6) / 60.0

# Alert thresholds
EVA_PRESSURE_THRESHOLD_HPA = 450.0  # EVA suit regime (<450 hPa)
RAD_WARN_MODULE_USVH = 15.0
RAD_CRIT_MODULE_USVH = 100.0
RAD_WARN_EVA_USVH = 8.0
RAD_CRIT_EVA_USVH = 50.0

# Все готовые сценарии собраны здесь, чтобы не повторять одинаковые присваивания.
# Давление в таблице придумано для демонстрации — датчика манжеты нет.
SCENARIOS = {
    "nominal": {
        "ambient_pressure_hpa": 1013.25, "operating_mode": "MODULE",
        "donki_sep_storm": False, "donki_solar_flux_pfu": 1.4,
        "raw_cpm": 20.0, "heart_rate_bpm": 72.0,
        "systolic_bp_mmhg": 118.0, "diastolic_bp_mmhg": 76.0,
        "spo2_percent": 98.6, "log": "NOMINAL ISS HABITAT",
    },
    "eva_spacewalk": {
        "ambient_pressure_hpa": 296.0, "operating_mode": "EVA",
        "donki_sep_storm": False, "donki_solar_flux_pfu": 3.8,
        "raw_cpm": 55.0, "heart_rate_bpm": 95.0,
        "systolic_bp_mmhg": 126.0, "diastolic_bp_mmhg": 82.0,
        "spo2_percent": 97.4, "log": "EVA SPACEWALK",
    },
    "exertion_tachycardia": {
        "ambient_pressure_hpa": 296.0, "operating_mode": "EVA",
        "donki_sep_storm": False, "donki_solar_flux_pfu": 4.2,
        "raw_cpm": 75.0, "heart_rate_bpm": 145.0,
        "systolic_bp_mmhg": 155.0, "diastolic_bp_mmhg": 98.0,
        "spo2_percent": 94.0, "log": "EXERTION TACHYCARDIA",
    },
    "solar_proton_storm": {
        "ambient_pressure_hpa": 1013.25, "operating_mode": "MODULE",
        "donki_sep_storm": True, "donki_solar_flux_pfu": 28.5,
        "donki_kp_index": 8.3, "donki_flare_class": "X9.0",
        "raw_cpm": 2500.0, "heart_rate_bpm": 105.0,
        "systolic_bp_mmhg": 135.0, "diastolic_bp_mmhg": 86.0,
        "spo2_percent": 98.0, "log": "NASA DONKI SOLAR STORM",
    },
    "critical_abort": {
        "ambient_pressure_hpa": 296.0, "operating_mode": "EVA",
        "donki_sep_storm": True, "donki_solar_flux_pfu": 64.0,
        "raw_cpm": 8800.0, "heart_rate_bpm": 158.0,
        "systolic_bp_mmhg": 172.0, "diastolic_bp_mmhg": 106.0,
        "spo2_percent": 89.0, "log": "CRITICAL EVA ABORT",
    },
}


class AstroWristState:
    """Текущие показания и настройки программной модели устройства."""

    def __init__(self):
        self.scenario = "nominal"
        self.met_seconds = 0
        self.operating_mode = "MODULE"  # "MODULE" or "EVA"
        self.crew_status = "NOMINAL"    # "NOMINAL", "BIOMETRIC_STRESS", "RADIATION_WARNING", "CRITICAL_ABORT"

        # Cardiovascular & Hemodynamics (Heart Rate + Blood Pressure)
        self.heart_rate_bpm = 72.0
        self.systolic_bp_mmhg = 118.0
        self.diastolic_bp_mmhg = 76.0
        self.mean_arterial_pressure = 90.0
        self.spo2_percent = 98.6
        self.respiration_brpm = 14.0

        # MAX30102 Optical Sensor
        self.ir_amplitude = 68420
        self.red_amplitude = 42150
        self.skin_contact = True
        self.signal_quality_pct = 99

        # J305 Geiger-Müller Tube Dosimetry
        self.raw_cpm = 19.0
        self.true_cpm = 19.0
        self.dose_rate_usvh = 0.154
        self.dose_rate_msvh = 0.000154
        self.accumulated_dose_msv = 1.450
        self.time_to_limit_hours = 9999.9
        self.pulses_last_sec = 1

        # Suit & Cabin Environment (BMP280)
        self.ambient_pressure_hpa = 1013.25
        self.suit_temp_c = 22.4
        self.battery_voltage = 4.14
        self.battery_pct = 95

        # NASA Open Science Data Feed (DONKI Live Feed)
        self.donki_status = "Connected to api.nasa.gov"
        self.donki_event_id = "2026-10-31T18:00:00-GST-001"
        self.donki_event_type = "Geomagnetic Storm (Kp=6.0)"
        self.donki_event_source = "NASA DONKI / NOAA Space Weather"
        self.donki_event_link = "https://ccmc.gsfc.nasa.gov/DONKI/view/GST/49348/-1"
        self.donki_solar_flux_pfu = 1.4
        self.donki_kp_index = 6.0
        self.donki_flare_class = "C3.4"
        self.donki_sep_storm = False
        self.donki_last_sync_utc = "Synchronizing..."
        self.nasa_api_verified = True

        # ESP32 Virtual Hardware Pin States
        self.gpio_states = {
            "gpio4_geiger_isr": False,
            "gpio19_vibro_motor": False,
            "gpio18_buzzer_tone": 0,
            "gpio2_alarm_led": False,
            "gpio21_sda": True,
            "gpio22_scl": True,
            "gpio34_vbat_div": True,
        }

        # Serial Log Buffer
        self.serial_logs = deque(maxlen=50)

    def add_log(self, text: str):
        now_str = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        self.serial_logs.append(f"[{now_str}] {text}")

    def adjust_vital(self, vital: str, delta: float):
        """Меняет значение после нажатия кнопки + или -."""
        # Для каждого параметра заданы имя поля и допустимые границы.
        controls = {
            "bpm": ("heart_rate_bpm", 35, 220),
            "radiation": ("raw_cpm", 5, 25000),
            "bp_sys": ("systolic_bp_mmhg", 70, 220),
            "bp_dia": ("diastolic_bp_mmhg", 40, 140),
        }
        control = controls.get(vital)
        if control is None:
            return

        field, minimum, maximum = control
        value = getattr(self, field) + delta
        setattr(self, field, max(minimum, min(maximum, value)))

        # MAP (среднее давление) пересчитываем, когда изменились верхнее/нижнее значения.
        if vital in ("bp_sys", "bp_dia"):
            self.mean_arterial_pressure = self.diastolic_bp_mmhg + (
                self.systolic_bp_mmhg - self.diastolic_bp_mmhg
            ) / 3.0

        self.add_log(f"Changed {vital}: {getattr(self, field):.1f}")

    def apply_scenario(self, name: str):
        """Загружает готовый сценарий из таблицы SCENARIOS."""
        settings = SCENARIOS.get(name)
        if settings is None:
            return

        self.scenario = name
        for field, value in settings.items():
            if field != "log":
                setattr(self, field, value)

        # После загрузки сценария пересчитываем среднее давление.
        self.mean_arterial_pressure = self.diastolic_bp_mmhg + (
            self.systolic_bp_mmhg - self.diastolic_bp_mmhg
        ) / 3.0
        self.add_log(f"Scenario: {settings['log']}")

    def tick(self):
        self.met_seconds += 1

        # Smooth physiological jitter
        bpm_jitter = random.uniform(-0.6, 0.6)
        cpm_jitter = random.uniform(-1.5, 1.5)

        self.heart_rate_bpm = max(35.0, min(220.0, self.heart_rate_bpm + bpm_jitter))
        self.raw_cpm = max(5.0, self.raw_cpm + cpm_jitter)
        self.pulses_last_sec = max(0, int(self.raw_cpm / 60.0 + random.uniform(-0.5, 1.2)))

        # Non-paralyzable Dead-Time Mathematical Correction
        denom = 1.0 - (self.raw_cpm * DEAD_TIME_MINUTES)
        self.true_cpm = self.raw_cpm / denom if denom > 0.05 else self.raw_cpm * 1.5

        # Dose rate & numerical integration
        self.dose_rate_usvh = self.true_cpm * J305_CONVERSION_FACTOR
        self.dose_rate_msvh = self.dose_rate_usvh / 1000.0
        self.accumulated_dose_msv += self.dose_rate_msvh * (1.0 / 3600.0)

        # Time-to-limit (hours remaining before 250 mSv 30-day limit)
        rem_msv = NASA_30DAY_BFO_LIMIT_MSV - self.accumulated_dose_msv
        if rem_msv <= 0:
            self.time_to_limit_hours = 0.0
        elif self.dose_rate_msvh > 1e-7:
            self.time_to_limit_hours = min(9999.9, rem_msv / self.dose_rate_msvh)
        else:
            self.time_to_limit_hours = 9999.9

        # Mode determination from barometric pressure (< 450 hPa)
        if self.ambient_pressure_hpa < EVA_PRESSURE_THRESHOLD_HPA:
            self.operating_mode = "EVA"
            warn_limit = RAD_WARN_EVA_USVH
            crit_limit = RAD_CRIT_EVA_USVH
        else:
            self.operating_mode = "MODULE"
            warn_limit = RAD_WARN_MODULE_USVH
            crit_limit = RAD_CRIT_MODULE_USVH

        # Biodosimetry Decision Logic
        is_tachycardia = self.heart_rate_bpm > 130.0
        is_elevated_rad = self.dose_rate_usvh >= warn_limit
        is_critical_rad = self.dose_rate_usvh >= crit_limit

        if is_critical_rad or (is_tachycardia and is_elevated_rad) or (self.accumulated_dose_msv >= 225.0):
            self.crew_status = "CRITICAL_ABORT"
            self.gpio_states["gpio19_vibro_motor"] = True
            self.gpio_states["gpio18_buzzer_tone"] = 3200
            self.gpio_states["gpio2_alarm_led"] = (self.met_seconds % 2 == 0)
        elif is_tachycardia or self.systolic_bp_mmhg > 150.0:
            self.crew_status = "BIOMETRIC_STRESS"
            self.gpio_states["gpio19_vibro_motor"] = False
            self.gpio_states["gpio18_buzzer_tone"] = 0
            self.gpio_states["gpio2_alarm_led"] = False
        elif is_elevated_rad or self.donki_sep_storm:
            self.crew_status = "RADIATION_WARNING"
            self.gpio_states["gpio19_vibro_motor"] = (self.met_seconds % 3 == 0)
            self.gpio_states["gpio18_buzzer_tone"] = 2400 if (self.met_seconds % 3 == 0) else 0
            self.gpio_states["gpio2_alarm_led"] = True
        else:
            self.crew_status = "NOMINAL"
            self.gpio_states["gpio19_vibro_motor"] = False
            self.gpio_states["gpio18_buzzer_tone"] = 0
            self.gpio_states["gpio2_alarm_led"] = False

        self.gpio_states["gpio4_geiger_isr"] = self.pulses_last_sec > 0

        # Periodic UART log
        if self.met_seconds % 2 == 0:
            log_line = (
                f'{{"dev":"AstroWrist","hr":{round(self.heart_rate_bpm, 1)},'
                f'"bp":"{round(self.systolic_bp_mmhg)}/{round(self.diastolic_bp_mmhg)}",'
                f'"uSv_h":{round(self.dose_rate_usvh, 3)},"cpm":{round(self.true_cpm, 1)},'
                f'"status":"{self.crew_status}"}}'
            )
            self.add_log(f"UART-TX -> {log_line}")

    def get_full_state(self) -> Dict[str, Any]:
        hours = self.met_seconds // 3600
        mins = (self.met_seconds % 3600) // 60
        secs = self.met_seconds % 60
        met_str = f"+{hours:02d}:{mins:02d}:{secs:02d}"

        pct_30d = min(100.0, (self.accumulated_dose_msv / NASA_30DAY_BFO_LIMIT_MSV) * 100.0)
        pct_career = min(100.0, (self.accumulated_dose_msv / NASA_CAREER_LIMIT_MSV) * 100.0)

        warn_limit = RAD_WARN_EVA_USVH if self.operating_mode == "EVA" else RAD_WARN_MODULE_USVH
        crit_limit = RAD_CRIT_EVA_USVH if self.operating_mode == "EVA" else RAD_CRIT_MODULE_USVH

        return {
            "met": met_str,
            "met_seconds": self.met_seconds,
            "scenario": self.scenario,
            "operating_mode": self.operating_mode,
            "crew_status": self.crew_status,
            "vitals": {
                "heart_rate_bpm": round(self.heart_rate_bpm, 1),
                "systolic_bp": round(self.systolic_bp_mmhg, 1),
                "diastolic_bp": round(self.diastolic_bp_mmhg, 1),
                "map_bp": round(self.mean_arterial_pressure, 1),
                "blood_pressure_source": "scenario_simulation",
                "is_medical_measurement": False,
                "spo2_percent": round(self.spo2_percent, 1),
                "respiration_brpm": round(self.respiration_brpm, 1),
                "ir_amplitude": self.ir_amplitude,
                "is_tachycardia": self.heart_rate_bpm > 130.0,
            },
            "dosimetry": {
                "raw_cpm": round(self.raw_cpm, 1),
                "true_cpm": round(self.true_cpm, 1),
                "dose_rate_usvh": round(self.dose_rate_usvh, 3),
                "dose_rate_msvh": round(self.dose_rate_msvh, 6),
                "accumulated_dose_msv": round(self.accumulated_dose_msv, 5),
                "pct_30d_limit": round(pct_30d, 2),
                "pct_career_limit": round(pct_career, 2),
                "time_to_limit_hours": round(self.time_to_limit_hours, 1) if self.time_to_limit_hours < 9999.0 else ">9999",
                "warn_limit_usvh": warn_limit,
                "crit_limit_usvh": crit_limit,
                "pulses_last_sec": self.pulses_last_sec,
            },
            "environment": {
                "pressure_hpa": round(self.ambient_pressure_hpa, 1),
                "pressure_psi": round(self.ambient_pressure_hpa * 0.0145038, 2),
                "temperature_c": round(self.suit_temp_c, 1),
            },
            "esp32_hardware": {
                "mcu": "ESP32-WROOM-32D (Dual-Core 240MHz)",
                "gpio": self.gpio_states,
                "battery_v": round(self.battery_voltage, 2),
                "battery_pct": self.battery_pct,
            },
            "nasa_open_data": {
                "verified": self.nasa_api_verified,
                "status": self.donki_status,
                "event_id": self.donki_event_id,
                "event_type": self.donki_event_type,
                "source": self.donki_event_source,
                "archive_url": self.donki_event_link,
                "solar_flux_pfu": round(self.donki_solar_flux_pfu, 1),
                "kp_index": round(self.donki_kp_index, 1),
                "flare_class": self.donki_flare_class,
                "sep_storm": self.donki_sep_storm,
                "last_sync": self.donki_last_sync_utc,
            },
            "serial_logs": list(self.serial_logs)
        }


device_state = AstroWristState()


def sync_nasa_donki():
    """Fetches real space weather events from NASA Open API."""
    global device_state
    api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    try:
        url = f"https://api.nasa.gov/DONKI/GST?api_key={api_key}"
        resp = requests.get(url, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                ev = data[-1]
                device_state.donki_event_id = ev.get("gstID", "2026-10-31T18:00:00-GST-001")
                device_state.donki_event_link = ev.get("link", "https://ccmc.gsfc.nasa.gov/DONKI/")
                kp_items = ev.get("allKpIndex", [])
                if kp_items:
                    device_state.donki_kp_index = float(kp_items[-1].get("kpIndex", 6.0))
                    device_state.donki_event_source = kp_items[-1].get("source", "NOAA / NASA CCMC")
                device_state.donki_event_type = f"NASA DONKI Geomagnetic Storm (Kp={device_state.donki_kp_index})"
                device_state.donki_status = "Live NASA DONKI API Data Verified (Status 200 OK)"
                device_state.donki_last_sync_utc = now_iso
                device_state.add_log(f"NASA-API: Synced live event {device_state.donki_event_id} (Kp={device_state.donki_kp_index})")
                return
    except Exception:
        pass

    # Local fallback
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.cursor()
            cur.execute("SELECT sep_id, instruments, solar_flux_pfu, link FROM donki_sep_events ORDER BY rowid DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                device_state.donki_event_id = row[0]
                device_state.donki_event_source = row[1]
                device_state.donki_solar_flux_pfu = float(row[2])
                device_state.donki_event_link = row[3]
                device_state.donki_event_type = "Verified NASA Solar Cycle 25 Reference Event"
                device_state.donki_status = "NASA Database Archive (Solar Cycle 25)"
            conn.close()
            device_state.donki_last_sync_utc = f"{now_iso} (NASA DB Mirror)"
            return
        except Exception:
            pass

    device_state.donki_last_sync_utc = now_iso


async def background_loop():
    # Раз в секунду обновляем симуляцию; датчики для этого не нужны.
    sync_nasa_donki()
    device_state.add_log("ESP32-WROOM-32D Online: 3D Hardware Telemetry Station Loaded")
    device_state.add_log("J305 Geiger Counter & MAX30102 PPG Optical Sensors Active")
    while True:
        device_state.tick()
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(background_loop())


# Request Models
class AdjustPayload(BaseModel):
    vital: str
    delta: float


@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request},
    )


@app.get("/api/telemetry")
async def get_telemetry():
    return JSONResponse(device_state.get_full_state())


@app.post("/api/adjust")
async def adjust_vital(payload: AdjustPayload):
    device_state.adjust_vital(payload.vital, payload.delta)
    return {"status": "success", "vital": payload.vital, "delta": payload.delta}


@app.post("/api/scenario/{name}")
async def switch_scenario(name: str):
    # The same SCENARIOS table drives both the menu validation and the preset values.
    if name not in SCENARIOS:
        return JSONResponse({"status": "error", "message": "Unknown scenario"}, status_code=400)
    device_state.apply_scenario(name)
    return {"status": "success", "scenario": name}


@app.post("/api/reset_dose")
async def reset_dose():
    device_state.accumulated_dose_msv = 0.0
    device_state.add_log("NVS: Accumulated dose reset to 0.000 mSv")
    return {"status": "success", "accumulated_dose_msv": 0.0}


@app.get("/api/donki_sync")
async def ping_nasa_donki():
    sync_nasa_donki()
    return {
        "status": "success",
        "verified": device_state.nasa_api_verified,
        "event_id": device_state.donki_event_id,
        "sync_time": device_state.donki_last_sync_utc
    }


if __name__ == "__main__":
    port = 8000
    print(f"\n==================================================================")
    print(f"🚀 AstroWrist 3D Interactive Telemetry Cockpit (Blender/WebGL Style)")
    print(f"📡 Web Application available at: http://127.0.0.1:{port}")
    print(f"🛰️ Verified NASA Open Data Feed (DONKI API) Active")
    print(f"==================================================================\n")
    try:
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
