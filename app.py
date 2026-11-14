"""
AstroHealth Spaceflight Biodosimetry & Biometric Telemetry Station
==================================================================
Web Application & Mission Control Cockpit for NASA Space Apps Challenge 2026
Challenge 6: Create Health Monitoring Software for Astronauts on Space Missions

Runs 100% in software without requiring physical sensors or ESP32 hardware,
integrating live NASA DONKI Space Weather API and NASA-STD-3001 health standards.
"""

import asyncio
import datetime as dt
import json
import math
import os
import random
import sqlite3
import sys
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# App Paths
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "nasa_uplink" / "nasa_donki_events.db"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="AstroHealth Spaceflight Medical Monitor",
    description="Software Telemetry Station for Astronaut Ionizing Radiation, Cardiovascular Vitals & NASA Space Weather",
    version="2.0.0"
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ============================================================================
# NASA-STD-3001 STANDARDS & CONSTANTS
# ============================================================================
NASA_30DAY_BFO_LIMIT_MSV = 250.0   # Blood Forming Organs 30-day permissible limit
NASA_CAREER_LIMIT_MSV = 600.0      # Astronaut career effective dose limit
J305_CONVERSION_FACTOR = 0.00812   # CPM to uSv/h (Cs-137 calibration)
J305_DEAD_TIME_US = 190.0          # GM tube recovery time in microseconds
DEAD_TIME_MINUTES = (J305_DEAD_TIME_US * 1e-6) / 60.0

# Thresholds
EVA_PRESSURE_THRESHOLD_HPA = 450.0  # Transition below 450 hPa indicates EVA suit
RAD_WARN_MODULE_USVH = 15.0
RAD_CRIT_MODULE_USVH = 100.0
RAD_WARN_EVA_USVH = 8.0
RAD_CRIT_EVA_USVH = 50.0


class AstronautState:
    """Simulated state engine providing realistic human biometrics and space dosimetry."""

    def __init__(self):
        self.scenario = "nominal"
        self.met_seconds = 0
        self.operating_mode = "MODULE"  # "MODULE" or "EVA"
        self.crew_status = "NOMINAL"    # "NOMINAL", "BIOMETRIC_STRESS", "RADIATION_WARNING", "CRITICAL_ABORT"

        # Vitals
        self.heart_rate_bpm = 72.0
        self.systolic_bp_mmhg = 118.0
        self.diastolic_bp_mmhg = 76.0
        self.mean_arterial_pressure = 90.0
        self.spo2_percent = 98.4
        self.respiration_brpm = 14.0
        self.suit_pressure_hpa = 1013.2
        self.suit_temp_c = 21.8

        # Radiation Dosimetry
        self.raw_cpm = 18.0
        self.true_cpm = 18.0
        self.dose_rate_usvh = 0.146
        self.dose_rate_msvh = 0.000146
        self.accumulated_dose_msv = 1.4280
        self.time_to_limit_hours = 9999.9

        # Space Weather Uplink (NASA DONKI)
        self.sep_storm_active = False
        self.solar_flux_pfu = 1.2
        self.kp_index = 2.0
        self.solar_flare_class = "C2.1"
        self.donki_last_sync_utc = "Synchronizing..."
        self.active_donki_event = "No Active Solar Storms"

        # Simulation tick state
        self.step_counter = 0

    def apply_scenario(self, name: str):
        self.scenario = name
        if name == "nominal":
            self.suit_pressure_hpa = 1013.25
            self.operating_mode = "MODULE"
            self.sep_storm_active = False
            self.solar_flux_pfu = 1.2
            self.raw_cpm = 18.0
            self.heart_rate_bpm = 72.0
            self.systolic_bp_mmhg = 118.0
            self.diastolic_bp_mmhg = 76.0
            self.spo2_percent = 98.5
            self.active_donki_event = "Solar Cycle 25: Quiet Magnetosphere"

        elif name == "eva_spacewalk":
            self.suit_pressure_hpa = 296.0  # 4.3 psi NASA xEMU suit pressure
            self.operating_mode = "EVA"
            self.sep_storm_active = False
            self.solar_flux_pfu = 3.5
            self.raw_cpm = 45.0
            self.heart_rate_bpm = 96.0
            self.systolic_bp_mmhg = 126.0
            self.diastolic_bp_mmhg = 82.0
            self.spo2_percent = 97.2
            self.active_donki_event = "EVA Routine Operations - Low LEO Background"

        elif name == "exertion_tachycardia":
            self.suit_pressure_hpa = 296.0
            self.operating_mode = "EVA"
            self.sep_storm_active = False
            self.solar_flux_pfu = 4.0
            self.raw_cpm = 65.0
            self.heart_rate_bpm = 142.0  # Tachycardia threshold exceeded (>130 BPM)
            self.systolic_bp_mmhg = 152.0
            self.diastolic_bp_mmhg = 96.0
            self.spo2_percent = 94.0
            self.active_donki_event = "Physical Strain / High Metabolic Rate detected during EVA"

        elif name == "solar_proton_storm":
            self.suit_pressure_hpa = 1013.25
            self.operating_mode = "MODULE"
            self.sep_storm_active = True
            self.solar_flux_pfu = 24.8  # Strong Solar Proton Event
            self.kp_index = 8.3
            self.solar_flare_class = "X9.0"
            self.raw_cpm = 2400.0  # High radiation flux
            self.heart_rate_bpm = 108.0
            self.systolic_bp_mmhg = 132.0
            self.diastolic_bp_mmhg = 84.0
            self.spo2_percent = 98.0
            self.active_donki_event = "NASA DONKI Alert: Severe Solar Energetic Particle Event"

        elif name == "critical_abort":
            self.suit_pressure_hpa = 296.0  # Unshielded in EVA
            self.operating_mode = "EVA"
            self.sep_storm_active = True
            self.solar_flux_pfu = 65.4
            self.raw_cpm = 8600.0  # Critical radiation rate
            self.heart_rate_bpm = 155.0  # Extreme tachycardia under acute radiation
            self.systolic_bp_mmhg = 168.0
            self.diastolic_bp_mmhg = 104.0
            self.spo2_percent = 89.0  # Hypoxia
            self.active_donki_event = "CRITICAL: Coronal Mass Ejection + High Particle Flux detected"

    def tick(self):
        """Advances state by 1 second with realistic physiological jitter and radiation integration."""
        self.met_seconds += 1
        self.step_counter += 1

        # Small physiological natural variability
        noise_bpm = random.uniform(-1.2, 1.2)
        noise_cpm = random.uniform(-2.0, 2.0)
        noise_sys = random.uniform(-1.0, 1.0)
        noise_dia = random.uniform(-0.8, 0.8)

        # Baseline per scenario
        if self.scenario == "nominal":
            target_bpm = 72.0
            target_cpm = 22.0
            target_sys, target_dia = 118.0, 76.0
            self.suit_pressure_hpa = 1013.25 + random.uniform(-0.5, 0.5)
        elif self.scenario == "eva_spacewalk":
            target_bpm = 95.0
            target_cpm = 65.0
            target_sys, target_dia = 128.0, 82.0
            self.suit_pressure_hpa = 296.0 + random.uniform(-0.3, 0.3)
        elif self.scenario == "exertion_tachycardia":
            target_bpm = 138.0
            target_cpm = 85.0
            target_sys, target_dia = 152.0, 96.0
            self.suit_pressure_hpa = 296.0 + random.uniform(-0.3, 0.3)
        elif self.scenario == "solar_proton_storm":
            target_bpm = 105.0
            target_cpm = 2650.0
            target_sys, target_dia = 134.0, 86.0
            self.suit_pressure_hpa = 1013.25 + random.uniform(-0.5, 0.5)
        elif self.scenario == "critical_abort":
            target_bpm = 156.0
            target_cpm = 8900.0
            target_sys, target_dia = 168.0, 104.0
            self.suit_pressure_hpa = 296.0 + random.uniform(-0.3, 0.3)
        else:
            target_bpm = 72.0
            target_cpm = 20.0
            target_sys, target_dia = 120.0, 80.0

        # Smooth relaxation towards targets
        self.heart_rate_bpm = max(40.0, min(210.0, 0.9 * self.heart_rate_bpm + 0.1 * target_bpm + noise_bpm))
        self.raw_cpm = max(5.0, 0.85 * self.raw_cpm + 0.15 * target_cpm + noise_cpm)
        self.systolic_bp_mmhg = max(70.0, min(220.0, 0.9 * self.systolic_bp_mmhg + 0.1 * target_sys + noise_sys))
        self.diastolic_bp_mmhg = max(40.0, min(140.0, 0.9 * self.diastolic_bp_mmhg + 0.1 * target_dia + noise_dia))
        self.mean_arterial_pressure = self.diastolic_bp_mmhg + (self.systolic_bp_mmhg - self.diastolic_bp_mmhg) / 3.0

        # Geiger-Müller Non-Paralyzable Dead-Time Physics Correction
        # true_cpm = raw_cpm / (1 - raw_cpm * dead_time_min)
        denom = 1.0 - (self.raw_cpm * DEAD_TIME_MINUTES)
        if denom > 0.05:
            self.true_cpm = self.raw_cpm / denom
        else:
            self.true_cpm = self.raw_cpm * 1.5

        # Dose rate calculation
        self.dose_rate_usvh = self.true_cpm * J305_CONVERSION_FACTOR
        self.dose_rate_msvh = self.dose_rate_usvh / 1000.0

        # Numerical integration of accumulated dose (1 second = 1/3600 hours)
        self.accumulated_dose_msv += self.dose_rate_msvh * (1.0 / 3600.0)

        # Time-to-limit (hours remaining until 250 mSv 30-day NASA limit)
        remaining_msv = NASA_30DAY_BFO_LIMIT_MSV - self.accumulated_dose_msv
        if remaining_msv <= 0.0:
            self.time_to_limit_hours = 0.0
        elif self.dose_rate_msvh > 1e-7:
            self.time_to_limit_hours = min(9999.9, remaining_msv / self.dose_rate_msvh)
        else:
            self.time_to_limit_hours = 9999.9

        # Mode determination from barometric pressure
        if self.suit_pressure_hpa < EVA_PRESSURE_THRESHOLD_HPA:
            self.operating_mode = "EVA"
            warn_threshold = RAD_WARN_EVA_USVH
            crit_threshold = RAD_CRIT_EVA_USVH
        else:
            self.operating_mode = "MODULE"
            warn_threshold = RAD_WARN_MODULE_USVH
            crit_threshold = RAD_CRIT_MODULE_USVH

        # Multi-parameter Biodosimetry State Decision
        is_tachycardia = self.heart_rate_bpm > 130.0
        is_elevated_rad = self.dose_rate_usvh >= warn_threshold
        is_critical_rad = self.dose_rate_usvh >= crit_threshold
        is_near_30d = self.accumulated_dose_msv >= (NASA_30DAY_BFO_LIMIT_MSV * 0.90)

        if is_critical_rad or (is_tachycardia and is_elevated_rad) or is_near_30d:
            self.crew_status = "CRITICAL_ABORT"
        elif is_tachycardia or self.systolic_bp_mmhg > 145.0 or self.spo2_percent < 92.0:
            self.crew_status = "BIOMETRIC_STRESS"
        elif is_elevated_rad or self.sep_storm_active:
            self.crew_status = "RADIATION_WARNING"
        else:
            self.crew_status = "NOMINAL"

    def get_telemetry_payload(self) -> Dict[str, Any]:
        hours = self.met_seconds // 3600
        mins = (self.met_seconds % 3600) // 60
        secs = self.met_seconds % 60
        met_formatted = f"+{hours:02d}:{mins:02d}:{secs:02d}"

        pct_30d = min(100.0, (self.accumulated_dose_msv / NASA_30DAY_BFO_LIMIT_MSV) * 100.0)
        pct_career = min(100.0, (self.accumulated_dose_msv / NASA_CAREER_LIMIT_MSV) * 100.0)

        warn_limit = RAD_WARN_EVA_USVH if self.operating_mode == "EVA" else RAD_WARN_MODULE_USVH
        crit_limit = RAD_CRIT_EVA_USVH if self.operating_mode == "EVA" else RAD_CRIT_MODULE_USVH

        return {
            "met": met_formatted,
            "met_seconds": self.met_seconds,
            "scenario": self.scenario,
            "operating_mode": self.operating_mode,
            "crew_status": self.crew_status,
            "vitals": {
                "heart_rate_bpm": round(self.heart_rate_bpm, 1),
                "systolic_bp": round(self.systolic_bp_mmhg, 1),
                "diastolic_bp": round(self.diastolic_bp_mmhg, 1),
                "map_bp": round(self.mean_arterial_pressure, 1),
                "spo2_percent": round(self.spo2_percent, 1),
                "respiration_brpm": round(self.respiration_brpm, 1),
                "suit_pressure_hpa": round(self.suit_pressure_hpa, 1),
                "suit_pressure_psi": round(self.suit_pressure_hpa * 0.0145038, 2),
                "suit_temp_c": round(self.suit_temp_c, 1),
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
            },
            "nasa_donki": {
                "sep_storm": self.sep_storm_active,
                "solar_flux_pfu": round(self.solar_flux_pfu, 1),
                "kp_index": round(self.kp_index, 1),
                "flare_class": self.solar_flare_class,
                "event_headline": self.active_donki_event,
                "last_sync": self.donki_last_sync_utc,
            }
        }


# Singleton State Instance
sim_state = AstronautState()


def sync_nasa_donki_data():
    """Fetches latest space weather telemetry from official NASA DONKI API or local SQLite archive."""
    global sim_state
    api_key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    try:
        url = f"https://api.nasa.gov/DONKI/SEP?api_key={api_key}"
        resp = requests.get(url, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                sim_state.active_donki_event = f"NASA DONKI SEP: {latest.get('sepID', 'Active Storm')}"
            else:
                sim_state.active_donki_event = "NASA DONKI: All clear (Quiet solar activity)"
            sim_state.donki_last_sync_utc = now_iso
            return
    except Exception:
        pass

    # Fallback to local verified NASA Solar Cycle 25 SQLite DB
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.cursor()
            cur.execute("SELECT sep_id, instruments, solar_flux_pfu FROM donki_sep_events ORDER BY rowid DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                sim_state.active_donki_event = f"NASA Reference SC25: {row[0]} ({row[1]})"
                sim_state.solar_flux_pfu = float(row[2])
            conn.close()
            sim_state.donki_last_sync_utc = f"{now_iso} (Verified NASA DB Archive)"
            return
        except Exception:
            pass

    sim_state.donki_last_sync_utc = f"{now_iso} (NASA-STD-3001 Baseline)"


# Background Periodic Simulation Task
async def simulation_loop():
    sync_nasa_donki_data()
    while True:
        sim_state.tick()
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(simulation_loop())


# Web Routes
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/telemetry")
async def get_telemetry():
    return JSONResponse(sim_state.get_telemetry_payload())


@app.post("/api/scenario/{scenario_name}")
async def set_scenario(scenario_name: str):
    valid = ["nominal", "eva_spacewalk", "exertion_tachycardia", "solar_proton_storm", "critical_abort"]
    if scenario_name in valid:
        sim_state.apply_scenario(scenario_name)
        return {"status": "success", "scenario": scenario_name}
    return JSONResponse({"status": "error", "message": "Unknown scenario"}, status_code=400)


@app.post("/api/reset_dose")
async def reset_dose():
    sim_state.accumulated_dose_msv = 0.0
    return {"status": "success", "accumulated_dose_msv": 0.0}


@app.get("/api/donki_sync")
async def trigger_donki_sync():
    sync_nasa_donki_data()
    return {"status": "success", "sync_time": sim_state.donki_last_sync_utc, "event": sim_state.active_donki_event}


if __name__ == "__main__":
    port = 8000
    print(f"\n=======================================================")
    print(f"🚀 AstroHealth Spaceflight Medical Station Launching...")
    print(f"📡 Web Application available at: http://127.0.0.1:{port}")
    print(f"🌌 Integrated with NASA DONKI API & NASA-STD-3001 Standards")
    print(f"=======================================================\n")
    try:
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
