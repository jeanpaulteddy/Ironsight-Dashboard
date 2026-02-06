# backend/scoring.py
from config import RINGS_CM

def score_from_r(r_cm: float):
    """
    Returns (score_value, is_x).
    r_cm is distance from center in centimeters.
    X ring counts as 10 with X-flag.
    Outside 1 ring -> 0.
    """
    x_r = RINGS_CM.get("X", None)
    if x_r is not None and r_cm <= x_r:
        return 10, True

    for s in range(10, 0, -1):
        if r_cm <= RINGS_CM[s]:
            return s, False

    return 0, False
