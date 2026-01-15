# backend/udp_listener.py
import asyncio, json, math, time
from typing import Dict, Any
from urllib.request import urlopen, Request
# Keep consistent with your current geometry idea
D_M = 1.0
HALF_SPAN = D_M / 2.0

_MODE_CACHE = {"mode": "shooting", "ts": 0.0}

def get_mode_cached(ttl=0.5) -> str:
    """Fetch /api/mode from backend at most once per ttl seconds."""
    now = time.time()
    if now - _MODE_CACHE["ts"] < ttl:
        return _MODE_CACHE["mode"]

    try:
        req = Request("http://127.0.0.1:8000/api/mode", headers={"Accept": "application/json"})
        with urlopen(req, timeout=0.2) as r:
            data = json.loads(r.read().decode("utf-8"))
            mode = data.get("mode", "shooting")
            if mode in ("shooting", "scoring"):
                _MODE_CACHE["mode"] = mode
    except Exception:
        # If backend is restarting, keep last known mode
        pass

    _MODE_CACHE["ts"] = now
    return _MODE_CACHE["mode"]

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

def xy_from_features(sx: float, sy: float):
    x = HALF_SPAN * sx
    y = HALF_SPAN * sy
    return x, y

class UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue, ch2comp: Dict[str, str]):
        self.queue = queue
        self.ch2comp = ch2comp

    def datagram_received(self, data: bytes, addr):
        try:
            msg = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception:
            return

        if not (isinstance(msg, dict) and msg.get("type") == "hit_bundle"):
            return

        comp = extract_compass_peaks(msg, self.ch2comp)

        sx, sy = features_from_peaks(comp["N"], comp["E"], comp["W"], comp["S"])
        x, y = xy_from_features(sx, sy)
        r = math.hypot(x, y)
        
        # Ignore hits unless system is in shooting mode
        if get_mode_cached() != "shooting":
            return
        
        event = {
            "src_ip": addr[0],
            "x": x,
            "y": y,
            "r": r,
            "raw": msg,
        }

        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

async def udp_loop(host: str, port: int, queue: asyncio.Queue, ch2comp: Dict[str, str]):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: UDPProtocol(queue, ch2comp),
        local_addr=(host, port),
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        transport.close()
