# backend/mode_state.py
import traceback

MODE = "shooting"

def get_mode() -> str:
    return MODE

def set_mode(m: str) -> str:
    global MODE
    before = MODE
    m = (m or "").strip().lower()
    if m in ("shooting", "scoring"):
        MODE = m
    print(f"[MODE_STATE] {before} -> {MODE} (requested={m})")
    # show who called it (last few frames)
    traceback.print_stack(limit=6)
    return MODE