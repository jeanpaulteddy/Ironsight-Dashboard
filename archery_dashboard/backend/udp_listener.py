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
    """Fetch /api/calibration/fit at most once per ttl seconds."""
    now = time.time()
    if now - _FIT_CACHE["ts"] < ttl:
        return _FIT_CACHE["fit"]

    try:
        req = Request("http://127.0.0.1:8000/api/calibration/fit", headers={"Accept": "application/json"})
        with urlopen(req, timeout=0.2) as r:
            data = json.loads(r.read().decode("utf-8"))
            fit = data.get("fit")
            if isinstance(fit, dict) and fit.get("model") == "affine_sxsy" and isinstance(fit.get("params"), dict):
                _FIT_CACHE["fit"] = fit
            else:
                _FIT_CACHE["fit"] = None
    except Exception:
        # keep last known fit if backend is restarting
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

    def datagram_received(self, data: bytes, addr):
        try:
            msg = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception:
            return

        if not (isinstance(msg, dict) and msg.get("type") == "hit_bundle"):
            return

        comp = extract_compass_peaks(msg, self.ch2comp)

        energy = comp["N"] + comp["E"] + comp["W"] + comp["S"]

        # --- Jump gating must compare against *previous* baseline ---
        prev_ema = self._energy_ema
        if prev_ema == 0.0:
            # first ever sample, initialize baseline
            prev_ema = energy
            self._energy_ema = energy

        delta = energy - prev_ema

        # Hard gate
        if energy < self.min_energy:
            # TEMP debug (optional)
            # print("[DROP energy] energy=", round(energy, 1), "ema=", round(prev_ema, 1), "peaks=", comp)
            # Update baseline slowly even for drops
            self._energy_ema = (1 - self._ema_alpha) * self._energy_ema + self._ema_alpha * energy
            return

        # Jump gate (blocks steady ghost bursts near threshold)
        if delta < self.min_jump:
            # TEMP debug (optional)
            # print("[DROP jump] energy=", round(energy, 1), "ema=", round(prev_ema, 1), "delta=", round(delta, 1))
            self._energy_ema = (1 - self._ema_alpha) * self._energy_ema + self._ema_alpha * energy
            return

        # Update baseline after passing gates (or keep it always-updating; either is fine)
        self._energy_ema = (1 - self._ema_alpha) * self._energy_ema + self._ema_alpha * energy

        # Cooldown (avoid multiple bundles for one arrow)
        now = time.time()
        if now - self._last_accept_ts < self.cooldown_s:
            return

        # Mode check (only accept in shooting mode)
        mode = self.mode_getter() if self.mode_getter else None
        if mode is None:
            return
        if str(mode).strip() != "shooting":
            return

        # Compute features + calibrated mapping
        sx, sy = features_from_peaks(comp["N"], comp["E"], comp["W"], comp["S"])
        fit = get_fit_cached()
        x, y = xy_from_features(sx, sy, fit)
        r = math.hypot(x, y)

        print("[HIT] energy=", round(energy, 1), "ema=", round(self._energy_ema, 1), "delta=", round(delta, 1),
            "sxsy=", round(sx, 3), round(sy, 3))

        self._last_accept_ts = now

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
