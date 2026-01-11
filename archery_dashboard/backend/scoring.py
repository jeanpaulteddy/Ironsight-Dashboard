# backend/scoring.py
from config import RINGS_M

def score_from_r(r_m: float):
    """
    Returns (score_value, is_x).
    X ring counts as 10 with X-flag.
    Outside 1 ring -> 0.
    """
    x_r = RINGS_M.get("X", None)
    if x_r is not None and r_m <= x_r:
        return 10, True

    for s in range(10, 0, -1):
        if r_m <= RINGS_M[s]:
            return s, False

    return 0, False
