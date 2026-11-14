#ifndef ASTROWRIST_CONFIG_H
#define ASTROWRIST_CONFIG_H

#include <Arduino.h>

// Здесь собраны номера выводов ESP32 и основные пороги.
// Если подключил датчик к другому GPIO, сначала поменяй номер здесь.

// ============================================================================
// 1. HARDWARE PINOUT DEFINITIONS (ESP32-WROOM-32D)
// ============================================================================

// High-voltage Geiger-Mueller module (J305 / RadiationD-v1.1 CAJOE) interrupt pin
constexpr uint8_t PIN_GEIGER_PULSE = 4;

// Wrist haptic vibration motor (PWM / digital MOSFET driver for radiation alerts)
constexpr uint8_t PIN_VIBRO_MOTOR  = 19;

// Piezoelectric buzzer for real-time particle clicks and critical alarm siren
constexpr uint8_t PIN_BUZZER       = 18;

// High-brightness visual alarm LED for critical dose / SEP storm indication
constexpr uint8_t PIN_LED_ALARM    = 2;

// Tactile push button for manual screen cycling and non-critical alarm mute/reset
constexpr uint8_t PIN_MODE_BTN     = 0;

// Shared I2C Bus for SSD1306 OLED, MAX30102 Pulse Oximeter, and BMP280 Barometer
constexpr uint8_t PIN_I2C_SDA      = 21;
constexpr uint8_t PIN_I2C_SCL      = 22;
constexpr uint8_t SDA_PIN          = PIN_I2C_SDA;
constexpr uint8_t SCL_PIN          = PIN_I2C_SCL;

// LiPo Battery voltage divider sense pin (ADC1_CH6, 2x 100kOhm divider)
constexpr uint8_t PIN_BATTERY_ADC  = 34;

// ============================================================================
// 2. PHYSICAL & MEDICAL CONSTANTS (NASA-STD-3001 VOLUME 1 STANDARDS)
// ============================================================================

// J305 beta/gamma Geiger-Mueller tube conversion factor: CPM -> uSv/h
// Standard calibration (Cs-137 / Co-60): 1 CPM = 0.00812 uSv/h (~123.15 CPM per uSv/h)
constexpr float J305_CONVERSION_FACTOR = 0.00812f;

// Geiger-Mueller tube recovery / dead time in microseconds (J305 spec: 190 us)
constexpr uint32_t DEAD_TIME_US        = 190;

// Dead time expressed in minutes for direct mathematical correction with CPM:
// DEAD_TIME_MINUTES = (190 * 10^-6 seconds) / (60 seconds/minute) = 3.1666667e-6 min
constexpr float DEAD_TIME_MINUTES      = static_cast<float>(DEAD_TIME_US) / 60000000.0f;

// NASA-STD-3001 Blood Forming Organs (BFO) 30-day radiation dose limit (mSv)
constexpr float NASA_30DAY_LIMIT_MSV   = 250.0f;

// NASA-STD-3001 Career effective radiation dose limit for astronauts (mSv)
constexpr float NASA_CAREER_LIMIT_MSV  = 600.0f;

// ============================================================================
// 3. GEIGER SLIDING WINDOW & TIMING PARAMETERS
// ============================================================================

// 12 buckets of 5 seconds each = 60-second sliding window updated every 1 second
constexpr uint8_t  GEIGER_BUCKET_COUNT       = 12;
constexpr uint32_t GEIGER_BUCKET_DURATION_MS = 5000;
constexpr uint32_t GEIGER_UPDATE_INTERVAL_MS = 1000;

// Non-Volatile Storage (NVS) commit period (save accumulated_mSv every 60 seconds)
constexpr uint32_t NVS_SAVE_INTERVAL_MS      = 60000;

// Serial JSON telemetry broadcast period (1 Hz)
constexpr uint32_t TELEMETRY_INTERVAL_MS     = 1000;

// OLED UI refresh interval (4 Hz for smooth heart icon animation and inverse blink)
constexpr uint32_t DISPLAY_REFRESH_MS        = 250;

// NASA DONKI space weather alert expiration window (15 minutes without update)
constexpr uint32_t DONKI_ALERT_TIMEOUT_MS    = 900000;

// ============================================================================
// 4. ENVIRONMENTAL & BIODOSIMETRY THRESHOLDS
// ============================================================================

// Barometric pressure threshold (hPa) for autonomous Extra-Vehicular Activity detection.
// Nominal ISS cabin is ~1013 hPa; NASA EMU/xEMU spacesuit operates at ~296-400 hPa (4.3 psi).
// Dropping below 450 hPa indicates airlock depressurization / EVA suit mode.
constexpr float EVA_PRESSURE_THRESHOLD_HPA   = 450.0f;

// Radiation dose-rate thresholds inside shielded spacecraft module (uSv/h)
constexpr float RAD_WARNING_USVH_MODULE      = 15.0f;
constexpr float RAD_CRITICAL_USVH_MODULE     = 100.0f;

// Reduced radiation dose-rate thresholds during EVA (unshielded spacesuit environment)
constexpr float RAD_WARNING_USVH_EVA         = 8.0f;
constexpr float RAD_CRITICAL_USVH_EVA        = 50.0f;

// Biometric thresholds on wrist PPG sensor (MAX30102)
constexpr float TACHYCARDIA_BPM_THRESHOLD    = 130.0f;
constexpr float ELEVATED_BPM_THRESHOLD       = 115.0f;
constexpr float BRADYCARDIA_BPM_THRESHOLD    = 45.0f;
constexpr float HYPOXIA_SPO2_THRESHOLD       = 92.0f;
constexpr float CRITICAL_SPO2_THRESHOLD      = 88.0f;

// ============================================================================
// 5. I2C PERIPHERAL ADDRESSES & DISPLAY GEOMETRY
// ============================================================================

constexpr uint16_t OLED_WIDTH                = 128;
constexpr uint16_t OLED_HEIGHT               = 64;
constexpr int8_t   OLED_RESET_PIN            = -1;
constexpr uint8_t  OLED_I2C_ADDR             = 0x3C;

constexpr uint8_t  BMP280_I2C_ADDR_PRIMARY   = 0x76;
constexpr uint8_t  BMP280_I2C_ADDR_SECONDARY = 0x77;

// ============================================================================
// 6. SYSTEM ENUMERATIONS & DATA STRUCTURES
// ============================================================================

// Astronaut environment mode automatically determined by BMP280 barometer
enum OperatingMode : uint8_t {
    MODE_MODULE = 0,  // Pressurized orbital habitat (>= 450 hPa)
    EVA_MODE    = 1   // Airlock depressurization / Extravehicular Activity (< 450 hPa)
};

// Integrated biodosimetric crew status combining radiation & cardiovascular telemetry
enum CrewHealthStatus : uint8_t {
    NOMINAL           = 0,  // Normal radiation background and stable vitals
    RADIATION_WARNING = 1,  // Elevated dose rate, DONKI SEP storm warning, or >50% 30d limit
    BIOMETRIC_STRESS  = 2,  // Tachycardia (>130 BPM) or mild hypoxia (<92% SpO2)
    CRITICAL_ABORT    = 3   // Critical dose rate, >90% limit, or tachycardia + high radiation
};

// Spacecraft uplink message structure populated from NASA DONKI API via Serial JSON
struct NasaDonkiSpaceWeather {
    bool     sep_storm;         // Active Solar Energetic Particle (SEP) event flag
    float    solar_flux;        // Proton / flare flux index (pfu or W/m^2 scaled)
    float    kp_index;          // Planetary geomagnetic Kp index (0.0 - 9.0)
    char     event_id[32];      // Latest NASA DONKI event ID (e.g., "2026-11-14T12:00:00-SEP-001")
    uint32_t last_rx_millis;    // Timestamp of last uplink packet received
    bool     uplink_connected;  // True if spacecraft uplink packet received within timeout
};

// Helper functions to convert enums to human-readable telemetry strings
inline const char* operatingModeToString(OperatingMode mode) {
    return (mode == EVA_MODE) ? "EVA_MODE" : "MODULE";
}

inline const char* healthStatusToString(CrewHealthStatus status) {
    switch (status) {
        case NOMINAL:           return "NOMINAL";
        case RADIATION_WARNING: return "RADIATION_WARNING";
        case BIOMETRIC_STRESS:  return "BIOMETRIC_STRESS";
        case CRITICAL_ABORT:    return "CRITICAL_ABORT";
        default:                return "UNKNOWN";
    }
}

#endif // ASTROWRIST_CONFIG_H
