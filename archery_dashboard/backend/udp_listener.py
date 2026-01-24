# backend/udp_listener.py
import asyncio, json, math, time
from typing import Dict, Any, Callable
import os
try:
    from mode_state import get_mode as default_get_mode
except Exception:
    default_get_mode = None

from urllib.request import urlopen, Request

_FIT_CACHE = {"fit": None, "ts": 0.0}

def get_fit_cached(ttl: float = 0.5):
    now = time.time()
    if now - _FIT_CACHE["ts"] < ttl:
        return _FIT_CACHE["fit"]

    try:
        req = Request("http://127.0.0.1:8000/api/calibration/fit", headers={"Accept": "application/json"})
        with urlopen(req, timeout=0.2) as r:
            data = json.loads(r.read().decode("utf-8"))
            fit = data.get("fit")

            if isinstance(fit, dict) and isinstance(fit.get("params"), dict):
                if fit.get("model") in ("affine_sxsy", "poly2_sxsy"):
                    _FIT_CACHE["fit"] = fit
                else:
                    _FIT_CACHE["fit"] = None
            else:
                _FIT_CACHE["fit"] = None
    except Exception:
        pass

    _FIT_CACHE["ts"] = now
    return _FIT_CACHE["fit"]

# Keep consistent with your current geometry idea
D_M = 1.0
HALF_SPAN = D_M / 2.0

def extract_compass_peaks(msg: Dict[str, Any], ch2comp: Dict[str, str]) -> Dict[str, float]:
    ch = msg.get("ch", {})
    peaks_by_ch = {k: float(v.get("peak", 0.0)) for k, v in ch.items()}

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
    def __init__(self, queue: asyncio.Queue, ch2comp: Dict[str, str], mode_getter: Callable[[], str] | None = None):
        self.queue = queue
        self.ch2comp = ch2comp
        # If a mode getter isn't provided, fall back to mode_state.get_mode (if available)
        self.mode_getter = mode_getter or default_get_mode
        self._last_accept_ts = 0.0
        self.cooldown_s = 0.7      # tweak later if needed
        self.min_energy = 1080      # sum of peaks threshold (tune later)
        self._energy_ema = 0.0
        self._ema_alpha = 0.05
        self.min_jump = 60.0   # tune: 40–120
        self.debug_print = True
        self.ghost_floor = 900.0   # don’t spam tiny noise; adjust if needed
        
    def datagram_received(self, data: bytes, addr):
        try:
            msg = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception:
            return

        if not (isinstance(msg, dict) and msg.get("type") == "hit_bundle"):
            return

        comp = extract_compass_peaks(msg, self.ch2comp)

        energy = comp["N"] + comp["E"] + comp["W"] + comp["S"]

        # EMA baseline (keep previous for delta explanation)
        ema_prev = self._energy_ema
        if ema_prev == 0.0:
            ema_prev = energy
            self._energy_ema = energy

        delta = energy - ema_prev

        # Always update baseline (EMA)
        self._energy_ema = (1 - self._ema_alpha) * self._energy_ema + self._ema_alpha * energy
        ema_now = self._energy_ema

        # Classify + explain
        label = "HIT"
        reason = "pass"

        if energy < self.min_energy:
            label = "GHOST"
            reason = f"energy<{self.min_energy:.1f}"
        elif delta < self.min_jump:
            label = "GHOST"
            reason = f"delta<{self.min_jump:.1f}"

        # Print everything above a floor to avoid spam
        if getattr(self, "debug_print", False) and energy >= getattr(self, "ghost_floor", 0.0):
            print(
                f"[{label}] energy={energy:7.1f} ema_prev={ema_prev:7.1f} ema={ema_now:7.1f} "
                f"delta={delta:7.1f} reason={reason} "
                f"thr_energy={self.min_energy:.1f} thr_jump={self.min_jump:.1f} "
                f"peaks=N{comp['N']:.1f} E{comp['E']:.1f} W{comp['W']:.1f} S{comp['S']:.1f}"
            )

        # If it’s not a valid hit, stop here
        if label != "HIT":
            return

        # Cooldown (avoid duplicates)
        now = time.time()
        dt = now - self._last_accept_ts
        if dt < self.cooldown_s:
            if getattr(self, "debug_print", False) and energy >= getattr(self, "ghost_floor", 0.0):
                print(f"[DROP_COOLDOWN] energy={energy:7.1f} dt={dt:0.3f}s cooldown={self.cooldown_s:.3f}s")
            return

        # Mode check (only accept in shooting mode)
        mode = self.mode_getter() if self.mode_getter else None
        if mode is None:
            if getattr(self, "debug_print", False):
                print("[DROP_MODE] mode=None")
            return
        if str(mode).strip() != "shooting":
            if getattr(self, "debug_print", False) and energy >= getattr(self, "ghost_floor", 0.0):
                print(f"[DROP_MODE] mode={mode!r}")
            return

        # Accept hit (stamp last accept *after* passing all gates)
        self._last_accept_ts = now

        # Compute features + calibrated mapping
        sx, sy = features_from_peaks(comp["N"], comp["E"], comp["W"], comp["S"])
        fit = get_fit_cached()
        x, y = xy_from_features(sx, sy, fit)
        r = math.hypot(x, y)

        # Optional: print sx/sy only for accepted hits
        if getattr(self, "debug_print", False):
            print(f"[ACCEPT] sx={sx:+.3f} sy={sy:+.3f} x={x:+.4f} y={y:+.4f} r={r:.4f}")

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

async def udp_loop(host: str, port: int, queue: asyncio.Queue, ch2comp: Dict[str, str], mode_getter):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: UDPProtocol(queue, ch2comp, mode_getter=mode_getter),
        local_addr=(host, port),
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        transport.close()
