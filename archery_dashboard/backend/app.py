# backend/app.py
import asyncio, time
from typing import Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from pose_udp_listener import start_pose_udp_listener, get_latest_pose
import numpy as np
import config
from scoring import score_from_r
from state import SessionState, Shot
from udp_listener import udp_loop
from pydantic import BaseModel # type: ignore
import threading
from mode_state import get_mode, set_mode


app = FastAPI()

calibration = {
    "active": False,
    "pending": None,
    "samples": []
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # dev: allow everything
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_mode_lock = threading.Lock()
_mode = "shooting"  # or "scoring" if you prefer starting “safe”
_pose_clients: set[WebSocket] = set()
_pose_queue: asyncio.Queue = asyncio.Queue()
clients: Set[WebSocket] = set()
state = SessionState()
queue: asyncio.Queue = asyncio.Queue(maxsize=200)

def get_mode() -> str:
    with _mode_lock:
        return _mode

def set_mode(m: str) -> None:
    global _mode
    if m not in ("shooting", "scoring"):
        raise ValueError("mode must be 'shooting' or 'scoring'")
    with _mode_lock:
        _mode = m

class ModeIn(BaseModel):
    mode: str

async def _pose_broadcaster():
    while True:
        msg = await _pose_queue.get()
        dead = []
        for ws in list(_pose_clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _pose_clients.discard(ws)

# Channel mapping (same default you used)
CH2COMP = {"0": "N", "1": "E", "2": "W", "3": "S"}

@app.on_event("startup")
async def startup():
    # start UDP loop and broadcast loop
    asyncio.create_task(udp_loop(config.UDP_HOST, config.UDP_PORT, queue, CH2COMP, get_mode))
    asyncio.create_task(dispatch_loop())

@app.on_event("startup")
async def _startup_pose_listener():
    loop = asyncio.get_running_loop()

    def on_pose(msg: dict):
        # thread-safe handoff from UDP thread -> asyncio loop
        loop.call_soon_threadsafe(_pose_queue.put_nowait, msg)

    start_pose_udp_listener(host="0.0.0.0", port=5015, on_pose=on_pose)
    asyncio.create_task(_pose_broadcaster())

async def dispatch_loop():
    while True:
        evt = await queue.get()

        # If we're calibrating, capture a pending shot instead of recording it
        if calibration.get("active"):
            # Only accept one pending shot at a time
            if calibration.get("pending") is None:
                pending = {
                    "ts": time.time(),
                    # raw features if provided by udp_listener.py (optional)
                    "sx": evt.get("sx"),
                    "sy": evt.get("sy"),
                    # current (uncalibrated) position
                    "x": evt.get("x"),
                    "y": evt.get("y"),
                    "r": evt.get("r"),
                    "raw": evt.get("raw"),
                }
                calibration["pending"] = pending

                payload = {
                    "type": "cal_pending",
                    "pending": pending,
                    "count": len(calibration.get("samples", [])),
                }

                dead = []
                for ws in list(clients):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    clients.discard(ws)

            # Do not update normal scoring/state while calibrating
            continue

        # Normal mode: compute score + record shot
        score, is_x = score_from_r(evt["r"])
        shot = Shot(
            ts=time.time(),
            x=evt["x"],
            y=evt["y"],
            r=evt["r"],
            score=score,
            is_x=is_x,
        )
        state.add_shot(shot)

        payload = {
            "type": "shot",
            "shot": {
                "ts": shot.ts,
                "x": shot.x,
                "y": shot.y,
                "r": shot.r,
                "score": "X" if shot.is_x else shot.score,
            },
            "table": state.to_payload(),
        }

        dead = []
        for ws in list(clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

@app.get("/api/state")
def get_state():
    return state.to_payload()

@app.post("/api/reset")
def reset_state():
    state.ends.clear()
    return {"ok": True, "table": state.to_payload()}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)

    # send current state immediately
    await ws.send_json({"type": "state", "table": state.to_payload()})

    try:
        while True:
            # keep alive; later we can accept commands (reset, next end, etc.)
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)

@app.websocket("/ws_pose")
async def ws_pose(ws: WebSocket):
    await ws.accept()
    _pose_clients.add(ws)

    # send latest pose immediately (if available)
    latest = get_latest_pose()
    if latest is not None:
        await ws.send_json(latest)

    try:
        # Keep the connection open. We don't require any client messages.
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        _pose_clients.discard(ws)
    except Exception:
        _pose_clients.discard(ws)

@app.get("/api/config")
def get_config():
    return {"RINGS_M": config.RINGS_M, "ARROWS_PER_END": config.ARROWS_PER_END, "MAX_ENDS": config.MAX_ENDS}

@app.post("/api/config/rings")
async def set_rings(payload: dict):
    # payload example: {"10":0.02,"9":0.04,...,"X":0.01}
    new_rings = {}
    for k, v in payload.items():
        if k == "X":
            new_rings["X"] = float(v)
        else:
            new_rings[int(k)] = float(v)
    config.RINGS_M.clear()
    config.RINGS_M.update(new_rings)
    return {"ok": True, "RINGS_M": config.RINGS_M}

@app.get("/api/shots")
def get_shots():
    return {"shots": state.all_shots()}

@app.get("/api/posture")
def api_posture():
    return {"pose": get_latest_pose()}

@app.get("/api/mode")
def api_get_mode():
    return {"mode": get_mode()}

@app.post("/api/mode")
def api_set_mode(payload: ModeIn):
    before = get_mode()
    set_mode(payload.mode)
    after = get_mode()
    print("[MODE] before=", before, "requested=", payload.mode, "after=", after)
    return {"mode": after}

@app.post("/api/calibration/start")
def cal_start():
    calibration["active"] = True
    calibration["pending"] = None
    calibration["samples"] = []
    return {"ok": True, "active": True}

@app.get("/api/calibration/status")
def cal_status():
    return calibration

@app.post("/api/calibration/confirm")
def cal_confirm(payload: dict):
    # payload: {x_gt: float, y_gt: float}
    if not calibration["active"]:
        return {"ok": False, "error": "not active"}
    if calibration["pending"] is None:
        return {"ok": False, "error": "no pending shot"}
    x_gt = float(payload.get("x_gt"))
    y_gt = float(payload.get("y_gt"))
    sample = {**calibration["pending"], "x_gt": x_gt, "y_gt": y_gt}
    calibration["samples"].append(sample)
    calibration["pending"] = None
    return {"ok": True, "count": len(calibration["samples"])}

@app.post("/api/calibration/compute")
def cal_compute():
    samples = calibration.get("samples", [])
    if len(samples) < 6:
        return {"ok": False, "error": "need at least 6 samples", "count": len(samples)}

    # Build least-squares system
    # rows: [sx, sy, 1] -> x_gt and y_gt
    A = []
    bx = []
    by = []
    for s in samples:
        sx = s.get("sx")
        sy = s.get("sy")
        x_gt = s.get("x_gt")
        y_gt = s.get("y_gt")
        if sx is None or sy is None or x_gt is None or y_gt is None:
            continue
        A.append([float(sx), float(sy), 1.0])
        bx.append(float(x_gt))
        by.append(float(y_gt))

    if len(A) < 6:
        return {"ok": False, "error": "not enough valid samples", "count": len(A)}

    A = np.array(A, dtype=np.float64)
    bx = np.array(bx, dtype=np.float64)
    by = np.array(by, dtype=np.float64)

    # Solve A * px ~= bx, A * py ~= by
    px, *_ = np.linalg.lstsq(A, bx, rcond=None)  # [a,b,c]
    py, *_ = np.linalg.lstsq(A, by, rcond=None)  # [d,e,f]

    # Compute errors
    x_hat = A @ px
    y_hat = A @ py
    err = np.sqrt((x_hat - bx) ** 2 + (y_hat - by) ** 2)

    mean_cm = float(err.mean() * 100.0)
    max_cm = float(err.max() * 100.0)

    params = {
        "a": float(px[0]), "b": float(px[1]), "c": float(px[2]),
        "d": float(py[0]), "e": float(py[1]), "f": float(py[2]),
    }

    calibration["fit"] = {
        "model": "affine_sxsy",
        "params": params,
        "mean_error_cm": mean_cm,
        "max_error_cm": max_cm,
        "n": int(len(err)),
    }

    return {"ok": True, **calibration["fit"]}