#ifndef ASTROWRIST_GEIGER_COUNTER_H
#define ASTROWRIST_GEIGER_COUNTER_H

#include <Arduino.h>
#include "config.h"

// Счётчик импульсов трубки -> оценка CPM за минуту -> пересчёт в мощность дозы.

/**
 * @class GeigerCounter
 * @brief Hardware interrupt-driven Geiger-Mueller dosimetry engine for J305 tube.
 *
 * Implements:
 * 1. IRAM hardware interrupt handler `onParticleHit()` with high-frequency
 *    debounce / dead-time filtering (190 us).
 * 2. 12-bucket x 5-second sliding window ring buffer (60-second total span)
 *    that computes an instantaneous moving CPM every 1 second without waiting
 *    for a full 60-second cycle to elapse.
 * 3. Non-paralyzable Geiger-Mueller dead-time mathematical correction:
 *    true_cpm = raw_cpm / (1.0 - raw_cpm * DEAD_TIME)
 * 4. Dose-rate conversion (uSv/h and mSv/h), numerical Riemann integration of
 *    accumulated dose (accumulated_mSv), and NASA-STD-3001 Time-to-Limit (hours).
 */
class GeigerCounter {
public:
    GeigerCounter()
        : currentBucketIdx_(0),
          completedBuckets_(0),
          secondsInCurrentBucket_(0),
          oldestBucketCarryover_(0),
          windowFull_(false),
          lastUpdateMs_(0),
          bucketStartMs_(0),
          pulsesLastSecond_(0),
          raw_cpm_(0.0f),
          true_cpm_(0.0f),
          uSv_h_(0.0f),
          mSv_h_(0.0f),
          accumulated_mSv_(0.0f),
          time_to_limit_hours_(9999.9f),
          simulatedExtraCpm_(0.0f) {
        for (uint8_t i = 0; i < GEIGER_BUCKET_COUNT; ++i) {
            buckets_[i] = 0;
        }
    }

    /**
     * @brief Initializes GPIO interrupt pin for the J305 high-voltage Geiger module.
     * @param initialAccumulatedMsv Previously stored accumulated dose from NVS flash (mSv).
     */
    void begin(float initialAccumulatedMsv = 0.0f) {
        accumulated_mSv_ = (initialAccumulatedMsv >= 0.0f) ? initialAccumulatedMsv : 0.0f;

        pinMode(PIN_GEIGER_PULSE, INPUT_PULLUP);

        uint32_t nowMs = millis();
        lastUpdateMs_  = nowMs;
        bucketStartMs_ = nowMs;

        // Reset volatile ISR state before attaching hardware interrupt
        noInterrupts();
        s_isrPulseCount        = 0;
        s_totalLifetimePulses  = 0;
        s_rejectedBouncePulses = 0;
        s_lastPulseMicros      = micros();
        s_particleClickPending = false;
        interrupts();

        // J305 cathode follower / CAJOE module outputs active-LOW falling-edge pulses
        attachInterrupt(digitalPinToInterrupt(PIN_GEIGER_PULSE), GeigerCounter::onParticleHit, FALLING);
    }

    /**
     * @brief Hardware Interrupt Service Routine (ISR) placed in ESP32 Internal RAM (IRAM).
     * Filters out high-frequency electrical ringing/bounce below the 190 us J305 dead time.
     */
    static void IRAM_ATTR onParticleHit() {
        uint32_t nowMicros = micros();
        // Unsigned subtraction handles 32-bit micros() overflow (~71.5 minutes) seamlessly
        if ((nowMicros - s_lastPulseMicros) >= DEAD_TIME_US) {
            s_isrPulseCount        = s_isrPulseCount + 1;
            s_totalLifetimePulses  = s_totalLifetimePulses + 1;
            s_lastPulseMicros      = nowMicros;
            s_particleClickPending = true;
        } else {
            s_rejectedBouncePulses = s_rejectedBouncePulses + 1;
        }
    }

    /**
     * @brief Consumes and clears the single-particle click flag for real-time piezo audio feedback.
     * @return true if one or more ionizing particles hit the tube since the last check.
     */
    bool consumeParticleClick() {
        if (s_particleClickPending) {
            noInterrupts();
            s_particleClickPending = false;
            interrupts();
            return true;
        }
        return false;
    }

    /**
     * @brief Injects synthetic pulses (e.g., during NASA DONKI SEP storm simulation or ground testing).
     * @param extraPulses Number of ionization counts to add into the current 1-second slice.
     */
    void injectTestPulses(uint32_t extraPulses) {
        if (extraPulses == 0) {
            return;
        }
        noInterrupts();
        s_isrPulseCount       = s_isrPulseCount + extraPulses;
        s_totalLifetimePulses = s_totalLifetimePulses + extraPulses;
        s_particleClickPending = true;
        interrupts();
    }

    /**
     * @brief Sets an optional background/storm bias CPM from spacecraft DONKI simulation uplink.
     */
    void setSimulatedStormCpmBias(float extraCpm) {
        simulatedExtraCpm_ = (extraCpm >= 0.0f) ? extraCpm : 0.0f;
    }

    /**
     * @brief Executes 1-second sliding window update, dead-time correction, and dose integration.
     * Should be called continuously from main loop; executes every GEIGER_UPDATE_INTERVAL_MS (1000 ms).
     * @param nowMs Current system timestamp from millis().
     * @return true if a 1-second dosimetry calculation cycle was completed on this call.
     */
    bool update(uint32_t nowMs) {
        uint32_t dtMs = nowMs - lastUpdateMs_;
        if (dtMs < GEIGER_UPDATE_INTERVAL_MS) {
            return false;
        }
        lastUpdateMs_ = nowMs;

        // 1. Atomically harvest pulses captured by the IRAM ISR during the last second
        uint32_t harvestedPulses = 0;
        noInterrupts();
        harvestedPulses = s_isrPulseCount;
        s_isrPulseCount = 0;
        interrupts();

        pulsesLastSecond_ = harvestedPulses;

        // 2. Update the 12-bucket x 5-second sliding window ring buffer
        buckets_[currentBucketIdx_] += harvestedPulses;
        secondsInCurrentBucket_++;

        // Advance to next 5-second bucket if 5000 ms (or 5 one-second ticks) have elapsed
        if ((nowMs - bucketStartMs_) >= GEIGER_BUCKET_DURATION_MS || secondsInCurrentBucket_ >= 5) {
            bucketStartMs_ = nowMs;
            if (completedBuckets_ < (GEIGER_BUCKET_COUNT - 1)) {
                completedBuckets_++;
            } else {
                windowFull_ = true;
            }

            currentBucketIdx_      = (currentBucketIdx_ + 1) % GEIGER_BUCKET_COUNT;
            oldestBucketCarryover_ = buckets_[currentBucketIdx_];
            buckets_[currentBucketIdx_] = 0;
            secondsInCurrentBucket_     = 0;
        }

        // 3. Calculate instantaneous sliding-window raw_cpm every second without waiting 60s
        uint32_t activeWindowSum = 0;
        for (uint8_t i = 0; i < GEIGER_BUCKET_COUNT; ++i) {
            activeWindowSum += buckets_[i];
        }

        if (!windowFull_) {
            uint32_t elapsedWindowSec = (static_cast<uint32_t>(completedBuckets_) * 5U) + secondsInCurrentBucket_;
            if (elapsedWindowSec == 0) {
                elapsedWindowSec = 1;
            }
            // Extrapolate partial window (< 60s since boot) to counts per minute
            raw_cpm_ = (static_cast<float>(activeWindowSum) / static_cast<float>(elapsedWindowSec)) * 60.0f;
        } else {
            // Smooth 60.0-second sliding window: 11 completed buckets (55s) + current partial bucket (s sec)
            // + remaining fraction ((5 - s) / 5) of the oldest bucket being overwritten
            float oldestWeight = static_cast<float>(5U - secondsInCurrentBucket_) / 5.0f;
            if (oldestWeight < 0.0f) {
                oldestWeight = 0.0f;
            }
            raw_cpm_ = static_cast<float>(activeWindowSum) + (static_cast<float>(oldestBucketCarryover_) * oldestWeight);
        }

        // Add optional simulated solar storm CPM bias if triggered by uplink test
        raw_cpm_ += simulatedExtraCpm_;

        // 4. Mathematical Dead-Time Correction for Geiger-Mueller Tube J305:
        // Formula: true_cpm = raw_cpm / (1.0 - raw_cpm * DEAD_TIME)
        // where DEAD_TIME is in minutes (190 us = 190e-6 / 60.0 min) to match CPM units.
        constexpr float DEAD_TIME = DEAD_TIME_MINUTES;
        float deadTimeFactor = 1.0f - (raw_cpm_ * DEAD_TIME);
        if (deadTimeFactor < 0.05f) {
            // Clamp denominator at 0.05 to avoid singularity during extreme tube saturation
            deadTimeFactor = 0.05f;
        }
        true_cpm_ = raw_cpm_ / deadTimeFactor;

        // 5. Convert corrected CPM to ambient dose equivalent rate (uSv/h and mSv/h)
        uSv_h_ = true_cpm_ * J305_CONVERSION_FACTOR;
        mSv_h_ = uSv_h_ / 1000.0f;

        // 6. Numerical integration of accumulated dose over time step dt (in hours)
        float dtHours = static_cast<float>(dtMs) / 3600000.0f;
        accumulated_mSv_ += mSv_h_ * dtHours;

        // 7. Compute remaining safe working time (Time-to-Limit in hours) per NASA-STD-3001 30-day BFO limit
        float remainingMsv = NASA_30DAY_LIMIT_MSV - accumulated_mSv_;
        if (remainingMsv <= 0.0f) {
            time_to_limit_hours_ = 0.0f;
        } else if (mSv_h_ > 0.000001f) {
            time_to_limit_hours_ = remainingMsv / mSv_h_;
            if (time_to_limit_hours_ > 9999.9f) {
                time_to_limit_hours_ = 9999.9f;
            }
        } else {
            time_to_limit_hours_ = 9999.9f;
        }

        return true;
    }

    // Reset accumulated dose (e.g., mission reset via long button press or serial command)
    void resetAccumulatedDose() {
        accumulated_mSv_ = 0.0f;
        time_to_limit_hours_ = 9999.9f;
    }

    void setAccumulatedDoseMsv(float doseMsv) {
        accumulated_mSv_ = (doseMsv >= 0.0f) ? doseMsv : 0.0f;
    }

    // Accessors for telemetry and UI rendering
    float    getRawCPM()            const { return raw_cpm_; }
    float    getTrueCPM()           const { return true_cpm_; }
    float    getDoseRateUsvh()      const { return uSv_h_; }
    float    getDoseRateMsvh()      const { return mSv_h_; }
    float    getAccumulatedMsv()    const { return accumulated_mSv_; }
    float    getTimeToLimitHours()  const { return time_to_limit_hours_; }
    uint32_t getPulsesLastSecond()  const { return pulsesLastSecond_; }
    uint32_t getTotalPulses()       const { return s_totalLifetimePulses; }
    uint32_t getRejectedBounces()   const { return s_rejectedBouncePulses; }
    uint8_t  getCurrentBucketIdx()  const { return currentBucketIdx_; }

    float get30DayLimitPercent() const {
        float pct = (accumulated_mSv_ / NASA_30DAY_LIMIT_MSV) * 100.0f;
        return (pct > 100.0f) ? 100.0f : pct;
    }

    float getCareerLimitPercent() const {
        float pct = (accumulated_mSv_ / NASA_CAREER_LIMIT_MSV) * 100.0f;
        return (pct > 100.0f) ? 100.0f : pct;
    }

private:
    // Volatile variables modified inside IRAM hardware interrupt handler
    static volatile uint32_t s_isrPulseCount;
    static volatile uint32_t s_totalLifetimePulses;
    static volatile uint32_t s_rejectedBouncePulses;
    static volatile uint32_t s_lastPulseMicros;
    static volatile bool     s_particleClickPending;

    // 12-bucket x 5-second ring buffer state
    uint32_t buckets_[GEIGER_BUCKET_COUNT];
    uint8_t  currentBucketIdx_;
    uint8_t  completedBuckets_;
    uint8_t  secondsInCurrentBucket_;
    uint32_t oldestBucketCarryover_;
    bool     windowFull_;

    // Timing and calculated dosimetry state
    uint32_t lastUpdateMs_;
    uint32_t bucketStartMs_;
    uint32_t pulsesLastSecond_;

    float    raw_cpm_;
    float    true_cpm_;
    float    uSv_h_;
    float    mSv_h_;
    float    accumulated_mSv_;
    float    time_to_limit_hours_;
    float    simulatedExtraCpm_;
};

// Static member definitions for header-only inclusion
volatile uint32_t GeigerCounter::s_isrPulseCount        = 0;
volatile uint32_t GeigerCounter::s_totalLifetimePulses  = 0;
volatile uint32_t GeigerCounter::s_rejectedBouncePulses = 0;
volatile uint32_t GeigerCounter::s_lastPulseMicros      = 0;
volatile bool     GeigerCounter::s_particleClickPending = false;

#endif // ASTROWRIST_GEIGER_COUNTER_H
