# backend/config.py
UDP_HOST = "0.0.0.0"
UDP_PORT = 5005

# Placeholder ring radii in meters (we will tune later)
RINGS_M = {
    "X": 0.010,
    10: 0.020,
    9: 0.040,
    8: 0.060,
    7: 0.080,
    6: 0.100,
    5: 0.120,
    4: 0.140,
    3: 0.160,
    2: 0.180,
    1: 0.200,
}

ARROWS_PER_END = 3
MAX_ENDS = 10
