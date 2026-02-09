# backend/camera.py
"""
Integrated PiCamera2/IMX500 camera module for archery dashboard.
Handles pose estimation, MJPEG streaming, and screenshot capture.
"""
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Dict, Any

import numpy as np # type: ignore
import cv2 # type: ignore
import config

# Thread-safe storage for latest frame and pose
_latest_jpeg: Optional[bytes] = None
_latest_pose: Optional[Dict[str, Any]] = None
_lock = threading.Lock()

# Camera state
_camera_running = False
_camera_error: Optional[str] = None

# Configuration from config.py (can be overridden in start_camera)
MJPEG_PORT = getattr(config, 'CAMERA_MJPEG_PORT', 8081)
MODEL_PATH = getattr(config, 'CAMERA_MODEL_PATH', "/usr/share/imx500-models/imx500_network_higherhrnet_coco.rpk")
DETECTION_THRESHOLD = getattr(config, 'CAMERA_DETECTION_THRESHOLD', 0.3)
WINDOW_SIZE_H_W = (480, 640)


def get_latest_frame() -> Optional[bytes]:
    """Get the latest JPEG frame (thread-safe)."""
    with _lock:
        return _latest_jpeg


def get_latest_pose() -> Optional[Dict[str, Any]]:
    """Get the latest pose/posture data (thread-safe)."""
    with _lock:
        return _latest_pose


def is_camera_running() -> bool:
    """Check if camera is running."""
    return _camera_running


def get_camera_error() -> Optional[str]:
    """Get camera initialization error if any."""
    return _camera_error


def _set_latest_frame(frame: bytes) -> None:
    global _latest_jpeg
    with _lock:
        _latest_jpeg = frame


def _set_latest_pose(pose: Dict[str, Any]) -> None:
    global _latest_pose
    with _lock:
        _latest_pose = pose


# ============================================================
# MJPEG HTTP Server
# ============================================================

class _MJPEGHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress request logging
        pass

    def do_GET(self):
        if self.path == "/status":
            import json
            status = {
                "running": _camera_running,
                "error": _camera_error,
                "has_frame": _latest_jpeg is not None,
            }
            body = json.dumps(status).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/snapshot":
            # Return a single JPEG frame
            frame = get_latest_frame()
            if frame is None:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"No frame available")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(frame)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(frame)
            return

        if self.path not in ("/", "/stream"):
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()

        try:
            while True:
                frame = get_latest_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue

                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                time.sleep(0.05)  # ~20 fps cap
        except Exception:
            # Client disconnected
            pass


def _start_mjpeg_server():
    """Start the MJPEG HTTP server in a daemon thread."""
    srv = HTTPServer(("0.0.0.0", MJPEG_PORT), _MJPEGHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"[CAMERA] MJPEG server started at http://0.0.0.0:{MJPEG_PORT}/stream")
    print(f"[CAMERA] Snapshot endpoint at http://0.0.0.0:{MJPEG_PORT}/snapshot")


# ============================================================
# Posture Analysis
# ============================================================

def _compute_angle(a, b, c):
    """Return angle ABC (in degrees) given 3 points a, b, c as (x, y)."""
    a = np.array(a, dtype=np.float32)
    b = np.array(b, dtype=np.float32)
    c = np.array(c, dtype=np.float32)

    ba = a - b
    bc = c - b

    denom = (np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-6)
    cosang = np.dot(ba, bc) / denom
    cosang = np.clip(cosang, -1.0, 1.0)
    return np.degrees(np.arccos(cosang))


def _analyse_posture(person_kp):
    """Compute archery-related posture metrics and return a score + messages."""
    # COCO keypoint indices
    NOSE = 0
    L_SHOULDER, R_SHOULDER = 5, 6
    L_ELBOW, R_ELBOW = 7, 8
    L_WRIST, R_WRIST = 9, 10
    L_HIP, R_HIP = 11, 12

    # Extract points (x, y)
    nose = person_kp[NOSE][:2]
    shoulder_l = person_kp[L_SHOULDER][:2]
    shoulder_r = person_kp[R_SHOULDER][:2]
    elbow_r = person_kp[R_ELBOW][:2]
    wrist_r = person_kp[R_WRIST][:2]
    hip_l = person_kp[L_HIP][:2]
    hip_r = person_kp[R_HIP][:2]

    messages = []
    score = 100.0

    # 1) Right elbow angle
    elbow_angle_r = _compute_angle(shoulder_r, elbow_r, wrist_r)
    if 165 <= elbow_angle_r <= 190:
        pass
    elif 150 <= elbow_angle_r < 165:
        score -= 5
        messages.append("Elbow slightly bent")
    else:
        score -= 15
        messages.append("Elbow too bent, try to straighten your arm")

    # 2) Shoulder line tilt
    ref_above_l = (shoulder_l[0], shoulder_l[1] - 10)
    shoulder_tilt = _compute_angle(ref_above_l, shoulder_l, shoulder_r)
    tilt_from_horizontal = abs(shoulder_tilt - 90)
    if tilt_from_horizontal <= 8:
        pass
    elif tilt_from_horizontal <= 15:
        score -= 5
        messages.append("Shoulders slightly tilted")
    else:
        score -= 10
        messages.append("Shoulders not aligned, try to level them")

    # 3) Torso lean (left hip to left shoulder)
    ref_above_hip = (hip_l[0], hip_l[1] - 10)
    torso_angle = _compute_angle(ref_above_hip, hip_l, shoulder_l)
    torso_lean = abs(torso_angle - 90)
    if torso_lean <= 8:
        pass
    elif torso_lean <= 15:
        score -= 5
        messages.append("Torso slightly leaning")
    else:
        score -= 10
        messages.append("Torso leaning too much, stand more upright")

    # 4) Head tilt (nose vs midpoint of shoulders)
    shoulders_mid = (0.5 * (shoulder_l[0] + shoulder_r[0]),
                     0.5 * (shoulder_l[1] + shoulder_r[1]))
    ref_above_mid = (shoulders_mid[0], shoulders_mid[1] - 10)
    head_angle = _compute_angle(ref_above_mid, shoulders_mid, nose)
    head_tilt = abs(head_angle - 90)
    if head_tilt <= 10:
        pass
    elif head_tilt <= 20:
        score -= 3
        messages.append("Head slightly tilted")
    else:
        score -= 7
        messages.append("Head tilt is large, keep your head more upright")

    score = max(0, min(100, score))

    return {
        "type": "pose",
        "ts": time.time(),
        "elbow_angle_r": float(elbow_angle_r),
        "shoulder_tilt_raw": float(shoulder_tilt),
        "shoulder_tilt_from_horizontal": float(tilt_from_horizontal),
        "torso_lean": float(torso_lean),
        "head_tilt": float(head_tilt),
        "score": float(score),
        "messages": messages
    }


# ============================================================
# Camera Initialization and Processing
# ============================================================

def start_camera(
    model_path: str = None,
    detection_threshold: float = None,
    mjpeg_port: int = None
) -> bool:
    """
    Initialize and start the PiCamera2/IMX500 camera.

    Returns True if camera started successfully, False otherwise.
    On systems without PiCamera2 (e.g., development laptops), this will
    fail gracefully and return False.
    """
    global _camera_running, _camera_error, MJPEG_PORT, MODEL_PATH, DETECTION_THRESHOLD

    if model_path:
        MODEL_PATH = model_path
    if detection_threshold:
        DETECTION_THRESHOLD = detection_threshold
    if mjpeg_port:
        MJPEG_PORT = mjpeg_port

    # Start MJPEG server (even if camera fails, we can serve test frames)
    _start_mjpeg_server()

    # Try to import PiCamera2 dependencies
    try:
        from picamera2 import CompletedRequest, MappedArray, Picamera2 # pyright: ignore[reportMissingImports]
        from picamera2.devices.imx500 import IMX500, NetworkIntrinsics # pyright: ignore[reportMissingImports]
        from picamera2.devices.imx500.postprocess import COCODrawer # pyright: ignore[reportMissingImports]
        from picamera2.devices.imx500.postprocess_highernet import postprocess_higherhrnet # pyright: ignore[reportMissingImports]
    except ImportError as e:
        _camera_error = f"PiCamera2 not available: {e}"
        print(f"[CAMERA] {_camera_error}")
        print("[CAMERA] Running without camera (screenshots will not be captured)")
        return False

    def _run_camera():
        global _camera_running, _camera_error

        try:
            # Initialize IMX500
            imx500 = IMX500(MODEL_PATH)
            intrinsics = imx500.network_intrinsics
            if not intrinsics:
                intrinsics = NetworkIntrinsics()
                intrinsics.task = "pose estimation"
            elif intrinsics.task != "pose estimation":
                _camera_error = "Network is not a pose estimation task"
                print(f"[CAMERA] {_camera_error}")
                return

            # Set defaults
            if intrinsics.inference_rate is None:
                intrinsics.inference_rate = 10
            if intrinsics.labels is None:
                try:
                    with open("assets/coco_labels.txt", "r") as f:
                        intrinsics.labels = f.read().splitlines()
                except FileNotFoundError:
                    intrinsics.labels = ["person"]
            intrinsics.update_with_defaults()

            # Create drawer for skeleton visualization
            categories = intrinsics.labels
            categories = [c for c in categories if c and c != "-"]
            drawer = COCODrawer(categories, imx500, needs_rescale_coords=False)

            # State for pose estimation
            last_boxes = None
            last_scores = None
            last_keypoints = None

            def parse_output(metadata: dict):
                nonlocal last_boxes, last_scores, last_keypoints
                np_outputs = imx500.get_outputs(metadata=metadata, add_batch=True)
                if np_outputs is not None:
                    keypoints, scores, boxes = postprocess_higherhrnet(
                        outputs=np_outputs,
                        img_size=WINDOW_SIZE_H_W,
                        img_w_pad=(0, 0),
                        img_h_pad=(0, 0),
                        detection_threshold=DETECTION_THRESHOLD,
                        network_postprocess=True
                    )
                    if scores is not None and len(scores) > 0:
                        last_keypoints = np.reshape(np.stack(keypoints, axis=0), (len(scores), 17, 3))
                        last_boxes = [np.array(b) for b in boxes]
                        last_scores = np.array(scores)
                return last_boxes, last_scores, last_keypoints

            def pre_callback(request: CompletedRequest):
                nonlocal last_boxes, last_scores, last_keypoints

                # Parse pose estimation output
                boxes, scores, keypoints = parse_output(request.get_metadata())

                # Draw skeleton on frame
                with MappedArray(request, 'main') as m:
                    if boxes is not None and len(boxes) > 0:
                        drawer.annotate_image(
                            m.array, boxes, scores,
                            np.zeros(scores.shape), keypoints,
                            DETECTION_THRESHOLD,
                            DETECTION_THRESHOLD,
                            request.get_metadata(), picam2, 'main'
                        )

                    # Encode frame as JPEG and store
                    frame_bgr = cv2.cvtColor(m.array, cv2.COLOR_RGB2BGR)
                    ok, jpg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    if ok:
                        _set_latest_frame(jpg.tobytes())

                # Analyze posture if person detected
                if keypoints is not None and len(keypoints) > 0:
                    person_kp = keypoints[0]  # First detected person
                    posture = _analyse_posture(person_kp)
                    _set_latest_pose(posture)

            # Start camera
            picam2 = Picamera2(imx500.camera_num)
            config = picam2.create_preview_configuration(controls={'FrameRate': intrinsics.fps})
            imx500.show_network_fw_progress_bar()
            picam2.start(config, show_preview=False)
            imx500.set_auto_aspect_ratio()
            picam2.pre_callback = pre_callback

            _camera_running = True
            print("[CAMERA] PiCamera2/IMX500 started successfully")

            # Keep thread alive
            while _camera_running:
                time.sleep(0.5)

        except Exception as e:
            _camera_error = str(e)
            print(f"[CAMERA] Error starting camera: {e}")
            _camera_running = False

    # Run camera in daemon thread
    t = threading.Thread(target=_run_camera, daemon=True)
    t.start()

    # Give it a moment to initialize
    time.sleep(1.0)
    return _camera_running or _camera_error is None


def stop_camera():
    """Stop the camera (if running)."""
    global _camera_running
    _camera_running = False
    print("[CAMERA] Camera stopped")
