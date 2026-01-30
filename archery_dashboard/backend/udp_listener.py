# backend/udp_listener.py
import asyncio, json, math, time
from math import log
from typing import Dict, Any, Callable, Optional
import os
try:
    from mode_state import get_mode as default_get_mode
except Exception:
    default_get_mode = None

# Keep consistent with your current geometry idea
D_M = 1.0
HALF_SPAN = D_M / 2.0

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

def xy_from_features(sx: float, sy: float, fit):
    """Map normalized features -> meters. Uses calibration fit when available."""

    if isinstance(fit, dict):
        model = fit.get("model")
        p = fit.get("params", {})

        # New: 2nd-order polynomial fit
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
    def __init__(self, queue: asyncio.Queue, ch2comp: Dict[str, str], mode_getter: Optional[Callable[[], str]] = None, fit_getter: Optional[Callable[[], Any]] = None):
        self.queue = queue
        self.ch2comp = ch2comp
        # If a mode getter isn't provided, fall back to mode_state.get_mode (if available)
        self.mode_getter = mode_getter or default_get_mode
        self.fit_getter = fit_getter
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
        self.score_thresh_shooting = 7
        self.score_thresh_calibration = 9

        # Feature thresholds for scoring
        self.score_sumE2_1 = 300.0
        self.score_sumE2_2 = 600.0
        self.score_peak_1 = 320.0
        self.score_peak_2 = 360.0
        self.score_dom_1 = 0.45
        self.score_dom_2 = 0.60
        self.score_peak_over = 25.0
        self.score_entropy_max = 1.00
        self.score_top2_ratio = 0.75

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
        is_cal = mode_s in {"calibration", "calibrating"}

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

        # Hard rejects first
        if energy < self.min_energy:
            label = "GHOST"
            reason = f"energy<{self.min_energy:.1f}"
        elif getattr(self, "use_dom_gate", False) and (max_energy < getattr(self, "min_max_energy", 0.0)):
            label = "GHOST"
            reason = f"maxE<{self.min_max_energy:.1f}"
        elif getattr(self, "use_dom_gate", False) and (dom_ratio < getattr(self, "min_dom_ratio", 0.0)):
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
            else:
                # Calibration-specific hard requirement
                if is_cal and not ((max_peak >= 320.0) or (energy >= 300.0)):
                    label = "GHOST"
                    reason = "cal_requires(peak>=320 OR sumE2>=300)"
                else:
                    # Multi-feature score classifier
                    score = 0
                    why = []

                    if energy >= self.score_sumE2_1:
                        score += 2
                        why.append(f"sumE2>={self.score_sumE2_1:.0f}(+2)")
                    if energy >= self.score_sumE2_2:
                        score += 3
                        why.append(f"sumE2>={self.score_sumE2_2:.0f}(+3)")

                    if max_peak >= self.score_peak_1:
                        score += 2
                        why.append(f"peak>={self.score_peak_1:.0f}(+2)")
                    if max_peak >= self.score_peak_2:
                        score += 3
                        why.append(f"peak>={self.score_peak_2:.0f}(+3)")

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
        if mode is None:
            if getattr(self, "debug_print", False):
                print("[DROP_MODE] mode=None (mode_getter returned None)")
            return

        allowed = {"shooting", "calibration", "calibrating"}
        if mode_s not in allowed:
            if getattr(self, "debug_print", False) and energy >= getattr(self, "ghost_floor", 0.0):
                print(f"[DROP_MODE] mode={mode!r}")
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
        # These defaults are conservative; adjust after a few test runs.
        x_floor_ratio = 0.18
        y_floor_ratio = 0.18

        def _blend_to_zero(v: float, frac: float, floor: float) -> float:
            if frac <= 0.0:
                return 0.0
            if frac >= floor:
                return max(-1.0, min(1.0, v))
            # Linear blend from 0 at frac=0 to full at frac=floor
            w = frac / floor
            return max(-1.0, min(1.0, v * w))

        sx = _blend_to_zero(sx_raw, x_frac, x_floor_ratio)
        sy = _blend_to_zero(sy_raw, y_frac, y_floor_ratio)

        # Optional: small deadzone to stabilize near-center noise
        deadzone = 0.03
        if abs(sx) < deadzone:
            sx = 0.0
        if abs(sy) < deadzone:
            sy = 0.0

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
                f"sx_raw={sx_raw:+.3f} sy_raw={sy_raw:+.3f} -> sx={sx:+.3f} sy={sy:+.3f}"
            )

        x, y = xy_from_features(sx, sy, fit)
        r = math.hypot(x, y)

        if getattr(self, "debug_print", False):
            print(f"[ACCEPT] sx={sx:+.3f}  sy={sy:+.3f}  x={x:+.4f}m  y={y:+.4f}m  r={r:.4f}m")

        event = {
            "src_ip": addr[0],
            "sx": sx,
            "sy": sy,
            "x": x,
            "y": y,
            "r": r,
            "raw": msg,
        }

        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

async def udp_loop(host: str, port: int, queue: asyncio.Queue, ch2comp: Dict[str, str], mode_getter, fit_getter=None):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: UDPProtocol(queue, ch2comp, mode_getter=mode_getter, fit_getter=fit_getter),
        local_addr=(host, port),
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        transport.close()
