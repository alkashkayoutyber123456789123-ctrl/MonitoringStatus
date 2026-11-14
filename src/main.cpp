/**
 * ============================================================================
 * AstroWrist Dosimeter & Biometric Monitor — Main Flight Firmware
 * Target MCU: ESP32-WROOM-32D (PlatformIO / Arduino Framework)
 *
 * Features:
 * 1. NVS Flash Persistence (`Preferences`) reading & saving `accumulated_mSv`
 *    every 60 seconds to survive power cycles.
 * 2. J305 Geiger-Mueller interrupt dosimetry with 12x5s sliding window and
 *    190 us dead-time mathematical correction.
 * 3. MAX30102 wrist PPG (Heart Rate BPM & SpO2 %) + BMP280 barometer with
 *    autonomous EVA_MODE switch (< 450 hPa) and dynamic threshold lowering.
 * 4. Spacecraft UART JSON uplink receiver for live NASA DONKI Space Weather
 *    alerts (`{"sep_storm": true, "solar_flux": 15.4}`) with pre-emptive
 *    wrist haptic vibration warning.
 * 5. Real-time 1 Hz JSON telemetry stream over Serial (115200 baud).
 * ============================================================================
 */

#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
#include <ArduinoJson.h>

#include "config.h"
#include "geiger_counter.h"
#include "biodosimetry_health.h"
#include "wrist_display_ui.h"

// Global subsystem instances
Preferences            g_nvsPrefs;
GeigerCounter          g_geiger;
HealthMonitor          g_health;
WristDisplay           g_display;
NasaDonkiSpaceWeather  g_donkiUplink = {
    false,   // sep_storm
    0.0f,    // solar_flux
    1.0f,    // kp_index
    "NONE",  // event_id
    0,       // last_rx_millis
    false    // uplink_connected
};

// Timing and non-blocking actuator state variables
uint32_t g_lastNvsSaveMs         = 0;
uint32_t g_lastTelemetryMs       = 0;
uint32_t g_vibroPulseEndMs       = 0;
uint32_t g_buzzerToneEndMs       = 0;
uint32_t g_lastAlarmPatternMs    = 0;
uint8_t  g_alarmPatternStep      = 0;
bool     g_alarmMutedByUser      = false;

// Button debounce & long-press state (PIN_MODE_BTN = GPIO 0)
bool     g_lastBtnState          = HIGH;
uint32_t g_btnPressStartMs       = 0;
bool     g_btnLongPressHandled   = false;

// Serial UART JSON reception buffer
String   g_serialRxBuffer        = "";

// ============================================================================
// ACTUATOR & HAPTIC FEEDBACK HELPERS (NON-BLOCKING)
// ============================================================================

void triggerWristVibration(uint32_t durationMs) {
    digitalWrite(PIN_VIBRO_MOTOR, HIGH);
    g_vibroPulseEndMs = millis() + durationMs;
}

void triggerBuzzerClick(uint16_t freqHz, uint32_t durationMs) {
    tone(PIN_BUZZER, freqHz, durationMs);
    g_buzzerToneEndMs = millis() + durationMs;
}

/**
 * @brief Updates non-blocking haptic vibration motor, piezo siren, and LED alarm
 *        according to current CrewHealthStatus and particle impacts.
 */
void updateActuatorsAndAlarms(uint32_t nowMs) {
    // 1. Turn off one-shot vibration pulse when timer expires
    if (g_vibroPulseEndMs > 0 && static_cast<int32_t>(nowMs - g_vibroPulseEndMs) >= 0) {
        digitalWrite(PIN_VIBRO_MOTOR, LOW);
        g_vibroPulseEndMs = 0;
    }

    // 2. Real-time piezo click & brief LED flash on single ionizing particle hit
    if (g_geiger.consumeParticleClick()) {
        if (g_health.getCrewStatus() == NOMINAL) {
            // Classic short 2 ms Geiger click at 2400 Hz
            tone(PIN_BUZZER, 2400, 3);
        }
    }

    // 3. Periodic alarm patterns for elevated states
    CrewHealthStatus status = g_health.getCrewStatus();

    if (status == NOMINAL) {
        g_alarmMutedByUser = false; // Reset mute latch once back in nominal state
        digitalWrite(PIN_LED_ALARM, LOW);
        return;
    }

    if (status == CRITICAL_ABORT) {
        // CRITICAL_ABORT cannot be muted: alternating dual-tone siren + strong wrist vibration + LED strobe
        if ((nowMs - g_lastAlarmPatternMs) >= 300) {
            g_lastAlarmPatternMs = nowMs;
            g_alarmPatternStep   = (g_alarmPatternStep + 1) % 2;

            if (g_alarmPatternStep == 0) {
                digitalWrite(PIN_LED_ALARM, HIGH);
                digitalWrite(PIN_VIBRO_MOTOR, HIGH);
                g_vibroPulseEndMs = nowMs + 250;
                tone(PIN_BUZZER, 3200, 260);
            } else {
                digitalWrite(PIN_LED_ALARM, LOW);
                tone(PIN_BUZZER, 1800, 260);
            }
        }
    } else if (status == RADIATION_WARNING || status == BIOMETRIC_STRESS) {
        // Warning state: pulse wrist vibration & LED every 2 seconds unless acknowledged by button
        if ((nowMs - g_lastAlarmPatternMs) >= 2000) {
            g_lastAlarmPatternMs = nowMs;
            digitalWrite(PIN_LED_ALARM, HIGH);
            if (!g_alarmMutedByUser) {
                triggerWristVibration(350);
                uint16_t warnFreq = (status == RADIATION_WARNING) ? 2600 : 1900;
                tone(PIN_BUZZER, warnFreq, 180);
            }
        } else if ((nowMs - g_lastAlarmPatternMs) >= 350) {
            digitalWrite(PIN_LED_ALARM, LOW);
        }
    }
}

// ============================================================================
// BUTTON HANDLER (PIN_MODE_BTN = GPIO 0)
// ============================================================================

/**
 * @brief Handles tactile button on GPIO 0:
 *        - Short press (< 1.5s): Cycles OLED UI page AND mutes non-critical warning vibration/buzzer.
 *        - Long press (>= 3.0s): Resets accumulated dose (`accumulated_mSv`) in RAM & NVS flash.
 */
void handleModeButton(uint32_t nowMs) {
    bool currentBtnState = digitalRead(PIN_MODE_BTN);

    // Button pressed (active LOW with internal pull-up)
    if (currentBtnState == LOW && g_lastBtnState == HIGH) {
        g_btnPressStartMs     = nowMs;
        g_btnLongPressHandled = false;
    } else if (currentBtnState == LOW && g_lastBtnState == LOW) {
        // Check for 3-second hold to reset accumulated mission dose
        if (!g_btnLongPressHandled && (nowMs - g_btnPressStartMs) >= 3000) {
            g_btnLongPressHandled = true;
            g_geiger.resetAccumulatedDose();
            g_nvsPrefs.putFloat("acc_msv", 0.0f);
            triggerWristVibration(500);
            tone(PIN_BUZZER, 1500, 400);
        }
    } else if (currentBtnState == HIGH && g_lastBtnState == LOW) {
        // Button released: check debounce (> 40 ms) and short press
        uint32_t pressDuration = nowMs - g_btnPressStartMs;
        if (pressDuration >= 40 && !g_btnLongPressHandled) {
            g_display.nextPage();
            g_alarmMutedByUser = true;
            tone(PIN_BUZZER, 2800, 40);
        }
    }

    g_lastBtnState = currentBtnState;
}

// ============================================================================
// SPACECRAFT UART UPLINK — NASA DONKI SPACE WEATHER JSON PARSER
// ============================================================================

/**
 * @brief Parses incoming JSON packets from spacecraft computer over Serial UART.
 *        Expected primary payload from `nasa_donki_serial.py`:
 *        `{"sep_storm": true, "solar_flux": 15.4}`
 *        Also supports optional diagnostic fields:
 *        `"kp_index"`, `"event_id"`, `"sim_cpm"`, `"sim_bpm"`, `"sim_spo2"`, `"sim_pressure_hpa"`
 */
void processSpacecraftUplinkJson(const String& jsonLine, uint32_t nowMs) {
#if ARDUINOJSON_VERSION_MAJOR >= 7
    JsonDocument doc;
#else
    StaticJsonDocument<512> doc;
#endif

    DeserializationError err = deserializeJson(doc, jsonLine);
    if (err) {
        return;
    }

    bool previousSepStorm = g_donkiUplink.sep_storm;

    if (!doc["sep_storm"].isNull()) {
        g_donkiUplink.sep_storm = doc["sep_storm"].as<bool>();
    }
    if (!doc["solar_flux"].isNull()) {
        g_donkiUplink.solar_flux = doc["solar_flux"].as<float>();
    }
    if (!doc["kp_index"].isNull()) {
        g_donkiUplink.kp_index = doc["kp_index"].as<float>();
    }
    if (!doc["event_id"].isNull()) {
        const char* evId = doc["event_id"];
        if (evId != nullptr) {
            strncpy(g_donkiUplink.event_id, evId, sizeof(g_donkiUplink.event_id) - 1);
            g_donkiUplink.event_id[sizeof(g_donkiUplink.event_id) - 1] = '\0';
        }
    }

    g_donkiUplink.last_rx_millis   = nowMs;
    g_donkiUplink.uplink_connected = true;

    // Optional hardware-in-the-loop simulation parameters for bench verification
    if (!doc["sim_cpm"].isNull()) {
        g_geiger.setSimulatedStormCpmBias(doc["sim_cpm"].as<float>());
    } else if (g_donkiUplink.sep_storm && g_donkiUplink.solar_flux > 10.0f && g_geiger.getTotalPulses() == 0) {
        // When no physical J305 tube pulses are present and an active NASA DONKI SEP storm arrives,
        // couple solar_flux into storm CPM bias only if explicitly requested via "couple_flux": true
        if (!doc["couple_flux"].isNull() && doc["couple_flux"].as<bool>()) {
            g_geiger.setSimulatedStormCpmBias(g_donkiUplink.solar_flux * 120.0f);
        }
    }

    if (!doc["sim_bpm"].isNull() || !doc["sim_pressure_hpa"].isNull()) {
        float simBpm  = !doc["sim_bpm"].isNull() ? doc["sim_bpm"].as<float>() : g_health.getHeartRateBpm();
        float simSpo2 = !doc["sim_spo2"].isNull() ? doc["sim_spo2"].as<float>() : g_health.getSpO2Percent();
        float simPres = !doc["sim_pressure_hpa"].isNull() ? doc["sim_pressure_hpa"].as<float>() : g_health.getPressureHpa();
        g_health.setSimulatedTelemetry(simBpm, simSpo2, simPres, true);
    }

    if (!doc["reset_dose"].isNull() && doc["reset_dose"].as<bool>()) {
        g_geiger.resetAccumulatedDose();
        g_nvsPrefs.putFloat("acc_msv", 0.0f);
    }

    // Pre-emptive wrist haptic vibration alert when NASA DONKI reports an incoming SEP storm
    // or high solar proton flux (>= 10.0 pfu)
    if (g_donkiUplink.sep_storm || g_donkiUplink.solar_flux >= 10.0f) {
        g_alarmMutedByUser = false;
        // Immediate 800 ms haptic warning burst on the astronaut's wrist + double audio chime
        triggerWristVibration(800);
        digitalWrite(PIN_LED_ALARM, HIGH);
        if (!previousSepStorm) {
            tone(PIN_BUZZER, 3000, 250);
        }
    }
}

void pollSerialUplink(uint32_t nowMs) {
    while (Serial.available() > 0) {
        char ch = static_cast<char>(Serial.read());
        if (ch == '\n' || ch == '\r') {
            g_serialRxBuffer.trim();
            if (g_serialRxBuffer.startsWith("{") && g_serialRxBuffer.endsWith("}")) {
                processSpacecraftUplinkJson(g_serialRxBuffer, nowMs);
            }
            g_serialRxBuffer = "";
        } else if (g_serialRxBuffer.length() < 512) {
            g_serialRxBuffer += ch;
        } else {
            g_serialRxBuffer = ""; // Prevent buffer overflow on malformed serial noise
        }
    }

    // Expire DONKI uplink flag if no packet received within timeout window (15 min)
    if (g_donkiUplink.uplink_connected &&
        (nowMs - g_donkiUplink.last_rx_millis) > DONKI_ALERT_TIMEOUT_MS) {
        g_donkiUplink.uplink_connected = false;
        g_donkiUplink.sep_storm        = false;
    }
}

// ============================================================================
// 1 HZ SERIAL JSON TELEMETRY OUTPUT
// ============================================================================

void broadcastJsonTelemetry(uint32_t nowMs) {
#if ARDUINOJSON_VERSION_MAJOR >= 7
    JsonDocument doc;
#else
    StaticJsonDocument<768> doc;
#endif

    doc["device"]              = "AstroWrist-ESP32";
    doc["uptime_sec"]          = nowMs / 1000UL;
    doc["mode"]                = operatingModeToString(g_health.getOperatingMode());
    doc["status"]              = healthStatusToString(g_health.getCrewStatus());

    // Radiation Dosimetry Block (J305 + Dead-Time Correction + NASA-STD-3001)
    JsonObject rad = doc["dosimetry"].to<JsonObject>();
    rad["raw_cpm"]             = serialized(String(g_geiger.getRawCPM(), 2));
    rad["true_cpm"]            = serialized(String(g_geiger.getTrueCPM(), 2));
    rad["uSv_h"]               = serialized(String(g_geiger.getDoseRateUsvh(), 4));
    rad["mSv_h"]               = serialized(String(g_geiger.getDoseRateMsvh(), 6));
    rad["accumulated_mSv"]     = serialized(String(g_geiger.getAccumulatedMsv(), 5));
    rad["time_to_limit_hours"] = serialized(String(g_geiger.getTimeToLimitHours(), 2));
    rad["limit_30d_mSv"]       = NASA_30DAY_LIMIT_MSV;
    rad["limit_career_mSv"]    = NASA_CAREER_LIMIT_MSV;
    rad["warn_thresh_uSv_h"]   = g_health.getActiveWarningUsvh();
    rad["pulses_last_sec"]     = g_geiger.getPulsesLastSecond();
    rad["total_pulses"]        = g_geiger.getTotalPulses();

    // Biometrics & Suit Environment Block (MAX30102 + BMP280)
    JsonObject bio = doc["biometrics"].to<JsonObject>();
    bio["heart_rate_bpm"]      = serialized(String(g_health.getHeartRateBpm(), 1));
    bio["spo2_percent"]        = serialized(String(g_health.getSpO2Percent(), 1));
    bio["pressure_hpa"]        = serialized(String(g_health.getPressureHpa(), 2));
    bio["temperature_c"]       = serialized(String(g_health.getSuitTemperatureC(), 1));
    bio["battery_v"]           = serialized(String(g_health.getBatteryVoltage(), 2));
    bio["battery_pct"]         = g_health.getBatteryPercent();

    // NASA DONKI Space Weather Uplink Block
    JsonObject donki = doc["nasa_donki"].to<JsonObject>();
    donki["uplink_active"]     = g_donkiUplink.uplink_connected;
    donki["sep_storm"]         = g_donkiUplink.sep_storm;
    donki["solar_flux"]        = serialized(String(g_donkiUplink.solar_flux, 2));
    donki["kp_index"]          = serialized(String(g_donkiUplink.kp_index, 1));
    donki["event_id"]          = g_donkiUplink.event_id;

    serializeJson(doc, Serial);
    Serial.println();
}

// ============================================================================
// ARDUINO SETUP & MAIN EXECUTION LOOP
// ============================================================================

void setup() {
    // 1. Initialize UART at 115200 baud for telemetry output & NASA DONKI uplink
    Serial.begin(115200);

    // 2. Configure GPIO pins for actuators, LED, and mode button
    pinMode(PIN_VIBRO_MOTOR, OUTPUT);
    pinMode(PIN_BUZZER, OUTPUT);
    pinMode(PIN_LED_ALARM, OUTPUT);
    pinMode(PIN_MODE_BTN, INPUT_PULLUP);

    digitalWrite(PIN_VIBRO_MOTOR, LOW);
    digitalWrite(PIN_BUZZER, LOW);
    digitalWrite(PIN_LED_ALARM, LOW);

    // 3. Initialize Non-Volatile Storage (NVS) and restore accumulated radiation dose
    g_nvsPrefs.begin("astrowrist", false);
    float restoredDoseMsv = g_nvsPrefs.getFloat("acc_msv", 0.0f);
    if (isnan(restoredDoseMsv) || restoredDoseMsv < 0.0f || restoredDoseMsv > 10000.0f) {
        restoredDoseMsv = 0.0f;
    }

    // 4. Initialize shared I2C bus on SDA = GPIO 21, SCL = GPIO 22 (400 kHz Fast Mode)
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000UL);

    // 5. Initialize OLED Wrist Display, J305 Geiger Counter, and MAX30102/BMP280 Health Monitor
    g_display.begin();
    g_geiger.begin(restoredDoseMsv);
    g_health.begin(Wire);

    // Brief tactile & acoustic self-test confirmation on wrist
    triggerWristVibration(120);
    tone(PIN_BUZZER, 2200, 80);

    uint32_t nowMs     = millis();
    g_lastNvsSaveMs    = nowMs;
    g_lastTelemetryMs  = nowMs;
}

void loop() {
    uint32_t nowMs = millis();

    // 1. Poll spacecraft Serial UART for incoming NASA DONKI space weather JSON packets
    pollSerialUplink(nowMs);

    // 2. Poll tactile button (GPIO 0) for screen toggle, alarm mute, or 3s dose reset
    handleModeButton(nowMs);

    // 3. Continuously sample MAX30102 PPG (BPM/SpO2) and BMP280 barometer (EVA detection)
    g_health.updateSensors(nowMs);

    // 4. Update 12-bucket sliding-window Geiger dosimetry and evaluate crew health state
    if (g_geiger.update(nowMs)) {
        g_health.evaluateCrewStatus(
            g_geiger.getDoseRateUsvh(),
            g_geiger.getAccumulatedMsv(),
            g_donkiUplink
        );
    }

    // 5. Update non-blocking wrist haptic vibration, piezo clicks/siren, and alarm LED
    updateActuatorsAndAlarms(nowMs);

    // 6. Refresh 128x64 OLED wrist UI (4 Hz with hardware inverse blink on CRITICAL_ABORT)
    g_display.render(nowMs, g_geiger, g_health, g_donkiUplink);

    // 7. Broadcast comprehensive JSON telemetry over Serial every 1 second (1000 ms)
    if ((nowMs - g_lastTelemetryMs) >= TELEMETRY_INTERVAL_MS) {
        g_lastTelemetryMs = nowMs;
        broadcastJsonTelemetry(nowMs);
    }

    // 8. Persist accumulated radiation dose (accumulated_mSv) to NVS flash every 60 seconds
    if ((nowMs - g_lastNvsSaveMs) >= NVS_SAVE_INTERVAL_MS) {
        g_lastNvsSaveMs = nowMs;
        g_nvsPrefs.putFloat("acc_msv", g_geiger.getAccumulatedMsv());
    }
}
