# AstroHealth: Astronaut Biodosimetry & Biometric Telemetry Station

**Spaceflight Health Monitoring Software, Ionizing Radiation Dosimeter, Barometric EVA Detector & Hemodynamic Monitor**  
*Built for NASA International Space Apps Challenge 2026 — Challenge 6: Create Health Monitoring Software for Astronauts on Space Missions*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![NASA Space Apps](https://img.shields.io/badge/NASA_Space_Apps-2026-orange.svg)](https://www.spaceappschallenge.org/)
[![NASA-STD-3001](https://img.shields.io/badge/Compliance-NASA--STD--3001-green.svg)](https://www.nasa.gov/ochmo/health-and-medical-policy-and-standards/nasa-std-3001-technical-briefs/)
[![NASA DONKI](https://img.shields.io/badge/Data_Feed-NASA_DONKI_API-red.svg)](https://api.nasa.gov/)

---

## 🚀 Quick Start for Judges (No Hardware Required)

You do **NOT** need any physical microcontrollers, sensors, or breadboards to run and evaluate this project. The system includes an interactive Mission Control Medical Telemetry Web Application (`app.py`):

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Launch the AstroHealth Web Telemetry Dashboard
python app.py
```
> The dashboard will automatically open in your default browser at **`http://127.0.0.1:8000`**.

> **Blood-pressure values are simulated scenario data.** This software prototype has no connected cuff or blood-pressure sensor; the values are not measured from a person and are not for medical use.

### Interactive Features in the Software Prototype:
- **Live NASA DONKI Space Weather:** Direct integration with official NASA APIs for Solar Energetic Particle (SEP) storms, Solar Flares, and Geomagnetic ($K_p$) disturbances.
- **Cardiovascular & Hemodynamics:** Simulated blood pressure (systolic / diastolic mmHg & MAP) with scenario controls, plus Heart Rate (BPM), Pulse Oximetry ($\text{SpO}_2\%$), and Respiration Rate. Blood-pressure values are demonstration data, not sensor measurements.
- **Radiation Dosimetry (NASA-STD-3001):** True CPM calculated via $190\ \mu\text{s}$ non-paralyzable dead-time recovery physics, dose rate ($\mu\text{Sv/h}$ and $\text{mSv/h}$), 30-Day BFO limit progress bar ($250\text{ mSv}$), and Time-to-Limit (TTL) countdown.
- **Autonomous EVA Suit Mode:** Detects barometric depressurization below $450\text{ hPa}$ ($4.3\text{ psi}$ NASA xEMU spacesuit) and dynamically tightens radiation alert thresholds.
- **Interactive Scenarios Bar:** Click between *Nominal*, *EVA Spacewalk*, *Tachycardia Stress*, *NASA DONKI SEP Storm*, and *Critical Abort* with 1 click.
- **Virtual AstroWrist HUD:** Pixel-identical software emulation of the $128\times64$ OLED astronaut wrist unit.

---

## 1. Project Overview & Multi-Modal Architecture

**AstroWrist Dosimeter & Biometric Monitor** is a standalone wearable hardware instrument designed for astronauts operating aboard orbital habitats (such as the ISS or Gateway) and during Extravehicular Activities (EVA). Unlike passive thermoluminescent dosimeters (TLDs) that can only be analyzed post-flight, **AstroWrist** continuously fuses real-time ionizing radiation dosimetry with wrist-worn cardiovascular photoplethysmography (PPG), suit/cabin barometric telemetry, and spacecraft space-weather uplinks from the **NASA DONKI API**.

```
                   +---------------------------------------------------+
                   |         SPACECRAFT ONBOARD COMPUTER UPLINK        |
                   |   nasa_uplink/nasa_donki_serial.py (NASA DONKI)   |
                   |     {"sep_storm": true, "solar_flux": 15.4}       |
                   +-------------------------+-------------------------+
                                             | UART Serial (115200 baud)
                                             v
+----------------------+         +-----------------------+         +-----------------------+
| J305 Geiger-Müller   | GPIO 4  |                       | GPIO 19 | Wrist Haptic Vibro    |
| High-Voltage Module  +-------->|   ESP32-WROOM-32D     +-------->| Motor (SEP / Alarm)   |
| (400V Anode, 190us)  | IRAM ISR|   Dual-Core 240 MHz   |         +-----------------------+
+----------------------+         |                       | GPIO 18 | Piezo Buzzer          |
                                 |  - 12x5s Ring Buffer  +-------->| (Clicks & Siren)      |
+----------------------+         |  - Dead-Time Math     |         +-----------------------+
| Tactile Mode/Reset   | GPIO 0  |  - NVS Dose Storage   | GPIO 2  | Critical Dose Strobe  |
| Push Button          +-------->|  - Biodosimetry FSM   +-------->| High-Brightness LED   |
+----------------------+         +-----------+-----------+         +-----------------------+
                                             |
                        Shared Fast I2C Bus  | (SDA = GPIO 21, SCL = GPIO 22)
                 +---------------------------+---------------------------+
                 |                           |                           |
                 v                           v                           v
     +-----------------------+   +-----------------------+   +-----------------------+
     |  SSD1306 128x64 OLED  |   |  MAX30102 Wrist PPG   |   |  BMP280 Barometer     |
     |  Wrist HUD (0x3C)     |   |  Heart Rate & SpO2    |   |  Auto-EVA (<450 hPa)  |
     +-----------------------+   +-----------------------+   +-----------------------+
```

### Key Hardware Subsystems
1. **Ionizing Radiation Sensing (`GeigerCounter` — `src/geiger_counter.h`):**
   - Interfaces with a **J305** $\beta/\gamma$ Geiger-Müller tube driven by a $400\text{ V}$ high-voltage cascade (`RadiationD-v1.1 / CAJOE`).
   - Captures falling-edge ionization pulses via an `IRAM_ATTR` hardware interrupt on `GPIO 4` with $190\ \mu\text{s}$ high-frequency debounce/dead-time filtering.
   - Computes instantaneous sliding-window Counts Per Minute (`CPM`) every second using a **12-bucket $\times$ 5-second ring buffer** without waiting for a full 60-second window.
   - Persists cumulative mission dose (`accumulated_mSv`) to ESP32 **Non-Volatile Storage (`Preferences` / NVS Flash)** every 60 seconds.
2. **Optical Wrist Biometrics (`HealthMonitor` — `src/biodosimetry_health.h`):**
   - Uses a wrist-mounted **MAX30102** dual-wavelength (660 nm Red / 880 nm IR) optical photoplethysmography sensor to measure **Heart Rate (BPM)** and arterial blood oxygen saturation (**$\text{SpO}_2\ \%$**).
3. **Autonomous Barometric Airlock & EVA Detection (`BMP280`):**
   - Samples ambient atmospheric pressure via a **BMP280** barometer. When pressure drops below **$450\text{ hPa}$** (simulating airlock depressurization or transition into a $4.3\text{ psi} \approx 296\text{ hPa}$ NASA EMU/xEMU spacesuit), the firmware automatically switches from `[MODULE]` to `[EVA]` mode and lowers radiation warning thresholds.
4. **Compact Wrist HUD & Haptic Alerts (`WristDisplay` — `src/wrist_display_ui.h`):**
   - Drives a **128$\times$64 SSD1306 OLED** screen with mode badges, animated heart rate icon, battery gauge, large $\mu\text{Sv/h}$ & CPM readouts, a 30-day dose limit progress bar, Time-to-Limit (`TTL`), and hardware-level inverse display flashing (`invertDisplay`) during `CRITICAL_ABORT`.

---

## 2. ESP32 Pinout Table

All hardware pin assignments are defined in [`src/config.h`](src/config.h):

| Peripheral / Signal Line | Sensor Pin | ESP32 GPIO | Direction / Bus | Function in AstroWrist System |
| :--- | :---: | :---: | :---: | :--- |
| **Geiger Counter J305** | `INT / PULSE` | **`GPIO 4`** | Input (`INPUT_PULLUP`) | Hardware interrupt `onParticleHit()` (`FALLING` edge in IRAM) |
| **Wrist Vibration Motor** | `VIBRO_IN` | **`GPIO 19`** | Output (MOSFET Driver) | Haptic wrist vibration on radiation alerts & NASA DONKI SEP storms |
| **Piezoelectric Buzzer** | `BUZZER_IO` | **`GPIO 18`** | Output (PWM / Tone) | Real-time particle clicks ($2.4\text{ kHz}$) and dual-tone abort siren |
| **Critical Alarm LED** | `LED_ANODE` | **`GPIO 2`** | Output | Visual strobe indicator on critical dose or solar proton storm |
| **Mode / Reset Button** | `BTN_SW` | **`GPIO 0`** | Input (`INPUT_PULLUP`) | Short press: toggle OLED page / mute warning; 3s hold: NVS dose reset |
| **I2C Data Line** | `SDA` | **`GPIO 21`** | Bidirectional (I2C) | Shared I2C data line for SSD1306 OLED, MAX30102, and BMP280 |
| **I2C Clock Line** | `SCL` | **`GPIO 22`** | Output (I2C $400\text{ kHz}$) | Shared I2C clock line for SSD1306 OLED, MAX30102, and BMP280 |
| **Battery Sense Divider** | `VBAT_DIV` | **`GPIO 34`** | Analog Input (`ADC1_CH6`) | LiPo battery voltage monitoring via 1:2 resistive divider |

---

## 3. Physics, Dosimetry Math & NASA-STD-3001 Compliance

### 3.1. Sliding Window CPM (12 Buckets $\times$ 5 Seconds)
Standard Geiger counters either suffer from high statistical jitter over short intervals or require a full 60-second delay before updating. `GeigerCounter` uses a circular buffer of $N = 12$ buckets of $\Delta t_b = 5\text{ s}$ each ($60\text{ s}$ total span), transferring hardware ISR counts every $1\text{ s}$ to deliver an immediate, low-variance sliding `raw_cpm`.

### 3.2. Geiger-Müller Tube Dead-Time Correction
During intense Solar Energetic Particle (SEP) events or passages through the South Atlantic Anomaly (SAA), a Geiger-Müller tube becomes momentarily insensitive after each Townsend avalanche for its recovery/dead time $\tau = 190\ \mu\text{s}$ (`DEAD_TIME_US = 190`). At high count rates, raw counts significantly underestimate the true radiation field.

The firmware applies the non-paralyzable detector dead-time correction formula every second:

$$\text{CPM}_{\text{true}} = \frac{\text{CPM}_{\text{raw}}}{1.0 - \text{CPM}_{\text{raw}} \cdot \tau_{\text{min}}}$$

where $\tau_{\text{min}} = \frac{190 \times 10^{-6}\text{ s}}{60\text{ s/min}} = 3.1667 \times 10^{-6}\text{ min}$.

### 3.3. Dose Rate, Numerical Integration & Time-to-Limit (`TTL`)
Using the calibrated J305 conversion factor $K_{\text{J305}} = 0.00812\ \frac{\mu\text{Sv/h}}{\text{CPM}}$:

$$\dot{H}_{\mu\text{Sv/h}} = \text{CPM}_{\text{true}} \times 0.00812, \qquad \dot{H}_{\text{mSv/h}} = \frac{\dot{H}_{\mu\text{Sv/h}}}{1000}$$

Accumulated dose $D_{\text{acc}}$ ($\text{mSv}$) is integrated over each time step $\Delta t$ (in hours) and committed to ESP32 NVS flash every 60 seconds:

$$D_{\text{acc}}(t + \Delta t) = D_{\text{acc}}(t) + \dot{H}_{\text{mSv/h}} \cdot \Delta t_{\text{hours}}$$

Remaining safe operational time (`time_to_limit_hours`) before reaching the **NASA-STD-3001 30-Day Blood Forming Organs (BFO) limit** ($D_{\text{30d}} = 250.0\text{ mSv}$) is computed continuously:

$$T_{\text{limit (hours)}} = \frac{250.0\text{ mSv} - D_{\text{acc}}}{\dot{H}_{\text{mSv/h}}}$$

### 3.4. Autonomous Barometric EVA Detection & Integrated Biodosimetry
- **Habitat vs. Open Space (`EVA_MODE`):** Pressurized orbital modules maintain $\sim 1013\text{ hPa}$ and provide substantial aluminum/polyethylene shielding. Pure-oxygen spacesuits (NASA EMU/xEMU) operate at $4.3\text{ psi}$ ($\sim 296\text{ hPa}$). When the **BMP280** detects $P < 450\text{ hPa}$, the wrist unit autonomously switches to `EVA_MODE` and tightens the radiation warning threshold from $15.0\ \mu\text{Sv/h}$ down to $8.0\ \mu\text{Sv/h}$ (and critical threshold from $100.0\ \mu\text{Sv/h}$ to $50.0\ \mu\text{Sv/h}$).
- **Multi-Modal Crew Status Evaluation (`NOMINAL`, `RADIATION_WARNING`, `BIOMETRIC_STRESS`, `CRITICAL_ABORT`):** Following **NASA-STD-3001**, acute radiation exposure coupled with high physical exertion or autonomic stress is hazardous during EVA. If the **MAX30102** detects **tachycardia ($\text{BPM} > 130$)** concurrently with elevated radiation ($\dot{H} \ge \dot{H}_{\text{warn}}$), or if dose rates exceed critical thresholds, the device triggers `CRITICAL_ABORT` — flashing the OLED display in inverse video, sounding a dual-tone siren, and pulsing the wrist haptic actuator.

---

## 4. Repository Structure & Build Instructions

```text
AstroHealth-Telemetry/
├── app.py                          # NASA Web Dashboard & Mission Cockpit (FastAPI / HTML5 Canvas)
├── templates/
│   └── index.html                  # Responsive Mission Control UI with 60fps ECG & Virtual Wrist HUD
├── requirements.txt                # Python dependencies (fastapi, uvicorn, requests, jinja2)
├── LICENSE                         # Official Apache 2.0 Open Source License
├── README.md                       # Comprehensive English documentation & NASA attribution
├── nasa_uplink/
│   ├── nasa_donki_serial.py        # Live NASA DONKI API (SEP/GST/FLR) query engine & SQLite mirror
│   └── nasa_donki_events.db        # Pre-seeded NASA Solar Cycle 25 verified reference storms
└── src/                            # Optional Embedded Edge Hardware Firmware (ESP32-WROOM-32D)
    ├── config.h                    # Pinouts, NASA-STD-3001 constants & medical thresholds
    ├── geiger_counter.h            # 12x5s sliding window ring buffer & 190 us dead-time math
    ├── biodosimetry_health.h       # Optical PPG vitals, BMP280 auto-EVA detection (<450 hPa)
    ├── wrist_display_ui.h          # SSD1306 128x64 OLED graphics driver & critical inverse flashing
    └── main.cpp                    # NVS persistence, UART JSON bridge & 1 Hz telemetry
```

### Where to look in the code

- `app.py` — runs the dashboard server and updates the simulated readings.
- `templates/index.html` — draws the web page and asks `app.py` for new readings.
- `nasa_uplink/nasa_donki_serial.py` — downloads NASA space-weather data and can send it to an ESP32.
- `src/main.cpp` — starts the real ESP32 and repeats its main update loop.
- `src/geiger_counter.h` — counts Geiger pulses and calculates radiation dose.
- `src/biodosimetry_health.h` — reads MAX30102 and BMP280 sensors. It does not measure blood pressure.
- `src/wrist_display_ui.h` — draws readings on the small OLED screen.
- `src/config.h` — contains GPIO numbers and thresholds.

### Option A: Running the Interactive Web Application (Primary Software Submission)
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the application
python app.py
```
*Access the interface at `http://127.0.0.1:8000` to interact with live NASA space weather, simulated blood-pressure controls, and dosimetry.*

### Option B: Compiling the ESP32 Physical Wearable Firmware (Edge Hardware Concept)
```bash
# Compile and flash via PlatformIO (if hardware is present)
pio run --target upload --target monitor
```

---

## 5. NASA Data & Resources Usage

This project directly integrates official NASA open-science databases and human spaceflight medical standards:

1. **NASA Space Weather Database Of Notifications, Knowledge, Information (DONKI) API:**
   - **Solar Energetic Particle (SEP) Endpoint:** [`https://api.nasa.gov/DONKI/SEP`](https://api.nasa.gov/DONKI/SEP) — Used by [`nasa_uplink/nasa_donki_serial.py`](nasa_uplink/nasa_donki_serial.py) to detect active solar proton storms and trigger pre-emptive wrist haptic warnings on the astronaut's dosimeter before peak particle flux arrival.
   - **Geomagnetic Storm (GST) Endpoint:** [`https://api.nasa.gov/DONKI/GST`](https://api.nasa.gov/DONKI/GST) — Used to monitor planetary $K_p$ geomagnetic storm indices affecting low-Earth orbit magnetospheric shielding.
   - **Solar Flare (FLR) Endpoint:** [`https://api.nasa.gov/DONKI/FLR`](https://api.nasa.gov/DONKI/FLR) — Used to quantify X-class and M-class solar flare flux precursors (`solar_flux`).
   - **Official Portal:** [https://api.nasa.gov/](https://api.nasa.gov/) & [https://ccmc.gsfc.nasa.gov/tools/DONKI/](https://ccmc.gsfc.nasa.gov/tools/DONKI/)
2. **NASA Spaceflight Human-System Standard (NASA-STD-3001, Volume 1: Crew Health):**
   - Defines the permissible ionizing radiation dose limits implemented in [`src/config.h`](src/config.h):
     - **30-Day Blood Forming Organs (BFO) Limit:** `NASA_30DAY_LIMIT_MSV = 250.0 mSv`
     - **Career Effective Dose Limit:** `NASA_CAREER_LIMIT_MSV = 600.0 mSv`
   - Defines the barometric pressure regime for Extravehicular Mobility Units ($4.3\text{ psi} \approx 296\text{ hPa}$, triggering `EVA_MODE` below $450\text{ hPa}$) and cardiovascular stress limits.
   - **Official Document:** [https://www.nasa.gov/ochmo/health-and-medical-policy-and-standards/nasa-std-3001-technical-briefs/](https://www.nasa.gov/ochmo/health-and-medical-policy-and-standards/nasa-std-3001-technical-briefs/)

---

## 6. AI & Tool Attribution (NASA Space Apps Transparency Statement)

In accordance with the **NASA International Space Apps Challenge** rules on transparency and attribution:
- **AI Assistance Disclosure:** Generative AI coding tools (Google Gemini / Antigravity AI Assistant) were utilized to assist in structuring the modular C++ header architecture (`GeigerCounter`, `HealthMonitor`, `WristDisplay`), verifying the mathematical formulation of the 12-bucket sliding-window dead-time correction, and formatting the English technical documentation.
- **Open-Source Libraries:** Uses `Adafruit_SSD1306`, `Adafruit_GFX`, `Adafruit_BMP280`, `SparkFun_MAX3010x_Sensor_Library`, and `ArduinoJson` under their respective open-source licenses via PlatformIO.
