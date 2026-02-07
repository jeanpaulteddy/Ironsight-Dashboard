# backend/udp_listener.py
import asyncio, json, math, time, csv
from math import log
from typing import Dict, Any, Callable, Optional
from datetime import datetime
from pathlib import Path
import os
try:
    from mode_state import get_mode as default_get_mode
except Exception:
    default_get_mode = None

# Target geometry: sensors are 63cm from center (N, W, S, E positions)
D_CM = 126.0  # diameter in cm
HALF_SPAN = D_CM / 2.0  # 63cm - distance from center to sensor

def extract_compass_peaks(msg: Dict[str, Any], ch2comp: Dict[str, str]) -> Dict[str, float]:
    ch = msg.get("ch", {})
    # Prefer squared energy when available (energy2), then linear energy, then raw peak.
    peaks_by_ch = {k: float(v.get("energy2", v.get("energy", v.get("peak", 0.0)))) for k, v in ch.items()}

    out = {"N": 0.0, "E": 0.0, "W": 0.0, "S": 0.0}
    for ch_str, pk in peaks_by_ch.items():
        comp = ch2comp.get(ch_str)
        if comp:
            out[comp] = float(pk)
    return out

def features_from_peaks(pN: float, pE: float, pW: float, pS: float):
    eps = 1e-12
    sx = (pE - pW) / (pE + pW + eps)
    sy = (pN - pS) / (pN + pS + eps)
    return sx, sy

# ---------- TDOA LOCALIZATION ----------
# Wave speed in straw target (m/s) - tune based on actual measurements
# Observed: ~12000µs max timing diff across 1.3m target → ~100 m/s
TDOA_WAVE_SPEED = 100.0  # m/s
TDOA_ENABLED = True
TARGET_DIAMETER_CM = 126.0  # Sensor span = 2 * 63cm

def tdoa_localize(tdoa_us: Dict[str, int], ch2comp: Dict[str, str], wave_speed: float = TDOA_WAVE_SPEED):
    """
    Compute (sx, sy) features from TDOA timing.

    tdoa_us: {"0": dt_us, "1": dt_us, ...} - microseconds relative to first arrival
    ch2comp: channel to compass mapping {"0": "N", ...}
    wave_speed: estimated wave speed in m/s

    Returns (sx, sy, confidence) - confidence is 0-1 based on timing quality.
    """
    if not tdoa_us or len(tdoa_us) < 4:
        return None, None, 0.0

    # Map channel TDOA to compass directions
    tdoa_comp = {}
    for ch_str, dt_us in tdoa_us.items():
        comp = ch2comp.get(ch_str)
        if comp:
            tdoa_comp[comp] = dt_us

    if len(tdoa_comp) < 4:
        return None, None, 0.0

    # Convert to distance differences (meters)
    # A later arrival means the sensor is FURTHER from the impact
    dN = tdoa_comp.get("N", 0) * 1e-6 * wave_speed
    dW = tdoa_comp.get("W", 0) * 1e-6 * wave_speed
    dS = tdoa_comp.get("S", 0) * 1e-6 * wave_speed
    dE = tdoa_comp.get("E", 0) * 1e-6 * wave_speed

    # Compute normalized ratios
    # If East arrives later than West, impact is closer to West (negative X)
    dx = dE - dW  # positive = impact closer to West
    dy = dN - dS  # positive = impact closer to South

    # Normalize to [-1, 1] range
    # Max distance difference = target diameter (sensors on opposite edges)
    max_diff = TARGET_DIAMETER_CM / 100.0  # convert to meters for TDOA physics
    sx = -dx / max_diff  # flip sign for coordinate system
    sy = -dy / max_diff

    # Compute TDOA confidence based on timing spread and channel quality
    times = list(tdoa_comp.values())
    max_time = max(times)
    min_time = min(times)
    spread = max_time - min_time  # microseconds

    # Count channels at the minimum arrival time (simultaneous = unreliable)
    n_at_zero = sum(1 for t in times if t == min_time)

    expected_max_spread = (TARGET_DIAMETER_CM / 100.0) / wave_speed * 1e6  # ~13000µs

    if n_at_zero >= 3:
        # 3+ channels at same time: broad wavefront, only 1 useful timing channel
        confidence = 0.05
    elif n_at_zero == 2:
        # 2 channels at zero: only moderately useful
        confidence = 0.15
    elif spread < 100:  # All sensors nearly simultaneous
        confidence = 0.1
    elif spread > expected_max_spread * 1.5:  # Physically impossible spread
        confidence = 0.0
    elif spread > expected_max_spread:  # Slightly over expected
        confidence = 0.15
    else:
        # Good spread range — cap at 0.7 so TDOA never fully overrides energy
        confidence = min(0.7, 0.3 + (spread / expected_max_spread) * 0.4)

    # Clamp to valid range
    sx = max(-1.0, min(1.0, sx))
    sy = max(-1.0, min(1.0, sy))

    return sx, sy, confidence


def compute_energy_confidence(comp: Dict[str, float], dom_ratio: float) -> float:
    """
    Compute confidence score for energy-based localization.
    Higher confidence when energy is concentrated and axes are balanced.
    """
    pN, pE, pW, pS = comp.get("N", 0), comp.get("E", 0), comp.get("W", 0), comp.get("S", 0)
    total = pN + pE + pW + pS

    if total < 50:  # Very low energy - unreliable
        return 0.2

    # Check axis balance - both axes should have some energy
    x_axis = pE + pW
    y_axis = pN + pS
    axis_balance = min(x_axis, y_axis) / (max(x_axis, y_axis) + 1e-12)

    # Confidence based on dominance (concentrated impact) and axis balance
    # High dom_ratio = good (concentrated), high axis_balance = good (both axes have signal)
    confidence = 0.3 + 0.4 * dom_ratio + 0.3 * axis_balance

    return min(1.0, max(0.0, confidence))


def fuse_localization(sx_energy: float, sy_energy: float, energy_conf: float,
                      sx_tdoa: Optional[float], sy_tdoa: Optional[float], tdoa_conf: float) -> tuple:
    """
    Intelligent fusion of energy and TDOA localization.
    Energy is preferred — TDOA on this hardware is not reliable enough to override.

    Returns (sx, sy, method_used) where method_used describes the fusion.
    """
    # If TDOA not available, use energy only
    if sx_tdoa is None or sy_tdoa is None:
        return sx_energy, sy_energy, "energy_only"

    # Apply TDOA trust factor: energy is generally more reliable on this hardware
    TDOA_TRUST_FACTOR = 0.5
    tdoa_conf_eff = tdoa_conf * TDOA_TRUST_FACTOR

    # Check agreement between methods
    dx = abs(sx_energy - sx_tdoa)
    dy = abs(sy_energy - sy_tdoa)
    disagreement = math.sqrt(dx*dx + dy*dy)

    # Normalize confidence weights
    total_conf = energy_conf + tdoa_conf_eff
    if total_conf < 0.1:
        # Both low confidence - average them
        return (sx_energy + sx_tdoa) / 2, (sy_energy + sy_tdoa) / 2, "low_conf_avg"

    w_energy = energy_conf / total_conf
    w_tdoa = tdoa_conf_eff / total_conf

    if disagreement < 0.2:
        # Methods agree well - weighted average
        sx = w_energy * sx_energy + w_tdoa * sx_tdoa
        sy = w_energy * sy_energy + w_tdoa * sy_tdoa
        return sx, sy, f"agree_fuse(e={w_energy:.2f},t={w_tdoa:.2f})"

    elif disagreement < 0.5:
        # Moderate disagreement - trust higher confidence more
        penalty = 1.0 - (disagreement - 0.2) / 0.3 * 0.3  # 0-30% penalty
        w_energy *= penalty
        w_tdoa *= penalty
        total = w_energy + w_tdoa
        if total > 0:
            w_energy /= total
            w_tdoa /= total
        sx = w_energy * sx_energy + w_tdoa * sx_tdoa
        sy = w_energy * sy_energy + w_tdoa * sy_tdoa
        return sx, sy, f"disagree_fuse(e={w_energy:.2f},t={w_tdoa:.2f})"

    else:
        # High disagreement: always prefer energy. TDOA on this hardware
        # is not reliable enough to override when methods strongly disagree.
        return sx_energy, sy_energy, f"high_disagree_energy(e_conf={energy_conf:.2f},t_conf={tdoa_conf:.2f})"

# ---------- CSV HIT LOGGING ----------
HIT_LOG_DIR = Path(__file__).parent / "data" / "logs"
HIT_LOG_ENABLED = True

# CSV column headers - comprehensive for analysis
CSV_HEADERS = [
    "date", "time", "seq", "node", "session_id",
    "mode(shooting|calibration)",
    "estimated_x_cm", "estimated_y_cm",
    "fused_sx(-1to1)", "fused_sy(-1to1)",
    "clicked_x_cm(cal_only)", "clicked_y_cm(cal_only)",
    "fusion_method", "energy_confidence(0to1)", "tdoa_confidence(0to1)",
    "energy_sx(-1to1)", "energy_sy(-1to1)",
    "total_energy(sumE2)", "max_peak(raw_accel)", "dominant_ratio(0to1)",
    "tdoa_sx(-1to1)", "tdoa_sy(-1to1)",
    "tdoa_N_microsec(vs_first)", "tdoa_W_microsec(vs_first)",
    "tdoa_S_microsec(vs_first)", "tdoa_E_microsec(vs_first)",
    "energy_N(sumE2)", "energy_W(sumE2)", "energy_S(sumE2)", "energy_E(sumE2)",
    "label(hit|reject)", "classifier_score",
]

def get_log_file_for_date(date_str: str = None) -> Path:
    """Get the log file path for a specific date (YYYY-MM-DD format)."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return HIT_LOG_DIR / f"arrow_hits_{date_str}.csv"

def init_hit_log():
    """Create log directory. Headers are written per-file as needed."""
    if not HIT_LOG_ENABLED:
        return
    HIT_LOG_DIR.mkdir(parents=True, exist_ok=True)

def _ensure_csv_header(file_path: Path):
    """Write CSV header if file doesn't exist or is empty."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        with open(file_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)

def log_hit(evt: Dict[str, Any], mode: str = "shooting", session_id: str = ""):
    """
    Append a hit to today's CSV log file.

    Args:
        evt: Hit event data from UDP listener
        mode: "shooting" or "calibration"
        session_id: Optional session identifier for grouping
    """
    if not HIT_LOG_ENABLED:
        return
    try:
        now = datetime.now()
        log_file = get_log_file_for_date(now.strftime("%Y-%m-%d"))
        _ensure_csv_header(log_file)

        with open(log_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                # Identifiers
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S.%f")[:-3],  # HH:MM:SS.mmm
                evt.get("seq", ""),
                evt.get("node", ""),
                session_id,
                # Mode
                mode,
                # Software estimated position
                round(evt.get("x_m", 0), 1),
                round(evt.get("y_m", 0), 1),
                round(evt.get("sx", 0), 3),
                round(evt.get("sy", 0), 3),
                # Ground truth (empty for shooting, filled by calibration)
                round(evt.get("x_gt"), 1) if evt.get("x_gt") != "" and evt.get("x_gt") is not None else "",
                round(evt.get("y_gt"), 1) if evt.get("y_gt") != "" and evt.get("y_gt") is not None else "",
                # Fusion details
                evt.get("fusion_method", ""),
                round(evt.get("energy_conf", 0), 3),
                round(evt.get("tdoa_conf", 0), 3),
                # Energy features
                round(evt.get("sx_energy", 0), 3),
                round(evt.get("sy_energy", 0), 3),
                round(evt.get("total_energy", 0), 1),
                round(evt.get("max_peak", 0), 1),
                round(evt.get("dom_ratio", 0), 4),
                # TDOA features
                round(evt.get("sx_tdoa", 0) or 0, 3),
                round(evt.get("sy_tdoa", 0) or 0, 3),
                round(evt.get("tdoa_N_us", 0), 1),
                round(evt.get("tdoa_W_us", 0), 1),
                round(evt.get("tdoa_S_us", 0), 1),
                round(evt.get("tdoa_E_us", 0), 1),
                # Per-channel energy
                round(evt.get("energy_N", 0), 1),
                round(evt.get("energy_W", 0), 1),
                round(evt.get("energy_S", 0), 1),
                round(evt.get("energy_E", 0), 1),
                # Classification
                evt.get("label", ""),
                evt.get("score", 0)
            ])
    except Exception as e:
        print(f"[HIT_LOG] Error writing to CSV: {e}")

def log_calibration_confirmation(evt: Dict[str, Any], x_gt: float, y_gt: float, session_id: str = ""):
    """
    Log a calibration shot with ground truth position.
    Called when user confirms where the arrow actually landed.
    """
    evt_with_gt = {**evt, "x_gt": round(x_gt, 4), "y_gt": round(y_gt, 4)}
    log_hit(evt_with_gt, mode="calibration", session_id=session_id)

def xy_from_features(sx: float, sy: float, fit):
    """Map normalized features -> cm. Uses calibration fit when available."""

    if isinstance(fit, dict):
        model = fit.get("model")
        p = fit.get("params", {})

        # 2nd-order polynomial fit (6+ samples)
        if model == "poly2_sxsy":
            try:
                cx = p["x"]  # list of 6 coeffs
                cy = p["y"]  # list of 6 coeffs
                feats = [sx, sy, sx * sy, sx * sx, sy * sy, 1.0]
                x = sum(float(cx[i]) * feats[i] for i in range(6))
                y = sum(float(cy[i]) * feats[i] for i in range(6))
                return x, y
            except Exception:
                pass

        # Linear fit (3-5 samples)
        if model == "linear_sxsy":
            try:
                cx = p["x"]  # list of 3 coeffs
                cy = p["y"]  # list of 3 coeffs
                feats = [sx, sy, 1.0]
                x = sum(float(cx[i]) * feats[i] for i in range(3))
                y = sum(float(cy[i]) * feats[i] for i in range(3))
                return x, y
            except Exception:
                pass

        # Old: affine fit (backwards compatible)
        if model == "affine_sxsy":
            try:
                a = float(p["a"]); b = float(p["b"]); c = float(p["c"])
                d = float(p["d"]); e = float(p["e"]); f = float(p["f"])
                x = a * sx + b * sy + c
                y = d * sx + e * sy + f
                return x, y
            except Exception:
                pass

    # Fallback: original uncalibrated mapping
    x = HALF_SPAN * sx
    y = HALF_SPAN * sy
    return x, y

class UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue, ch2comp: Dict[str, str], mode_getter: Optional[Callable[[], str]] = None, fit_getter: Optional[Callable[[], Any]] = None, cal_getter: Optional[Callable[[], bool]] = None):
        self.queue = queue
        self.ch2comp = ch2comp
        # If a mode getter isn't provided, fall back to mode_state.get_mode (if available)
        self.mode_getter = mode_getter or default_get_mode
        self.fit_getter = fit_getter
        self.cal_getter = cal_getter
        self._last_accept_ts = 0.0

        # duplicate-suppression window (shorter so real consecutive arrows don't get dropped)
        self.cooldown_s = 0.35

        # We treat the incoming per-channel values as ENERGY2 when available (your Pico sends energy2).
        # So `sumE` in logs below is effectively sumE2.
        self.min_energy = 25.0

        # Basic sanity: we still require some dominance and at least one non-trivial channel.
        self.use_dom_gate = True
        self.min_max_energy = 12.0
        self.min_dom_ratio = 0.35  # hard floor; below this is usually diffuse vibration

        # Arrow classifier (tuned from your logs):
        # Accept if:
        #   (A) sumE2 >= 320 and dom >= 0.40
        #   OR
        #   (B) maxPeak >= 340 (big impulse)
        #   OR
        #   (C) sumE2 >= 240 and maxPeak >= 320 and dom >= 0.42
        self.sumE2_A = 320.0
        self.dom_A = 0.40
        self.peak_B = 340.0
        self.sumE2_C = 240.0
        self.peak_C = 320.0
        self.dom_C = 0.42

        # --- Score-based arrow classifier (multi-feature) ---
        # This uses multiple cues instead of a single threshold, and allows calibration
        # to be stricter than shooting.
        self.use_score_classifier = True

        # Score thresholds (tune): calibration should be stricter.
        self.score_thresh_shooting = 10
        self.score_thresh_calibration = 13

        # Feature thresholds for scoring (tuned for 38lb recurve)
        # Energy tiers: noise tops out ~400, real hits are 100k+
        self.score_sumE2_1 = 500.0
        self.score_sumE2_2 = 1000.0
        self.score_sumE2_3 = 5000.0   # strong impact evidence
        # Peak tiers: noise peaks ~290, real hits 500+
        self.score_peak_1 = 350.0
        self.score_peak_2 = 500.0
        self.score_peak_3 = 700.0     # unmistakable impact
        # Dominance tiers (unchanged, work well)
        self.score_dom_1 = 0.45
        self.score_dom_2 = 0.60
        self.score_peak_over = 25.0
        self.score_entropy_max = 1.00
        self.score_top2_ratio = 0.75
        # EMA delta scoring: real hits spike +100k, noise is flat/negative
        self.score_delta_1 = 1000.0
        self.score_delta_2 = 10000.0

        # If True, we will still allow legacy A/B/C as a fallback when score classifier is off.
        self.use_legacy_abc = True

        # Legacy peak gate is too brittle (you have real arrows around ~281-288 and ghosts can exceed that).
        self.use_peak_gate = False
        self.min_peak_abs = 0.0

        self._energy_ema = 0.0
        self._ema_alpha = 0.05
        self.min_jump = 8.0        # (currently not used; delta gating disabled for calibration)

        self.debug_print = True
        self.pretty_print = True
        self.ghost_floor = 10.0    # print smaller events while tuning

        # Initialize CSV hit logging
        init_hit_log()
        
    def datagram_received(self, data: bytes, addr):
        try:
            msg = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception:
            return

        if not (isinstance(msg, dict) and msg.get("type") == "hit_bundle"):
            return

        # Pretty bundle separation
        if getattr(self, "debug_print", False) and getattr(self, "pretty_print", False):
            print("\n" + "=" * 68)

        node = msg.get("node")
        seq = msg.get("seq")
        t_ms = msg.get("t_ms")

        raw_ch = msg.get("ch", {})

        # Stable channel order for readability
        ch_energy = {
            str(i): float(
                raw_ch.get(str(i), {}).get(
                    "energy2", raw_ch.get(str(i), {}).get("energy", raw_ch.get(str(i), {}).get("peak", 0.0))
                )
            )
            for i in range(4)
        }
        ch_peak = {str(i): float(raw_ch.get(str(i), {}).get("peak", 0.0)) for i in range(4)}

        max_peak = max(ch_peak.values()) if ch_peak else 0.0
        max_energy = max(ch_energy.values()) if ch_energy else 0.0
        sum_energy = sum(ch_energy.values()) if ch_energy else 0.0
        dom_ratio = (max_energy / sum_energy) if sum_energy > 1e-9 else 0.0

        # Extra features for robust arrow-vs-ghost classification
        # peak_over: impulse contrast relative to the other sensors
        if ch_peak:
            peaks_sorted = sorted(ch_peak.values())
            peak_median = peaks_sorted[len(peaks_sorted) // 2]
            peak_over = max_peak - peak_median
        else:
            peak_median = 0.0
            peak_over = 0.0

        # Entropy of the energy distribution across sensors (lower => more concentrated)
        if sum_energy > 1e-9:
            ps = [max(v, 0.0) / sum_energy for v in ch_energy.values()]
            entropy = -sum(p * log(p + 1e-12) for p in ps)
        else:
            entropy = 0.0

        # Ratio of top-2 energies to total (higher => concentrated into 1-2 sensors)
        if ch_energy:
            es = sorted([float(v) for v in ch_energy.values()], reverse=True)
            top2_ratio = (es[0] + es[1]) / sum_energy if (len(es) >= 2 and sum_energy > 1e-9) else 0.0
        else:
            top2_ratio = 0.0

        if getattr(self, "debug_print", False):
            hdr = "[BUNDLE]"
            meta = []
            if node is not None:
                meta.append(f"node={node}")
            if seq is not None:
                meta.append(f"seq={seq}")
            if t_ms is not None:
                meta.append(f"t_ms={t_ms}")
            meta.append(f"src={addr[0]}:{addr[1]}")
            print(hdr, " ".join(meta))
            print(f"  ch_energy2: 0={ch_energy['0']:.1f}  1={ch_energy['1']:.1f}  2={ch_energy['2']:.1f}  3={ch_energy['3']:.1f}")
            print(
                f"  ch_peak:   0={ch_peak['0']:.1f}  1={ch_peak['1']:.1f}  2={ch_peak['2']:.1f}  3={ch_peak['3']:.1f}   (max={max_peak:.1f})"
            )
            print(f"  max_energy={max_energy:.1f}  dom_ratio={dom_ratio:.2f}  top2_ratio={top2_ratio:.2f}")
            print(f"  peak_over={peak_over:.1f}  entropy={entropy:.2f}  peak_med={peak_median:.1f}")

        # Compass-mapped energies (these are energy2 in your bundles)
        comp = extract_compass_peaks(msg, self.ch2comp)
        energy = comp["N"] + comp["E"] + comp["W"] + comp["S"]

        # Determine mode early (calibration can be stricter)
        mode = self.mode_getter() if self.mode_getter else None
        mode_s = str(mode).strip().lower() if mode is not None else ""
        is_cal = self.cal_getter() if self.cal_getter else False

        # EMA baseline (keep previous for delta explanation)
        ema_prev = self._energy_ema
        if ema_prev == 0.0:
            # Initialize EMA from the first observed energy
            self._energy_ema = energy
            ema_prev = energy

        delta = energy - ema_prev

        # Always update baseline (EMA)
        self._energy_ema = (1 - self._ema_alpha) * self._energy_ema + self._ema_alpha * energy
        ema_now = self._energy_ema

        # ----------------------
        # Classification
        # ----------------------
        label = "HIT"
        reason = "pass"
        score = 0  # Will be set by score classifier

        # Hard rejects first
        if energy < self.min_energy:
            label = "GHOST"
            reason = f"energy<{self.min_energy:.1f}"
        elif getattr(self, "use_dom_gate", False) and (max_energy < getattr(self, "min_max_energy", 0.0)):
            label = "GHOST"
            reason = f"maxE<{self.min_max_energy:.1f}"
        elif getattr(self, "use_dom_gate", False) and (dom_ratio < getattr(self, "min_dom_ratio", 0.0)) and energy < 10000.0:
            label = "GHOST"
            reason = f"dom<{self.min_dom_ratio:.2f}"
        else:
            # --- Mandatory impulse/size gate ---
            # Prevents arrow removal / slow presses from scoring as HIT.
            # Pass if ANY of these show impact evidence.
            has_impact = (energy >= 300.0) or (max_peak >= 300.0) or (peak_over >= 10.0)

            # Extra small-event reject (helps keep logs clean and blocks weak structured noise)
            if (energy < 200.0) and (max_peak < 300.0) and (peak_over < 10.0):
                label = "GHOST"
                reason = "too_small(sumE2<200 & peak<300 & pOver<10)"
            elif not has_impact:
                label = "GHOST"
                reason = "no_impact(sumE2<300 & peak<300 & pOver<10)"
            elif (max_peak < 320.0) and (energy < 2000.0):
                label = "GHOST"
                reason = f"weak_signal(peak={max_peak:.0f}<320 & sumE2={energy:.0f}<2000)"
            elif is_cal and energy < 5000.0:
                label = "GHOST"
                reason = f"cal_low_energy(sumE2={energy:.0f}<5000)"
            else:
                # Calibration-specific hard requirement
                if is_cal and not ((max_peak >= 320.0) or (energy >= 300.0)):
                    label = "GHOST"
                    reason = "cal_requires(peak>=320 OR sumE2>=300)"
                else:
                    # Multi-feature score classifier
                    score = 0
                    why = []

                    # Energy tiers
                    if energy >= self.score_sumE2_1:
                        score += 2
                        why.append(f"sumE2>={self.score_sumE2_1:.0f}(+2)")
                    if energy >= self.score_sumE2_2:
                        score += 3
                        why.append(f"sumE2>={self.score_sumE2_2:.0f}(+3)")
                    if energy >= self.score_sumE2_3:
                        score += 3
                        why.append(f"sumE2>={self.score_sumE2_3:.0f}(+3)")

                    # Peak tiers
                    if max_peak >= self.score_peak_1:
                        score += 2
                        why.append(f"peak>={self.score_peak_1:.0f}(+2)")
                    if max_peak >= self.score_peak_2:
                        score += 3
                        why.append(f"peak>={self.score_peak_2:.0f}(+3)")
                    if max_peak >= self.score_peak_3:
                        score += 2
                        why.append(f"peak>={self.score_peak_3:.0f}(+2)")

                    # Dominance tiers
                    if dom_ratio >= self.score_dom_1:
                        score += 2
                        why.append(f"dom>={self.score_dom_1:.2f}(+2)")
                    if dom_ratio >= self.score_dom_2:
                        score += 3
                        why.append(f"dom>={self.score_dom_2:.2f}(+3)")

                    if peak_over >= self.score_peak_over:
                        score += 2
                        why.append(f"peakOver>={self.score_peak_over:.0f}(+2)")

                    if entropy <= self.score_entropy_max:
                        score += 2
                        why.append(f"entropy<={self.score_entropy_max:.2f}(+2)")

                    if top2_ratio >= self.score_top2_ratio:
                        score += 2
                        why.append(f"top2>={self.score_top2_ratio:.2f}(+2)")

                    # EMA delta tiers (real hits spike massively above baseline)
                    if delta >= self.score_delta_1:
                        score += 2
                        why.append(f"delta>={self.score_delta_1:.0f}(+2)")
                    if delta >= self.score_delta_2:
                        score += 3
                        why.append(f"delta>={self.score_delta_2:.0f}(+3)")

                    thresh = self.score_thresh_calibration if is_cal else self.score_thresh_shooting

                    if getattr(self, "use_score_classifier", True):
                        if score >= thresh:
                            label = "HIT"
                            reason = f"score={score}/{thresh} " + ",".join(why)
                        else:
                            label = "GHOST"
                            reason = f"score={score}/{thresh} " + ",".join(why)
                    else:
                        # Legacy A/B/C fallback
                        A = (energy >= self.sumE2_A) and (dom_ratio >= self.dom_A)
                        B = (max_peak >= self.peak_B)
                        C = (energy >= self.sumE2_C) and (max_peak >= self.peak_C) and (dom_ratio >= self.dom_C)

                        if A:
                            label, reason = "HIT", f"A(sumE2>={self.sumE2_A:.0f},dom>={self.dom_A:.2f})"
                        elif B:
                            label, reason = "HIT", f"B(peak>={self.peak_B:.0f})"
                        elif C:
                            label, reason = "HIT", f"C(sumE2>={self.sumE2_C:.0f},peak>={self.peak_C:.0f},dom>={self.dom_C:.2f})"
                        else:
                            label, reason = "GHOST", "not_arrow"

        # Low-energy override: reject peak-only false positives
        if label == "HIT" and energy < self.score_sumE2_3 and score < thresh + 5:
            label = "GHOST"
            reason = f"low_energy_override(sumE2={energy:.0f}<{self.score_sumE2_3:.0f},score={score})"

        # Print everything above a floor to avoid spam
        if getattr(self, "debug_print", False) and energy >= getattr(self, "ghost_floor", 0.0):
            print(
                f"[{label}] sumE={energy:6.1f}  maxE={max_energy:5.1f}  dom={dom_ratio:4.2f}  top2={top2_ratio:4.2f}  maxPeak={max_peak:6.1f}  pOver={peak_over:5.1f}  H={entropy:4.2f}  Δ={delta:6.1f}  ema={ema_now:6.1f}  (prev={ema_prev:6.1f})\n"
                f"       reason={reason}  thr(sumE2)={self.min_energy:.1f}  thr(maxE)={self.min_max_energy:.1f}  thr(dom_floor)={self.min_dom_ratio:.2f}  score_thr(shoot)={self.score_thresh_shooting}  score_thr(cal)={self.score_thresh_calibration}  thr(Δ)=disabled\n"
                f"       compass_energy2: N={comp['N']:.1f}  E={comp['E']:.1f}  W={comp['W']:.1f}  S={comp['S']:.1f}"
            )

        # If it’s not a valid hit, stop here
        if label != "HIT":
            return

        # Mode check (accept in shooting + calibration modes)
        if mode is None and not is_cal:
            if getattr(self, "debug_print", False):
                print("[DROP_MODE] mode=None and not calibrating")
            return

        allowed = {"shooting", "scoring"}
        if mode_s not in allowed and not is_cal:
            if getattr(self, "debug_print", False) and energy >= getattr(self, "ghost_floor", 0.0):
                print(f"[DROP_MODE] mode={mode!r}, is_cal={is_cal}")
            return

        # Cooldown (avoid duplicates)
        now = time.time()
        dt = now - self._last_accept_ts
        if dt < self.cooldown_s:
            if getattr(self, "debug_print", False) and energy >= getattr(self, "ghost_floor", 0.0):
                print(f"[DROP_COOLDOWN] sumE={energy:6.1f}  dt={dt:0.3f}s  cooldown={self.cooldown_s:.3f}s")
            return

        # Accept hit (stamp last accept *after* passing all gates)
        self._last_accept_ts = now

        # ----------------------
        # Robust geometry (axis-reliability gated)
        # ----------------------
        pN, pE, pW, pS = comp["N"], comp["E"], comp["W"], comp["S"]
        eps = 1e-12

        # Base ratios ([-1, +1])
        sx_raw = (pE - pW) / (pE + pW + eps)
        sy_raw = (pN - pS) / (pN + pS + eps)

        # Axis energy fractions: how much of total energy supports each axis
        totalE = pN + pE + pW + pS
        x_axis_E = pE + pW
        y_axis_E = pN + pS
        x_frac = (x_axis_E / (totalE + eps))
        y_frac = (y_axis_E / (totalE + eps))

        # If an axis has almost no energy, its ratio is dominated by noise.
        # Blend that axis ratio toward 0 (center) instead of letting it swing wildly.
        x_floor_ratio = 0.10
        y_floor_ratio = 0.10

        def _blend_to_zero(v: float, frac: float, floor: float) -> float:
            if frac <= 0.0:
                return 0.0
            if frac >= floor:
                return max(-1.0, min(1.0, v))
            # Sqrt blend: preserves more signal at intermediate fractions than linear
            w = math.sqrt(frac / floor)
            return max(-1.0, min(1.0, v * w))

        sx_energy = _blend_to_zero(sx_raw, x_frac, x_floor_ratio)
        sy_energy = _blend_to_zero(sy_raw, y_frac, y_floor_ratio)

        # Optional: small deadzone to stabilize near-center noise
        deadzone = 0.03
        if abs(sx_energy) < deadzone:
            sx_energy = 0.0
        if abs(sy_energy) < deadzone:
            sy_energy = 0.0

        # ----------------------
        # TDOA-based localization (using peak-time interpolation for better accuracy)
        # ----------------------
        # Prefer peak_tdoa_us (interpolated) over tdoa_us (interrupt-based)
        tdoa_us = msg.get("peak_tdoa_us", {}) or msg.get("tdoa_us", {})
        sx_tdoa, sy_tdoa, tdoa_conf = None, None, 0.0
        tdoa_comp = {}

        # Log sample counts if available (for debugging waveform capture)
        sample_count = msg.get("sample_count", {})
        if getattr(self, "debug_print", False) and sample_count:
            print(f"[WAVEFORM] samples per channel: {sample_count}")

        if TDOA_ENABLED and tdoa_us and len(tdoa_us) >= 4:
            sx_tdoa, sy_tdoa, tdoa_conf = tdoa_localize(tdoa_us, self.ch2comp)

            # Map TDOA to compass for logging
            for ch_str, dt_us in tdoa_us.items():
                c = self.ch2comp.get(ch_str)
                if c:
                    tdoa_comp[c] = dt_us

            if getattr(self, "debug_print", False):
                tdoa_source = "peak" if msg.get("peak_tdoa_us") else "interrupt"
                print(f"[TDOA-{tdoa_source}] N={tdoa_comp.get('N', 0)}us  W={tdoa_comp.get('W', 0)}us  S={tdoa_comp.get('S', 0)}us  E={tdoa_comp.get('E', 0)}us")
                if sx_tdoa is not None:
                    print(f"       sx_tdoa={sx_tdoa:+.3f}  sy_tdoa={sy_tdoa:+.3f}  conf={tdoa_conf:.2f}")

        # Compute energy confidence
        energy_conf = compute_energy_confidence(comp, dom_ratio)

        # Intelligent fusion of energy and TDOA
        sx, sy, fusion_method = fuse_localization(
            sx_energy, sy_energy, energy_conf,
            sx_tdoa, sy_tdoa, tdoa_conf
        )

        if getattr(self, "debug_print", False):
            print(f"[FUSION] method={fusion_method}  energy_conf={energy_conf:.2f}  tdoa_conf={tdoa_conf:.2f}")
            print(f"         sx_e={sx_energy:+.3f} sy_e={sy_energy:+.3f} | sx_t={f'{sx_tdoa:+.3f}' if sx_tdoa else 'N/A'} sy_t={f'{sy_tdoa:+.3f}' if sy_tdoa else 'N/A'} -> sx={sx:+.3f} sy={sy:+.3f}")

        fit = self.fit_getter() if self.fit_getter else None
        if getattr(self, "debug_print", False):
            fit_info = None if not fit else fit.get('model')
            if fit and fit.get('params'):
                # Show first 2 coefficients to verify calibration is loaded
                x_coeffs = fit['params'].get('x', [])
                y_coeffs = fit['params'].get('y', [])
                fit_info = f"{fit.get('model')} x[0:2]={x_coeffs[0:2] if x_coeffs else 'N/A'} y[0:2]={y_coeffs[0:2] if y_coeffs else 'N/A'}"
            print(
                f"[FIT] {fit_info}  "
                f"x_frac={x_frac:.2f} y_frac={y_frac:.2f}  "
                f"sx_raw={sx_raw:+.3f} sy_raw={sy_raw:+.3f} -> sx_e={sx_energy:+.3f} sy_e={sy_energy:+.3f} -> sx={sx:+.3f} sy={sy:+.3f}"
            )

        x, y = xy_from_features(sx, sy, fit)
        r = math.hypot(x, y)

        if getattr(self, "debug_print", False):
            print(f"[ACCEPT] sx={sx:+.3f}  sy={sy:+.3f}  x={x:+.2f}cm  y={y:+.2f}cm  r={r:.2f}cm")

        event = {
            "src_ip": addr[0],
            "sx": sx,
            "sy": sy,
            "x": x,
            "y": y,
            "r": r,
            "raw": msg,
        }

        # Log hit to CSV (with extended data for analysis)
        log_evt = {
            "seq": seq,
            "node": node,
            "x_m": x,
            "y_m": y,
            "sx": sx,
            "sy": sy,
            # Fusion details
            "fusion_method": fusion_method,
            "energy_conf": energy_conf,
            "tdoa_conf": tdoa_conf,
            # Energy features
            "sx_energy": sx_energy,
            "sy_energy": sy_energy,
            "total_energy": energy,
            "max_peak": max_peak,
            "dom_ratio": dom_ratio,
            # TDOA features
            "sx_tdoa": sx_tdoa,
            "sy_tdoa": sy_tdoa,
            "tdoa_N_us": tdoa_comp.get("N", 0),
            "tdoa_W_us": tdoa_comp.get("W", 0),
            "tdoa_S_us": tdoa_comp.get("S", 0),
            "tdoa_E_us": tdoa_comp.get("E", 0),
            # Per-channel energy
            "energy_N": comp["N"],
            "energy_W": comp["W"],
            "energy_S": comp["S"],
            "energy_E": comp["E"],
            # Classification
            "label": label,
            "score": score,
        }
        log_hit(log_evt, mode=mode_s if mode_s else "shooting")

        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

async def udp_loop(host: str, port: int, queue: asyncio.Queue, ch2comp: Dict[str, str], mode_getter, fit_getter=None, cal_getter=None):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: UDPProtocol(queue, ch2comp, mode_getter=mode_getter, fit_getter=fit_getter, cal_getter=cal_getter),
        local_addr=(host, port),
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        transport.close()
