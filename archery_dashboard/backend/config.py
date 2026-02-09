# backend/config.py
UDP_HOST = "0.0.0.0"
UDP_PORT = 5005

# Ring radii in centimeters (matching real target face)
RINGS_CM = {
    "X": 2,
    10: 4,
    9: 8,
    8: 12,
    7: 16,
    6: 20,
    5: 24,
    4: 28,
    3: 32,
    2: 36,
    1: 40,
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
TDOA_WAVE_SPEED = 100.0            # Wave propagation speed in straw target (m/s)
LOCALIZATION_MODE = "energy"        # "energy", "tdoa", or "fusion"
TARGET_DIAMETER_CM = 126.0          # Sensor span in cm (2 * 63cm from center)
HIT_LOG_ENABLED = True             # Enable CSV logging of all arrow hits
HIT_LOG_DIR = "data/logs"          # Directory for hit logs
