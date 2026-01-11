# backend/app.py
import asyncio, time
from typing import Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import config
from scoring import score_from_r
from state import SessionState, Shot
from udp_listener import udp_loop

app = FastAPI()

clients: Set[WebSocket] = set()
state = SessionState()
queue: asyncio.Queue = asyncio.Queue(maxsize=200)

# Channel mapping (same default you used)
CH2COMP = {"0": "N", "1": "E", "2": "W", "3": "S"}

@app.on_event("startup")
async def startup():
    # start UDP loop and broadcast loop
    asyncio.create_task(udp_loop(config.UDP_HOST, config.UDP_PORT, queue, CH2COMP))
    asyncio.create_task(dispatch_loop())

async def dispatch_loop():
    while True:
        evt = await queue.get()

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
