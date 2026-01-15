import json
import socket
import threading
from typing import Any, Callable, Dict, Optional

_latest_pose: Optional[Dict[str, Any]] = None
_lock = threading.Lock()

def get_latest_pose() -> Optional[Dict[str, Any]]:
    with _lock:
        return _latest_pose

def _set_latest_pose(p: Dict[str, Any]) -> None:
    global _latest_pose
    with _lock:
        _latest_pose = p

def start_pose_udp_listener(
    host: str = "0.0.0.0",
    port: int = 5015,
    on_pose: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """
    Listens for UDP JSON packets from the IMX500 pose demo.
    Stores the latest message and optionally calls on_pose(msg) for live pushing.
    """
    def run():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, port))
        while True:
            data, _addr = sock.recvfrom(65535)
            try:
                msg = json.loads(data.decode("utf-8", errors="ignore"))
                if isinstance(msg, dict) and msg.get("type") == "pose":
                    _set_latest_pose(msg)
                    if on_pose:
                        on_pose(msg)
            except Exception:
                pass

    t = threading.Thread(target=run, daemon=True)
    t.start()