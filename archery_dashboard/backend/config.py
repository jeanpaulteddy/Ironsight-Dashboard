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

# Database and storage paths
DATABASE_PATH = "data/archery.db"
SCREENSHOTS_DIR = "data/screenshots"
STREAM_URL = "http://localhost:8081/stream"

# Camera settings (for integrated PiCamera2/IMX500)
CAMERA_ENABLED = True  # Set to False to disable camera (e.g., for development)
CAMERA_MODEL_PATH = "/usr/share/imx500-models/imx500_network_higherhrnet_coco.rpk"
CAMERA_DETECTION_THRESHOLD = 0.3
CAMERA_MJPEG_PORT = 8081

# TDOA (Time Difference of Arrival) settings
TDOA_ENABLED = True                # Enable TDOA-based localization
TDOA_WAVE_SPEED = 150.0            # Wave propagation speed in straw target (m/s)
TDOA_WEIGHT = 0.5                  # Blend weight: 0.0 = pure energy, 1.0 = pure TDOA
HIT_LOG_ENABLED = True             # Enable CSV logging of all arrow hits
HIT_LOG_DIR = "data/logs"          # Directory for hit logs
