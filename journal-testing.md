# Ironsight — IEEE Journal Testing Plan

## Objective

Evaluate the impact of **target material**, **sensor count**, and **hardware topology** on arrow impact localization accuracy using the Ironsight accelerometer-based detection system. Each configuration is tested under controlled conditions to isolate individual variable effects.

---

## Test Configurations

| ID | Target Material | Sensors | Layout | Hardware | Purpose |
|----|----------------|---------|--------|----------|---------|
| **T1** | Straw (126 cm Ø) | 4 | Cardinal (N/W/S/E) | 1 Pico W — SPI | **Baseline** |
| **T2** | Compressed foam cube (1 m³) | 4 | Cardinal (N/W/S/E) | 1 Pico W — SPI | Isolate material effect |
| **T3** | Straw (126 cm Ø) | 8 | Octagonal (+ NE/NW/SE/SW) | 2 Pico W — SPI | Isolate sensor count effect |
| **T4** | Compressed foam cube (1 m³) | 8 | Octagonal | 2 Pico W — SPI | Combined: material + sensors |
| **T5** | Compressed foam cube (1 m³) | 8 | Octagonal | 1 Pico W — I2C + 2× TCA9548A MUX | Hardware topology comparison |
| **T6** | Compressed foam cube (1 m³) | 8 | Octagonal | 2 Pico W — I2C + MUX | Alt topology |

### Pairwise Comparisons

| Comparison | Configs | Isolated Variable |
|------------|---------|-------------------|
| Material effect (4-sensor) | T1 vs T2 | Target material |
| Material effect (8-sensor) | T3 vs T4 | Target material |
| Sensor count effect (straw) | T1 vs T3 | Sensor count |
| Sensor count effect (foam) | T2 vs T4 | Sensor count |
| Hardware topology (SPI vs I2C-MUX, single Pico) | T4 vs T5 | Interface protocol |
| Hardware topology (SPI vs I2C-MUX, dual Pico) | T4 vs T6 | Interface protocol |
| Single vs dual Pico (I2C) | T5 vs T6 | MCU count |

---

## Experimental Protocol

### Constants (held fixed across all tests)
- Same archer
- Same bow and arrows
- Indoor environment (controlled temperature, no wind)
- Consistent lighting (for camera-based posture analysis)
- ADXL345 sensors configured at ±4g, 3200 Hz ODR
- Sensors surface-mounted on target face

### Per-Configuration Protocol
1. Mount sensors in the specified layout; verify all channels responding
2. Run system calibration procedure (click-based ground truth)
3. Shoot **30 arrows × 3 sessions** (90 total shots per configuration)
4. For each arrow:
   - System records hit automatically (all 39 CSV columns)
   - Manually measure and record ground-truth (x, y) position in cm from center
   - Photograph the target at the end of each 3-arrow end
5. Allow full refractory period (500 ms) between shots
6. Pull arrows only between ends (every 3 arrows)

### Ground Truth Collection
- Use a measuring tape or grid overlay on the target face
- Measure from target center to arrow shaft entry point
- Record to ±0.5 cm precision
- Two independent measurements per arrow (average for ground truth)

---

## Sensor Layouts

### 4-Sensor Cardinal Layout (T1, T2)
```
        N (0°)
        |
  W ----+---- E
  (270°)|  (90°)
        S (180°)
```
Sensors evenly spaced at 90° intervals on the target perimeter.

### 8-Sensor Octagonal Layout (T3, T4, T5, T6)
```
        N (0°)
    NW  |  NE
     \  |  /
  W ---+--- E
     /  |  \
    SW  |  SE
        S (180°)
```
Sensors evenly spaced at 45° intervals on the target perimeter.

---

## Hardware Setup Details

### T1, T2 — Single Pico W, SPI (4 sensors)
- Existing `main_spi.py` firmware, no modifications needed
- CS pins: GP21 (N), GP20 (W), GP17 (S), GP22 (E)
- Single UDP stream to backend

### T3, T4 — Dual Pico W, SPI (8 sensors)
- **Pico A**: 4 cardinal sensors (N/W/S/E) — existing firmware
- **Pico B**: 4 diagonal sensors (NE/NW/SE/SW) — same firmware, different node ID
- Synchronization: shared GPIO interrupt line or NTP-based alignment
- Backend merges two UDP streams by sequence + timestamp correlation
- Each Pico sends its own `hit_bundle`; backend fuses into single 8-channel event

### T5 — Single Pico W, I2C + 2× MUX (8 sensors)
- Two TCA9548A multiplexers (addresses 0x70, 0x71)
- 4 ADXL345 per MUX (alternating 0x53 / 0x1D addresses)
- Modified `main.py` firmware to scan both MUX addresses
- Single UDP stream with 8-channel bundles
- Concern: I2C bus speed may limit effective polling rate

### T6 — Dual Pico W, I2C + MUX (8 sensors)
- **Pico A**: 1× TCA9548A + 4 sensors (cardinal)
- **Pico B**: 1× TCA9548A + 4 sensors (diagonal)
- Uses existing `main.py` firmware per Pico
- Backend merges two UDP streams

---

## Metrics

### Primary Metrics
| Metric | Unit | Description |
|--------|------|-------------|
| **Localization MAE** | cm | Mean Absolute Error vs ground truth |
| **Localization RMSE** | cm | Root Mean Square Error vs ground truth |
| **Scoring accuracy** | % | Correct ring classification rate |
| **Detection rate** | % | Hits detected / arrows shot |

### Secondary Metrics
| Metric | Unit | Description |
|--------|------|-------------|
| False positive rate | count/session | Detections without an arrow impact |
| False negative rate | count/session | Missed arrow impacts |
| SNR per channel | dB | Signal-to-noise ratio of impact peaks |
| Energy distribution | — | Dominance ratio across sensors |
| TDOA spread | μs | Variance in time-of-arrival differences |
| Wave speed | m/s | Measured propagation speed in target material |
| Detection latency | ms | Time from impact to scored result |
| Energy confidence | 0–1 | `energy_conf` from localization engine |
| TDOA confidence | 0–1 | `tdoa_conf` from localization engine |

---

## Data Collection

### Existing CSV Columns (39 fields)
The system already logs all sensor data, timing, energy, TDOA, localization, and scoring per hit. No changes to logging format are needed for T1/T2.

### Additional Columns Needed
| Column | Type | Description |
|--------|------|-------------|
| `test_config_id` | string | T1–T6 identifier |
| `ground_truth_x` | float | Manually measured x position (cm) |
| `ground_truth_y` | float | Manually measured y position (cm) |
| `ground_truth_r` | float | Computed distance from center (cm) |
| `ground_truth_ring` | int | True scoring ring (0–10, X) |

### For 8-sensor configs, extend per-channel fields:
- `peak_NE`, `energy_NE`, `energy2_NE`, `tdoa_NE_us` (and NW, SE, SW)

### File Naming Convention
```
arrow_hits_T1_YYYY-MM-DD_session1.csv
arrow_hits_T2_YYYY-MM-DD_session1.csv
...
```

---

## Statistical Analysis Plan

### Descriptive Statistics
- Per-config: mean, median, std, min, max for all primary metrics
- Box plots of localization error by configuration
- Scatter plots of estimated vs ground-truth positions

### Inferential Statistics
- **One-way ANOVA** across all 6 configurations for localization RMSE
- **Paired t-tests** for isolated variable comparisons (e.g., T1 vs T2 for material)
- **Significance level**: α = 0.05
- **Effect size**: Cohen's d for pairwise comparisons

### Visualizations for Paper
- Bland-Altman plots (estimated vs ground truth agreement)
- Heatmaps of error distribution on target face
- Bar charts with error bars comparing configs
- CDF plots of localization error per configuration
- Confusion matrices for scoring accuracy (predicted ring vs actual ring)

---

## Software Modifications Required

| Component | Change | Configs Affected |
|-----------|--------|------------------|
| `config.py` | Add `TEST_CONFIG_ID` parameter; allow runtime target diameter override (100 cm for foam cube) | All |
| `localization.py` | Extend energy-ratio and TDOA formulas for 8-channel input; recalibrate OLS coefficients per material | T3–T6 |
| `udp_listener.py` | Support merging `hit_bundle` packets from two Pico nodes (correlate by timestamp window) | T3, T4, T6 |
| `main_spi.py` | Create variant with 8 CS pins for single-Pico 8-sensor SPI (if feasible given GPIO count) | — (not used; dual Pico instead) |
| `main.py` | Extend to scan two MUX addresses (0x70 + 0x71) | T5 |
| CSV logger | Add `test_config_id`, `ground_truth_x/y/r/ring` columns | All |
| Dashboard | Add ground-truth input UI (or accept manual CSV annotation post-session) | All |

---

## Execution Order

1. **T1** — Baseline with current system (straw, 4 sensors, SPI). Minimal setup.
2. **T2** — Swap target to foam cube, keep everything else identical. Reveals material impact.
3. **T3** — Back to straw, add 4 diagonal sensors with second Pico. Reveals sensor count impact.
4. **T4** — Foam cube + 8 sensors + dual Pico SPI. Full upgrade test.
5. **T5** — Foam cube + 8 sensors on single Pico via I2C MUX. Tests hardware consolidation.
6. **T6** — Foam cube + 8 sensors, dual Pico via I2C MUX. Tests I2C in distributed setup.

---

## Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Foam cube has very different wave speed than straw | TDOA calibration invalid | Measure wave speed empirically before test; update `TDOA_WAVE_SPEED` in config |
| Dual-Pico clock drift causes TDOA errors | Degraded timing accuracy | Use shared hardware interrupt line; or rely on energy-only localization |
| I2C bus at 140 kHz too slow for 8 sensors | Missed samples, lower effective ODR | Fall back to SPI-based configs; document limitation |
| Arrow damage to foam cube changes properties over time | Drift in results across sessions | Rotate arrow zones; replace cube if >50 hits in same area |
| Ground truth measurement error | Inflated localization error | Two independent measurements; use grid overlay |
