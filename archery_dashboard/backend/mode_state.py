# backend/mode_state.py
MODE = "shooting"

def get_mode() -> str:
    return MODE

def set_mode(m: str) -> str:
    global MODE
    if m in ("shooting", "scoring"):
        MODE = m
    return MODE