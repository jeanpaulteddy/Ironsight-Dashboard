# backend/screenshot.py
import aiohttp
import os
import config

# Try to import camera module for direct frame access
try:
    import camera
    _camera_available = True
except ImportError:
    _camera_available = False


def capture_screenshot_direct(output_path: str) -> bool:
    """
    Capture a screenshot directly from the camera module (no HTTP).

    Args:
        output_path: Relative path where to save the JPEG (e.g., "session_1/shot_1.jpg")

    Returns:
        True if successful, False otherwise
    """
    if not _camera_available:
        print("[SCREENSHOT] Camera module not available")
        return False

    frame = camera.get_latest_frame()
    if frame is None:
        print("[SCREENSHOT] No frame available from camera")
        return False

    try:
        # Construct full path
        screenshots_dir = os.path.join(os.path.dirname(__file__), config.SCREENSHOTS_DIR)
        full_path = os.path.join(screenshots_dir, output_path)

        # Create directory if needed
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Save frame
        with open(full_path, 'wb') as f:
            f.write(frame)

        print(f"[SCREENSHOT] Saved screenshot to {output_path} ({len(frame)} bytes)")
        return True
    except IOError as e:
        print(f"[SCREENSHOT] File I/O error: {e}")
        return False
    except Exception as e:
        print(f"[SCREENSHOT] Unexpected error: {e}")
        return False


async def capture_screenshot(stream_url: str, output_path: str) -> bool:
    """
    Capture a single JPEG frame from an MJPEG stream.

    Args:
        stream_url: URL of the MJPEG stream (e.g., "http://localhost:8081/stream")
        output_path: Relative path where to save the JPEG (e.g., "session_1/shot_1.jpg")

    Returns:
        True if successful, False otherwise
    """
    try:
        # Construct full path
        screenshots_dir = os.path.join(os.path.dirname(__file__), config.SCREENSHOTS_DIR)
        full_path = os.path.join(screenshots_dir, output_path)

        # Create directory if needed
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Fetch stream with timeout
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(stream_url) as resp:
                if resp.status != 200:
                    print(f"[SCREENSHOT] Failed to fetch stream: HTTP {resp.status}")
                    return False

                # Read MJPEG stream and extract first frame
                # MJPEG format: --boundary\r\nContent-Type: image/jpeg\r\nContent-Length: ...\r\n\r\n<JPEG data>
                boundary = None
                jpeg_data = None

                # Read response in chunks
                buffer = b''
                async for chunk in resp.content.iter_chunked(4096):
                    buffer += chunk

                    # Look for boundary if we haven't found it yet
                    if boundary is None:
                        # MJPEG streams often use --boundary format
                        if b'--' in buffer:
                            lines = buffer.split(b'\r\n')
                            for line in lines:
                                if line.startswith(b'--'):
                                    boundary = line
                                    print(f"[SCREENSHOT] Detected boundary: {boundary}")
                                    break

                    # Look for JPEG start marker (0xFFD8)
                    jpeg_start = buffer.find(b'\xff\xd8')
                    if jpeg_start >= 0:
                        # Look for JPEG end marker (0xFFD9)
                        jpeg_end = buffer.find(b'\xff\xd9', jpeg_start)
                        if jpeg_end >= 0:
                            # Extract complete JPEG frame
                            jpeg_data = buffer[jpeg_start:jpeg_end + 2]
                            break

                    # Limit buffer size to prevent memory issues
                    if len(buffer) > 1024 * 1024:  # 1MB max
                        print("[SCREENSHOT] Buffer exceeded 1MB, truncating")
                        buffer = buffer[-512 * 1024:]  # Keep last 512KB

                if jpeg_data:
                    # Save to file
                    with open(full_path, 'wb') as f:
                        f.write(jpeg_data)
                    print(f"[SCREENSHOT] Saved screenshot to {output_path} ({len(jpeg_data)} bytes)")
                    return True
                else:
                    print("[SCREENSHOT] No JPEG frame found in stream")
                    return False

    except aiohttp.ClientError as e:
        print(f"[SCREENSHOT] HTTP error capturing screenshot: {e}")
        return False
    except IOError as e:
        print(f"[SCREENSHOT] File I/O error: {e}")
        return False
    except Exception as e:
        print(f"[SCREENSHOT] Unexpected error capturing screenshot: {e}")
        return False

async def capture_screenshot_simple(stream_url: str, output_path: str) -> bool:
    """
    Simplified screenshot capture - tries multiple methods:
    1. Direct frame access from camera module (fastest, most reliable)
    2. Snapshot HTTP endpoint
    3. MJPEG stream parsing (fallback)
    """
    # Method 1: Try direct frame access first (no network overhead)
    if _camera_available:
        if capture_screenshot_direct(output_path):
            return True
        print("[SCREENSHOT] Direct capture failed, trying HTTP fallback")

    # Method 2: Try snapshot endpoint
    snapshot_url = stream_url.replace('/stream', '/snapshot') if '/stream' in stream_url else stream_url + '/snapshot'

    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Try snapshot endpoint
            async with session.get(snapshot_url) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    if 'image/jpeg' in content_type or 'image/jpg' in content_type:
                        # Direct JPEG response
                        jpeg_data = await resp.read()

                        # Save to file
                        screenshots_dir = os.path.join(os.path.dirname(__file__), config.SCREENSHOTS_DIR)
                        full_path = os.path.join(screenshots_dir, output_path)
                        os.makedirs(os.path.dirname(full_path), exist_ok=True)

                        with open(full_path, 'wb') as f:
                            f.write(jpeg_data)

                        print(f"[SCREENSHOT] Saved snapshot to {output_path} ({len(jpeg_data)} bytes)")
                        return True
    except Exception as e:
        print(f"[SCREENSHOT] Snapshot endpoint failed: {e}, falling back to MJPEG parsing")

    # Fall back to MJPEG stream parsing
    return await capture_screenshot(stream_url, output_path)
