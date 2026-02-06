# backend/app.py
import asyncio, time
import os,json
from typing import Set, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from fastapi.staticfiles import StaticFiles # type: ignore
from pose_udp_listener import start_pose_udp_listener, get_latest_pose as get_latest_pose_udp
import numpy as np # type: ignore
import config

# Import camera module (optional - gracefully handle if not available)
try:
    import camera
    _camera_available = True
except ImportError:
    _camera_available = False
    print("[APP] Camera module not available - running without camera")


def get_latest_pose():
    """Get latest pose from camera module, fallback to UDP listener."""
    if _camera_available:
        pose = camera.get_latest_pose()
        if pose is not None:
            return pose
    return get_latest_pose_udp()
from scoring import score_from_r
from state import SessionState, Shot
from udp_listener import udp_loop, log_calibration_confirmation
from pydantic import BaseModel # type: ignore
import threading
from mode_state import get_mode, set_mode
import database
from session_manager import SessionManager


app = FastAPI()

# Mount static file serving for screenshots
screenshots_dir = os.path.join(os.path.dirname(__file__), config.SCREENSHOTS_DIR)
os.makedirs(screenshots_dir, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=screenshots_dir), name="screenshots")

calibration = {
    "active": False,
    "pending": None,
    "samples": [],
    "paused": False,
}

CAL_FIT_PATH = os.path.join(os.path.dirname(__file__), "calibration_fit.json")
calibration_fit = None  # active fit used by UDP->XY mapping
calibration_fit_version = 0  # increments each time a new fit is applied

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # dev: allow everything
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_mode_lock = threading.Lock()
_mode = "shooting"  # or "scoring" if you prefer starting "safe"
_pose_clients: set[WebSocket] = set()
_pose_queue: asyncio.Queue = asyncio.Queue()
clients: Set[WebSocket] = set()
state = SessionState()
queue: asyncio.Queue = asyncio.Queue(maxsize=200)

# Session manager for tracking active sessions
session_manager = SessionManager()

def get_fit():
    # return the currently active calibration fit (or None)
    return calibration_fit

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
    last_pose_ts = 0
    while True:
        # If camera module is available, poll for new poses
        if _camera_available:
            pose = camera.get_latest_pose()
            if pose and pose.get("ts", 0) > last_pose_ts:
                last_pose_ts = pose.get("ts", 0)
                dead = []
                for ws in list(_pose_clients):
                    try:
                        await ws.send_json(pose)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    _pose_clients.discard(ws)
            await asyncio.sleep(0.05)  # Poll at ~20Hz
        else:
            # UDP mode: wait for messages from queue
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
CH2COMP = {"0": "N", "1": "W", "2": "S", "3": "E"}

@app.on_event("startup")
async def startup():
    # Initialize database
    await database.init_db()

    # Start camera (if available and enabled)
    camera_enabled = getattr(config, 'CAMERA_ENABLED', True)
    if _camera_available and camera_enabled:
        print("[APP] Starting camera...")
        camera.start_camera()
    elif not camera_enabled:
        print("[APP] Camera disabled in config - screenshots will use HTTP fallback")
    else:
        print("[APP] Camera not available - screenshots will use HTTP fallback")

    # start UDP loop and broadcast loop
    global calibration_fit
    try:
        with open(CAL_FIT_PATH, "r") as f:
            calibration_fit = json.load(f)
            # Detect old meter-based fits: if the constant offset (last coeff) is < 1.0
            # for both axes, it was calibrated in meters — discard it.
            params = calibration_fit.get("params", {}) if calibration_fit else {}
            cx = params.get("x", [])
            cy = params.get("y", [])
            if cx and cy and abs(cx[-1]) < 1.0 and abs(cy[-1]) < 1.0:
                print("[CAL] WARNING: old meter-based fit detected (offsets < 1cm) — clearing it. Re-calibrate in cm.")
                calibration_fit = None
                os.remove(CAL_FIT_PATH)
            else:
                print("[CAL] loaded fit from disk:", calibration_fit)
    except Exception:
        calibration_fit = None
    asyncio.create_task(udp_loop(config.UDP_HOST, config.UDP_PORT, queue, CH2COMP, get_mode, fit_getter=get_fit))
    asyncio.create_task(dispatch_loop())

@app.on_event("startup")
async def _startup_pose_listener():
    loop = asyncio.get_running_loop()

    def on_pose(msg: dict):
        # thread-safe handoff from UDP thread -> asyncio loop
        loop.call_soon_threadsafe(_pose_queue.put_nowait, msg)

    # Only start UDP listener if camera module not available
    # (camera module provides pose data directly)
    if not _camera_available:
        print("[APP] Starting UDP pose listener (camera not available)")
        start_pose_udp_listener(host="0.0.0.0", port=5015, on_pose=on_pose)
    else:
        print("[APP] Using camera module for pose data")

    asyncio.create_task(_pose_broadcaster())

async def dispatch_loop():
    while True:
        evt = await queue.get()
        print(f"[DISPATCH] Received event: x={evt.get('x')}, y={evt.get('y')}, r={evt.get('r')}")
        # If we're calibrating, capture a pending shot instead of recording it
        if calibration.get("active"):
            print(f"[DISPATCH] Calibration active, creating pending shot")
            # Only accept one pending shot at a time
            
            if calibration.get("paused"):
                calibration["pending"] = None
                continue

            if calibration.get("pending") is None:
                # Store full event data for CSV logging with ground truth later
                raw_msg = evt.get("raw", {})
                pending = {
                    "ts": time.time(),
                    # Raw features for calibration fit
                    "sx": evt.get("sx"),
                    "sy": evt.get("sy"),
                    # Current estimated position
                    "x": evt.get("x"),
                    "y": evt.get("y"),
                    "r": evt.get("r"),
                    # Full event data for CSV logging
                    "log_data": {
                        "seq": raw_msg.get("seq"),
                        "node": raw_msg.get("node"),
                        "x_m": evt.get("x"),
                        "y_m": evt.get("y"),
                        "sx": evt.get("sx"),
                        "sy": evt.get("sy"),
                        "raw": raw_msg,
                    },
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
        print(f"[DISPATCH] Normal mode, processing shot")
        score, is_x = score_from_r(evt["r"])
        shot = Shot(
            ts=time.time(),
            x=evt["x"],
            y=evt["y"],
            r=evt["r"],
            score=score,
            is_x=is_x,
        )

        # Add to legacy state (for backward compatibility)
        state.add_shot(shot)
        print(f"[DISPATCH] Shot recorded: score={score}, is_x={is_x}")

        # If there's an active session, add to session manager with screenshot
        if session_manager.has_active_session():
            posture = get_latest_pose()
            await session_manager.add_shot(shot, posture)
        else:
            print("[DISPATCH] No active session - shot not saved to database")

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
                print(f"[DISPATCH] Broadcasted shot to WebSocket client")
            except Exception as e:
                print(f"[DISPATCH] Failed to send to client: {e}")
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)
        print(f"[DISPATCH] Broadcast complete, {len(clients)} clients remaining")

def _save_fit_to_disk(fit: dict):
    tmp = CAL_FIT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(fit, f)
    os.replace(tmp, CAL_FIT_PATH)

def _compute_fit_internal(samples: list) -> tuple[dict | None, str | None]:
    """
    Compute calibration fit from samples.
    Returns (fit_dict, None) on success, or (None, error_string) on failure.
    Also sets calibration_fit global to apply immediately.

    Uses linear model (3 params) for 3-5 samples, poly2 (6 params) for 6+.
    """
    global calibration_fit

    if len(samples) < 3:
        return None, f"need at least 3 samples (have {len(samples)})"

    # Build least-squares system - collect valid samples first
    valid_samples = []
    for s in samples:
        sx = s.get("sx")
        sy = s.get("sy")
        x_gt = s.get("x_gt")
        y_gt = s.get("y_gt")
        if sx is None or sy is None or x_gt is None or y_gt is None:
            continue
        valid_samples.append((float(sx), float(sy), float(x_gt), float(y_gt)))

    n_valid = len(valid_samples)
    if n_valid < 3:
        return None, f"not enough valid samples (have {n_valid})"

    # Choose model based on sample count
    use_poly2 = n_valid >= 6

    A = []
    bx = []
    by = []
    for sx, sy, x_gt, y_gt in valid_samples:
        if use_poly2:
            A.append([sx, sy, sx*sy, sx*sx, sy*sy, 1.0])
        else:
            # Linear model: just sx, sy, 1
            A.append([sx, sy, 1.0])
        bx.append(x_gt)
        by.append(y_gt)

    A = np.array(A, dtype=np.float64)
    bx = np.array(bx, dtype=np.float64)
    by = np.array(by, dtype=np.float64)

    # Solve A * px ~= bx, A * py ~= by
    px, *_ = np.linalg.lstsq(A, bx, rcond=None)
    py, *_ = np.linalg.lstsq(A, by, rcond=None)

    # Compute errors
    x_hat = A @ px
    y_hat = A @ py
    err = np.sqrt((x_hat - bx) ** 2 + (y_hat - by) ** 2)

    mean_cm = float(err.mean())
    max_cm = float(err.max())

    if use_poly2:
        model_name = "poly2_sxsy"
        params = {
            "order": ["sx", "sy", "sx_sy", "sx2", "sy2", "1"],
            "x": [float(v) for v in px.tolist()],
            "y": [float(v) for v in py.tolist()],
        }
    else:
        model_name = "linear_sxsy"
        params = {
            "order": ["sx", "sy", "1"],
            "x": [float(v) for v in px.tolist()],
            "y": [float(v) for v in py.tolist()],
        }

    # Apply fit immediately so next arrow uses it
    global calibration_fit_version
    calibration_fit_version += 1
    calibration_fit = {"model": model_name, "params": params}

    print("=" * 60)
    print(f"[CAL] *** NEW FIT v{calibration_fit_version} COMPUTED & APPLIED ({len(err)} samples, {model_name}) ***")
    print(f"[CAL]   Mean error: {mean_cm:.2f} cm")
    print(f"[CAL]   Max error:  {max_cm:.2f} cm")
    print(f"[CAL]   X coeffs: {[f'{v:.6f}' for v in px.tolist()]}")
    print(f"[CAL]   Y coeffs: {[f'{v:.6f}' for v in py.tolist()]}")
    print("=" * 60)

    fit_result = {
        "model": model_name,
        "params": params,
        "mean_error_cm": mean_cm,
        "max_error_cm": max_cm,
        "n": int(len(err)),
    }

    return fit_result, None

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
    return {"RINGS_CM": config.RINGS_CM, "ARROWS_PER_END": config.ARROWS_PER_END, "MAX_ENDS": config.MAX_ENDS}

@app.post("/api/config/rings")
async def set_rings(payload: dict):
    # payload example: {"10":0.02,"9":0.04,...,"X":0.01}
    new_rings = {}
    for k, v in payload.items():
        if k == "X":
            new_rings["X"] = float(v)
        else:
            new_rings[int(k)] = float(v)
    config.RINGS_CM.clear()
    config.RINGS_CM.update(new_rings)
    return {"ok": True, "RINGS_CM": config.RINGS_CM}

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

# ========== Session Management Endpoints ==========

class SessionStartRequest(BaseModel):
    arrows_per_end: int
    num_ends: int
    notes: Optional[str] = None

@app.post("/api/session/start")
async def start_session(payload: SessionStartRequest):
    """Start a new training session"""
    session_id = await session_manager.start_session(
        arrows_per_end=payload.arrows_per_end,
        num_ends=payload.num_ends,
        notes=payload.notes
    )
    return {"ok": True, "session_id": session_id}

@app.get("/api/session/current")
def get_current_session():
    """Get information about the current active session"""
    session_info = session_manager.get_session_info()
    if session_info is None:
        return {"ok": False, "message": "No active session"}
    return {"ok": True, **session_info}

@app.post("/api/session/end")
async def end_session():
    """Manually end the current session"""
    if not session_manager.has_active_session():
        return {"ok": False, "message": "No active session"}

    await session_manager.end_session()
    return {"ok": True}

@app.get("/api/sessions")
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    start_date: Optional[float] = None,
    end_date: Optional[float] = None,
    complete_only: bool = False
):
    """List all sessions with filtering and pagination"""
    result = await database.list_sessions(
        limit=limit,
        offset=offset,
        start_date=start_date,
        end_date=end_date,
        complete_only=complete_only
    )
    return result

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: int):
    """Get full details of a specific session"""
    session = await database.get_session(session_id)
    if session is None:
        return {"ok": False, "error": "Session not found"}
    return {"ok": True, "session": session}

@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: int):
    """Delete a session and its associated data"""
    await database.delete_session(session_id)
    return {"ok": True}

@app.get("/api/sessions/{session_id}/stats")
async def get_session_stats(session_id: int):
    """Get statistics for a specific session"""
    stats = await database.get_session_stats(session_id)
    if stats is None:
        return {"ok": False, "error": "Session not found"}
    return {"ok": True, "stats": stats}

@app.post("/api/calibration/start")
def cal_start():
    calibration["active"] = True
    calibration["paused"] = False
    calibration["pending"] = None
    calibration["samples"] = []
    calibration["session_id"] = f"cal_{int(time.time())}"  # Unique session ID for CSV grouping
    calibration.pop("fit", None)
    return {"ok": True, "active": True, "session_id": calibration["session_id"]}

@app.post("/api/calibration/pause")
def cal_pause():
    calibration["paused"] = True
    calibration["pending"] = None  # clear any pending to avoid freezing
    return {"ok": True, "paused": True}

@app.post("/api/calibration/resume")
def cal_resume():
    calibration["paused"] = False
    calibration["pending"] = None
    return {"ok": True, "paused": False}

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

    pending = calibration["pending"]
    sample = {**pending, "x_gt": x_gt, "y_gt": y_gt}
    calibration["samples"].append(sample)
    calibration["pending"] = None

    count = len(calibration["samples"])
    print(f"[CAL] Arrow #{count} confirmed: sx={sample.get('sx', 0):.4f}, sy={sample.get('sy', 0):.4f} -> gt=({x_gt:.4f}, {y_gt:.4f})")

    # Log to CSV with ground truth
    log_data = pending.get("log_data", {})
    if log_data:
        session_id = calibration.get("session_id", f"cal_{int(time.time())}")
        log_calibration_confirmation(log_data, x_gt, y_gt, session_id=session_id)
        print(f"[CAL] Logged to CSV: estimated=({log_data.get('x_m', 0):.2f}, {log_data.get('y_m', 0):.2f})cm -> ground_truth=({x_gt:.2f}, {y_gt:.2f})cm")

    response = {"ok": True, "count": count}

    # Auto-compute and apply fit when we have enough samples
    if count >= 6:
        print(f"[CAL] Recomputing fit with {count} samples...")
        fit_result, err = _compute_fit_internal(calibration["samples"])
        if fit_result:
            calibration["fit"] = fit_result
            response["fit"] = {
                "mean_error_cm": fit_result["mean_error_cm"],
                "max_error_cm": fit_result["max_error_cm"],
                "n": fit_result["n"],
            }
            response["fit_version"] = calibration_fit_version
            response["fit_applied"] = True

    return response

@app.post("/api/calibration/compute")
def cal_compute():
    samples = calibration.get("samples", [])
    fit_result, err = _compute_fit_internal(samples)

    if err:
        return {"ok": False, "error": err, "count": len(samples)}

    calibration["fit"] = fit_result
    return {"ok": True, **fit_result}

@app.get("/api/calibration/fit")
def cal_fit():
    return {"ok": True, "fit": calibration_fit}

@app.post("/api/calibration/apply")
def cal_apply():
    global calibration_fit

    fit = calibration.get("fit")
    if not fit or fit.get("model") not in ("affine_sxsy", "poly2_sxsy"):
        return {"ok": False, "error": "no computed fit to apply"}

    # Save only what we need for runtime mapping
    calibration_fit = {"model": fit["model"], "params": fit["params"]}
    _save_fit_to_disk(calibration_fit)

    # Exit calibration mode cleanly
    calibration["active"] = False
    calibration["paused"] = False
    calibration["pending"] = None

    # Put system back into normal shooting mode
    try:
        set_mode("shooting")
    except Exception:
        pass

    return {"ok": True, "applied": True, "fit": calibration_fit, "mode": get_mode()}


@app.post("/api/calibration/reset")
def cal_reset():
    """Reset calibration to start fresh - clears fit and all samples."""
    global calibration_fit, calibration_fit_version

    # Clear the active fit
    calibration_fit = None
    calibration_fit_version = 0

    # Delete the saved fit file
    if os.path.exists(CAL_FIT_PATH):
        try:
            os.remove(CAL_FIT_PATH)
            print("[CAL] Deleted calibration_fit.json")
        except Exception as e:
            print(f"[CAL] Failed to delete fit file: {e}")

    # Clear all calibration state
    calibration["samples"] = []
    calibration["fit"] = None
    calibration["pending"] = None
    calibration["active"] = False
    calibration["paused"] = False

    print("[CAL] *** CALIBRATION RESET - starting fresh ***")
    return {"ok": True, "message": "Calibration reset. No fit active - raw sx/sy will be used."}