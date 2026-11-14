#ifndef ASTROWRIST_WRIST_DISPLAY_UI_H
#define ASTROWRIST_WRIST_DISPLAY_UI_H

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "config.h"

// Экран только показывает данные; измерениями занимаются классы датчиков.
#include "geiger_counter.h"
#include "biodosimetry_health.h"

// 8x7 pixel bitmap for heart icon (normal beat state)
static const uint8_t PROGMEM BMP_HEART_IDLE[7] = {
    0b01101100,
    0b11111110,
    0b11111110,
    0b11111110,
    0b01111100,
    0b00111000,
    0b00010000
};

// 8x7 pixel bitmap for heart icon (systolic pulse state)
static const uint8_t PROGMEM BMP_HEART_PULSE[7] = {
    0b01101100,
    0b10010010,
    0b10000010,
    0b10000010,
    0b01000100,
    0b00101000,
    0b00010000
};

// 8x8 pixel radiation trefoil / warning symbol
static const uint8_t PROGMEM BMP_RAD_SYMBOL[8] = {
    0b00111100,
    0b01000010,
    0b10111101,
    0b10100101,
    0b10111101,
    0b01011010,
    0b00111100,
    0b00000000
};

/**
 * @class WristDisplay
 * @brief SSD1306 128x64 I2C OLED user interface controller for the AstroWrist unit.
 *
 * Renders:
 * - Top bar: Operating Mode ([MODULE] or [EVA]), animated heart icon + BPM, battery level.
 * - Center HUD: Large current radiation dose rate (uSv/h), true CPM, and NASA DONKI SEP badge.
 * - Bottom section: Accumulated dose progress bar (mSv vs 250 mSv NASA 30-day limit),
 *   Time-to-Limit (hours), and full-screen hardware inverse blinking on CRITICAL_ABORT.
 */
class WristDisplay {
public:
    WristDisplay()
        : display_(OLED_WIDTH, OLED_HEIGHT, &Wire, OLED_RESET_PIN),
          oledOnline_(false),
          activePage_(0),
          blinkToggle_(false),
          heartAnimToggle_(false),
          lastRefreshMs_(0) {}

    /**
     * @brief Initializes the SSD1306 128x64 OLED display over I2C (address 0x3C).
     * @return true if display responded on the I2C bus.
     */
    bool begin() {
        if (!display_.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDR)) {
            oledOnline_ = false;
            return false;
        }
        oledOnline_ = true;
        display_.clearDisplay();
        display_.setTextColor(SSD1306_WHITE);
        display_.setTextWrap(false);
        renderBootSplash();
        return true;
    }

    /**
     * @brief Cycles between Primary Dosimetry HUD (Page 0) and Detailed NASA STD-3001
     *        Biodosimetry / Space Weather screen (Page 1).
     */
    void nextPage() {
        activePage_ = (activePage_ + 1) % 2;
    }

    uint8_t getActivePage() const {
        return activePage_;
    }

    bool isOnline() const {
        return oledOnline_;
    }

    /**
     * @brief Renders the startup splash screen showing firmware info and NASA STD-3001 compliance.
     */
    void renderBootSplash() {
        if (!oledOnline_) {
            return;
        }
        display_.clearDisplay();
        display_.drawRect(0, 0, OLED_WIDTH, OLED_HEIGHT, SSD1306_WHITE);
        display_.setTextSize(1);
        display_.setCursor(8, 6);
        display_.print(F("ASTROWRIST DOSIMETER"));
        display_.drawFastHLine(4, 16, 120, SSD1306_WHITE);
        display_.setCursor(8, 22);
        display_.print(F("Tube: J305 + MAX30102"));
        display_.setCursor(8, 33);
        display_.print(F("Baro: BMP280 Auto-EVA"));
        display_.setCursor(8, 44);
        display_.print(F("Std : NASA-STD-3001"));
        display_.setCursor(8, 54);
        display_.print(F("Link: NASA DONKI API"));
        display_.display();
    }

    /**
     * @brief Periodic non-blocking UI update (runs every DISPLAY_REFRESH_MS = 250 ms).
     */
    void render(
        uint32_t nowMs,
        const GeigerCounter& geiger,
        const HealthMonitor& health,
        const NasaDonkiSpaceWeather& donki
    ) {
        if (!oledOnline_) {
            return;
        }
        if ((nowMs - lastRefreshMs_) < DISPLAY_REFRESH_MS) {
            return;
        }
        lastRefreshMs_ = nowMs;

        heartAnimToggle_ = !heartAnimToggle_;

        // Hardware-level inverse screen flashing when crew status is CRITICAL_ABORT
        if (health.getCrewStatus() == CRITICAL_ABORT) {
            blinkToggle_ = !blinkToggle_;
            display_.invertDisplay(blinkToggle_);
        } else {
            blinkToggle_ = false;
            display_.invertDisplay(false);
        }

        display_.clearDisplay();

        // 1. Render Top Status Bar (common to both screens)
        drawTopBar(health);

        // 2. Render either Primary Wrist HUD (Page 0) or Secondary Diagnostic Page (Page 1)
        if (activePage_ == 0) {
            drawPrimaryCenterSection(geiger, health, donki);
            drawPrimaryBottomSection(geiger);
        } else {
            drawDiagnosticPage(geiger, health, donki);
        }

        display_.display();
    }

private:
    /**
     * @brief Draws top bar (y = 0..10):
     *        Left: [MODULE] or [EVA]
     *        Center: Animated Heart Icon + BPM
     *        Right: Battery Icon + %
     */
    void drawTopBar(const HealthMonitor& health) {
        display_.setTextSize(1);
        display_.setCursor(0, 1);

        // Mode indicator ([MODULE] or inverted high-contrast [EVA])
        if (health.getOperatingMode() == EVA_MODE) {
            display_.fillRect(0, 0, 31, 9, SSD1306_WHITE);
            display_.setTextColor(SSD1306_BLACK);
            display_.setCursor(1, 1);
            display_.print(F("[EVA]"));
            display_.setTextColor(SSD1306_WHITE);
        } else {
            display_.setCursor(0, 1);
            display_.print(F("[MODULE]"));
        }

        // Heart icon + Heart rate BPM in the center-right of top bar
        const uint8_t heartX = 52;
        const uint8_t* heartBmp = heartAnimToggle_ ? BMP_HEART_IDLE : BMP_HEART_PULSE;
        display_.drawBitmap(heartX, 1, heartBmp, 8, 7, SSD1306_WHITE);

        display_.setCursor(heartX + 10, 1);
        int bpmRounded = static_cast<int>(health.getHeartRateBpm() + 0.5f);
        display_.print(bpmRounded);

        // Battery indicator on top right (x = 96..127)
        uint8_t battPct = health.getBatteryPercent();
        display_.drawRect(108, 1, 17, 7, SSD1306_WHITE);
        display_.fillRect(125, 3, 2, 3, SSD1306_WHITE); // Battery positive terminal nub
        uint8_t fillWidth = static_cast<uint8_t>((static_cast<uint16_t>(battPct) * 13U) / 100U);
        if (fillWidth > 0) {
            display_.fillRect(110, 3, fillWidth, 3, SSD1306_WHITE);
        }

        // Thin horizontal separator line below top bar
        display_.drawFastHLine(0, 10, OLED_WIDTH, SSD1306_WHITE);
    }

    /**
     * @brief Draws Center Section (y = 13..41):
     *        Large current dose rate (uSv/h), CPM counter, and active warning/DONKI badges.
     */
    void drawPrimaryCenterSection(
        const GeigerCounter& geiger,
        const HealthMonitor& health,
        const NasaDonkiSpaceWeather& donki
    ) {
        float uSvh = geiger.getDoseRateUsvh();
        float cpm  = geiger.getTrueCPM();

        // Format dose rate with dynamic precision so it always fits cleanly in textSize(2)
        char doseBuf[12];
        if (uSvh < 10.0f) {
            snprintf(doseBuf, sizeof(doseBuf), "%.2f", uSvh);
        } else if (uSvh < 100.0f) {
            snprintf(doseBuf, sizeof(doseBuf), "%.1f", uSvh);
        } else {
            snprintf(doseBuf, sizeof(doseBuf), "%.0f", uSvh);
        }

        // Large radiation figures on left-center
        display_.setTextSize(2);
        display_.setCursor(0, 14);
        display_.print(doseBuf);

        // Unit label and status badge on the right side
        display_.setTextSize(1);
        display_.setCursor(66, 14);
        display_.print(F("uSv/h"));

        // Show NASA DONKI Solar Storm warning badge or Crew Status badge at top-right of center
        if (donki.uplink_connected && donki.sep_storm) {
            display_.fillRect(98, 13, 30, 9, SSD1306_WHITE);
            display_.setTextColor(SSD1306_BLACK);
            display_.setCursor(100, 14);
            display_.print(F("SEP!"));
            display_.setTextColor(SSD1306_WHITE);
        } else {
            display_.setCursor(98, 14);
            switch (health.getCrewStatus()) {
                case NOMINAL:           display_.print(F("NOM"));  break;
                case RADIATION_WARNING: display_.print(F("WARN")); break;
                case BIOMETRIC_STRESS:  display_.print(F("BIOM")); break;
                case CRITICAL_ABORT:    display_.print(F("ABRT")); break;
            }
        }

        // Sub-row in center: True Dead-Time-Corrected CPM & Ambient Pressure
        display_.setCursor(0, 32);
        display_.print(F("CPM:"));
        display_.print(static_cast<uint32_t>(cpm + 0.5f));

        display_.setCursor(64, 32);
        display_.print(static_cast<int>(health.getPressureHpa() + 0.5f));
        display_.print(F("hPa"));
    }

    /**
     * @brief Draws Bottom Section (y = 43..63):
     *        Accumulated dose progress bar (mSv vs NASA 30-day limit) & Time-to-Limit (TTL).
     */
    void drawPrimaryBottomSection(const GeigerCounter& geiger) {
        // Progress bar outline (x = 0..127, y = 43..51)
        const uint8_t barY = 43;
        const uint8_t barH = 8;
        display_.drawRect(0, barY, OLED_WIDTH, barH, SSD1306_WHITE);

        float pct = geiger.get30DayLimitPercent();
        // Ensure even small non-zero accumulated doses show at least 2px progress indicator
        uint8_t fillPx = static_cast<uint8_t>((pct * static_cast<float>(OLED_WIDTH - 4)) / 100.0f);
        if (geiger.getAccumulatedMsv() > 0.001f && fillPx < 2) {
            fillPx = 2;
        }
        if (fillPx > (OLED_WIDTH - 4)) {
            fillPx = OLED_WIDTH - 4;
        }
        if (fillPx > 0) {
            display_.fillRect(2, barY + 2, fillPx, barH - 4, SSD1306_WHITE);
        }

        // Bottom text row (y = 54): Accumulated dose (mSv) on left, Time-to-Limit (h) on right
        display_.setTextSize(1);
        display_.setCursor(0, 55);
        char accBuf[16];
        if (geiger.getAccumulatedMsv() < 10.0f) {
            snprintf(accBuf, sizeof(accBuf), "%.3fmSv", geiger.getAccumulatedMsv());
        } else {
            snprintf(accBuf, sizeof(accBuf), "%.1fmSv", geiger.getAccumulatedMsv());
        }
        display_.print(accBuf);

        // Time-to-Limit (hours) right-aligned
        float ttlHours = geiger.getTimeToLimitHours();
        char ttlBuf[16];
        if (ttlHours >= 999.0f) {
            snprintf(ttlBuf, sizeof(ttlBuf), "TTL:>999h");
        } else {
            snprintf(ttlBuf, sizeof(ttlBuf), "TTL:%.1fh", ttlHours);
        }
        display_.setCursor(68, 55);
        display_.print(ttlBuf);
    }

    /**
     * @brief Secondary diagnostic screen (Page 1) accessible via PIN_MODE_BTN (GPIO 0).
     *        Displays SpO2, BMP280 pressure, NASA-STD-3001 limits, and DONKI space weather.
     */
    void drawDiagnosticPage(
        const GeigerCounter& geiger,
        const HealthMonitor& health,
        const NasaDonkiSpaceWeather& donki
    ) {
        display_.setTextSize(1);

        display_.setCursor(0, 14);
        display_.print(F("SpO2:"));
        display_.print(static_cast<int>(health.getSpO2Percent() + 0.5f));
        display_.print(F("% P:"));
        display_.print(health.getPressureHpa(), 0);
        display_.print(F("hPa"));

        display_.setCursor(0, 24);
        display_.print(F("30d :"));
        display_.print(geiger.getAccumulatedMsv(), 2);
        display_.print(F("/"));
        display_.print(NASA_30DAY_LIMIT_MSV, 0);
        display_.print(F("mSv"));

        display_.setCursor(0, 34);
        display_.print(F("Car :"));
        display_.print(geiger.getAccumulatedMsv(), 2);
        display_.print(F("/"));
        display_.print(NASA_CAREER_LIMIT_MSV, 0);
        display_.print(F("mSv"));

        display_.setCursor(0, 44);
        display_.print(F("DONKI SEP:"));
        if (donki.uplink_connected) {
            display_.print(donki.sep_storm ? F("STORM!") : F("QUIET"));
        } else {
            display_.print(F("WAIT"));
        }

        display_.setCursor(0, 54);
        display_.print(F("Flux:"));
        display_.print(donki.solar_flux, 1);
        display_.print(F(" St:"));
        display_.print(healthStatusToString(health.getCrewStatus()));
    }

    Adafruit_SSD1306 display_;
    bool             oledOnline_;
    uint8_t          activePage_;
    bool             blinkToggle_;
    bool             heartAnimToggle_;
    uint32_t         lastRefreshMs_;
};

#endif // ASTROWRIST_WRIST_DISPLAY_UI_H
