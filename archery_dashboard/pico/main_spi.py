# Pico W SPI-based detector: 4x ADXL345 via SPI with peak-tracking event capture.
# Amplitude-based localization — no INT pins, no I2C mux.
# Sends hit_bundle packets to Pi via UDP (same format as main.py for compatibility).
# ================== main_spi.py (SPI + peak-tracking) ==================
import machine, network, socket, time, math, ujson, struct  # type: ignore
from secrets import SSID, PASSWORD

# ---------- USER CONFIG ----------
NODE_ID      = "PICO_A01"
CHANNELS     = [0, 1, 2, 3]

PI_IP        = "192.168.41.62"
DEST_PORT    = 5005

ODR_HZ       = 1600  # Idle poll rate (SPI is ~35x faster than I2C+mux)
ADXL_ODR     = 0x0F  # ADXL345 internal ODR: 3200 Hz
TRIGGER_TIMEOUT_MS = 100   # Max event window (longer to allow peak-tracking)
REFRACT_MS   = 500

K_SIGMA      = 6.0
SIGMA_CAP    = 20.0
ALPHA_MEAN   = 0.02
ALPHA_SIGMA  = 0.02
ALPHA_MEAN_WARMUP  = 0.1
ALPHA_SIGMA_WARMUP = 0.1

WARMUP_MS        = 4000
QUIET_ARM_MS     = 400
MIN_MAG          = 350.0

DECLINE_COUNT_THRESHOLD = 16  # Consecutive declining samples to declare peak (5ms at 3200Hz)

# ---------- SPI CONFIG ----------
SPI_ID   = 0
PIN_SCK  = 18
PIN_MOSI = 19  # SDA on ADXL breakout
PIN_MISO = 16  # SDO on ADXL breakout
SPI_BAUD = 5_000_000  # 5 MHz (ADXL345 max)

CS_PINS = {
    0: 21,  # Sensor 0 (North)
    1: 20,  # Sensor 1 (West)
    2: 17,  # Sensor 2 (South)
    3: 22,  # Sensor 3 (East)
}

# ---------- ADXL345 REGISTERS ----------
REG_DEVID       = 0x00
REG_BW_RATE     = 0x2C
REG_POWER_CTL   = 0x2D
REG_DATA_FORMAT = 0x31
REG_DATAX0      = 0x32
REG_FIFO_CTL    = 0x38
REG_FIFO_STATUS = 0x39

FIFO_STREAM_MODE = 0x80  # bits[7:6]=10 = stream mode

# ---------- RUNTIME STATE ----------
running_mean, running_sigma, thr_now = {}, {}, {}
snapshot_thr = {}
peak_mag, peak_xyz = {}, {}
sum_energy, sum_energy2, sum_samples = {}, {}, {}

first_ch, seq = None, 0

armed = False
warmup_until = 0
last_over_ts = 0
active_channels = []  # Channels with detected sensors (set during init)

# Per-sensor peak detection state during EVENT
# States: "WATCHING", "RISING", "PEAKED"
ch_peak_state = {}
decline_count = {}
crossed_thr = {}
peak_time_us = {}

# Waveform buffer for peak-time interpolation
waveform = {}

SAMPLE_PERIOD_US = 312.5  # 1/3200Hz in microseconds

# ---------- SPI BUS & CS PINS ----------
spi = None
cs_pins = {}

def init_spi():
    global spi
    spi = machine.SPI(SPI_ID,
                      baudrate=SPI_BAUD,
                      polarity=1, phase=1,
                      sck=machine.Pin(PIN_SCK),
                      mosi=machine.Pin(PIN_MOSI),
                      miso=machine.Pin(PIN_MISO))
    for ch, gpio in CS_PINS.items():
        cs_pins[ch] = machine.Pin(gpio, machine.Pin.OUT, value=1)

# ---------- SPI PRIMITIVES (from spi_test.py) ----------
def spi_read(cs, reg, n=1):
    """Read n bytes from reg using given CS pin."""
    cmd = (reg | 0x80 | (0x40 if n > 1 else 0x00))
    cs.value(0)
    spi.write(bytes([cmd]))
    data = spi.read(n)
    cs.value(1)
    return data

def spi_write(cs, reg, val):
    """Write single byte to reg using given CS pin."""
    cs.value(0)
    spi.write(bytes([reg, val]))
    cs.value(1)

# ---------- SENSOR DETECT & INIT ----------
def detect_sensor(ch):
    """Check DEVID=0xE5 via SPI. Returns True if ADXL345 found."""
    cs = cs_pins[ch]
    devid = spi_read(cs, REG_DEVID, 1)[0]
    return devid == 0xE5

def init_adxl345(ch):
    """Configure ADXL345 via SPI — no interrupt registers needed."""
    cs = cs_pins[ch]
    spi_write(cs, REG_POWER_CTL, 0x00)      # standby
    time.sleep_ms(2)
    spi_write(cs, REG_DATA_FORMAT, 0x0B)     # full-res ±16g, 13-bit
    spi_write(cs, REG_BW_RATE, ADXL_ODR)    # 3200 Hz ODR
    spi_write(cs, REG_FIFO_CTL, 0x00)       # bypass mode (flush FIFO)
    spi_write(cs, REG_POWER_CTL, 0x08)      # measure mode
    spi_write(cs, REG_FIFO_CTL, FIFO_STREAM_MODE)  # stream mode

# ---------- FIFO READS ----------
def fifo_flush_latest(ch):
    """Flush FIFO, return only the newest sample (x, y, z). Used in IDLE."""
    cs = cs_pins[ch]
    count = spi_read(cs, REG_FIFO_STATUS, 1)[0] & 0x3F
    if count == 0:
        raw = spi_read(cs, REG_DATAX0, 6)
        return struct.unpack('<hhh', raw)
    # Read all, keep last (newest)
    for _ in range(count):
        raw = spi_read(cs, REG_DATAX0, 6)
    return struct.unpack('<hhh', raw)

def fifo_burst_read(ch):
    """Read all FIFO entries via SPI. Returns list of (x, y, z) tuples."""
    cs = cs_pins[ch]
    count = spi_read(cs, REG_FIFO_STATUS, 1)[0] & 0x3F
    if count == 0:
        return []
    samples = []
    for _ in range(count):
        raw = spi_read(cs, REG_DATAX0, 6)
        x, y, z = struct.unpack('<hhh', raw)
        samples.append((x, y, z))
    return samples

def fifo_read_single(ch):
    """Read single sample from sensor. Returns (x, y, z) or None if FIFO empty."""
    cs = cs_pins[ch]
    count = spi_read(cs, REG_FIFO_STATUS, 1)[0] & 0x3F
    if count == 0:
        return None
    raw = spi_read(cs, REG_DATAX0, 6)
    return struct.unpack('<hhh', raw)

# ---------- UTILITY ----------
def mag3(x, y, z):
    return math.sqrt(x*x + y*y + z*z)

# ---------- BASELINE ----------
def update_baseline(ch, m, warmup=False):
    a_mean = ALPHA_MEAN_WARMUP if warmup else ALPHA_MEAN
    a_sigma = ALPHA_SIGMA_WARMUP if warmup else ALPHA_SIGMA
    mu = running_mean[ch]
    sg = running_sigma[ch]
    mu = (1 - a_mean) * mu + a_mean * m
    dev = abs(m - mu)
    sg = (1 - a_sigma) * sg + a_sigma * dev
    if sg > SIGMA_CAP:
        sg = SIGMA_CAP
    running_mean[ch], running_sigma[ch] = mu, sg
    thr_now[ch] = mu + K_SIGMA * sg

def init_baselines(now):
    global warmup_until, armed
    for ch in CHANNELS:
        running_mean[ch] = 0.0
        running_sigma[ch] = 0.5
        thr_now[ch] = K_SIGMA * 0.5
    warmup_until = time.ticks_add(now, WARMUP_MS)
    armed = False

# ---------- PEAK-TIME INTERPOLATION ----------
def find_peak_time_interpolated(samples):
    """
    Find peak time with sub-sample accuracy using parabolic interpolation.
    samples: list of (time_us, magnitude) tuples
    Returns: interpolated peak time in microseconds
    """
    if len(samples) < 3:
        if not samples:
            return 0
        peak_idx = 0
        peak_val = samples[0][1]
        for i in range(1, len(samples)):
            if samples[i][1] > peak_val:
                peak_val = samples[i][1]
                peak_idx = i
        return samples[peak_idx][0]

    peak_idx = 0
    peak_val = samples[0][1]
    for i in range(1, len(samples)):
        if samples[i][1] > peak_val:
            peak_val = samples[i][1]
            peak_idx = i

    if peak_idx == 0 or peak_idx == len(samples) - 1:
        return samples[peak_idx][0]

    t0, y0 = samples[peak_idx - 1]
    t1, y1 = samples[peak_idx]
    t2, y2 = samples[peak_idx + 1]

    dt = (t2 - t0) / 2.0
    denom = 2.0 * (y0 - 2.0 * y1 + y2)
    if abs(denom) < 0.001:
        return t1

    offset = (y0 - y2) / denom
    if offset > 0.5:
        offset = 0.5
    elif offset < -0.5:
        offset = -0.5

    peak_time = t1 + int(offset * dt)
    return peak_time

# ---------- PER-SAMPLE PEAK TRACKING ----------
def init_event_state():
    """Initialize per-sensor peak detection state for a new event."""
    for ch in active_channels:
        ch_peak_state[ch] = "WATCHING"
        decline_count[ch] = 0
        crossed_thr[ch] = False
        peak_time_us[ch] = 0
        peak_mag[ch] = 0.0
        peak_xyz[ch] = (0, 0, 0)
        sum_energy[ch] = 0.0
        sum_energy2[ch] = 0.0
        sum_samples[ch] = 0
        waveform[ch] = []

def process_event_sample(ch, x, y, z, t_us):
    """Process one sample for peak tracking during EVENT phase.
    Updates per-sensor state: peak_mag, peak_xyz, peak_time_us, energy accumulators."""
    m = mag3(x, y, z)

    # Waveform capture for interpolation
    waveform[ch].append((t_us, m))

    # Energy accumulation (above baseline)
    mu = running_mean.get(ch, 0.0)
    e = m - mu
    if e > 0:
        sum_energy[ch] = sum_energy.get(ch, 0.0) + e
        sum_energy2[ch] = sum_energy2.get(ch, 0.0) + (e * e)
    sum_samples[ch] = sum_samples.get(ch, 0) + 1

    # Peak tracking
    if m > peak_mag.get(ch, 0.0):
        peak_mag[ch] = m
        peak_xyz[ch] = (x, y, z)
        peak_time_us[ch] = t_us
        decline_count[ch] = 0
    else:
        decline_count[ch] += 1

    # Threshold crossing check
    if m > snapshot_thr.get(ch, 0.0):
        crossed_thr[ch] = True

    # State transitions
    state = ch_peak_state[ch]
    if state == "WATCHING":
        if crossed_thr[ch]:
            ch_peak_state[ch] = "RISING"
    elif state == "RISING":
        if crossed_thr[ch] and decline_count[ch] >= DECLINE_COUNT_THRESHOLD:
            ch_peak_state[ch] = "PEAKED"

# ---------- NETWORK ----------
udp = None
DEST_IP = None

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    while not wlan.isconnected():
        time.sleep_ms(100)
    ip, netmask, gw, dns = wlan.ifconfig()
    print("Wi-Fi:", (ip, netmask, gw, dns))
    return wlan

def init_udp_unicast(pi_ip):
    global udp, DEST_IP
    DEST_IP = pi_ip
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print("UDP unicast ->", DEST_IP, DEST_PORT)

def send_bundle(b):
    try:
        udp.sendto(ujson.dumps(b).encode(), (DEST_IP, DEST_PORT))
    except Exception as e:
        print("send error:", e)

# ---------- BUNDLE BUILDER ----------
def build_bundle(now):
    chs = {}
    for ch in CHANNELS:
        x, y, z = peak_xyz.get(ch, (0, 0, 0))
        chs[str(ch)] = {
            "peak":    round(peak_mag.get(ch, 0.0), 1),
            "energy":  round(sum_energy.get(ch, 0.0), 1),
            "energy2": round(sum_energy2.get(ch, 0.0), 1),
            "samples": int(sum_samples.get(ch, 0)),
            "x": x, "y": y, "z": z,
            "thr":    round(snapshot_thr.get(ch, 0.0), 1),
            "int_us": 0  # No interrupt timestamps in SPI mode
        }

    # TDOA: all -1 (no INT-based TDOA in SPI mode)
    tdoa = {str(ch): -1 for ch in CHANNELS}

    # Peak TDOA from waveform interpolation
    peak_times = {}
    sample_counts = {}
    for ch in CHANNELS:
        samples = waveform.get(ch, [])
        sample_counts[str(ch)] = len(samples)
        if len(samples) >= 3:
            peak_times[ch] = find_peak_time_interpolated(samples)

    peak_tdoa = {}
    if peak_times:
        t0 = min(peak_times.values())
        for ch in CHANNELS:
            if ch in peak_times:
                peak_tdoa[str(ch)] = peak_times[ch] - t0
            else:
                peak_tdoa[str(ch)] = -1

    return {
        "type": "hit_bundle", "node": NODE_ID, "seq": seq, "t_ms": now,
        "first": first_ch, "order": [first_ch] if first_ch is not None else [],
        "trigger_timeout_ms": TRIGGER_TIMEOUT_MS, "refract_ms": REFRACT_MS,
        "fw_ver": "spi_v1",
        "K_SIGMA": K_SIGMA, "SIGMA_CAP": SIGMA_CAP,
        "channels": CHANNELS,
        "ch": chs,
        "tdoa_us": tdoa,
        "peak_tdoa_us": peak_tdoa,
        "sample_count": sample_counts
    }

# ---------- MAIN LOOP ----------
def main_loop():
    global armed, last_over_ts, first_ch, seq

    now0 = time.ticks_ms()
    init_baselines(now0)
    target_us = int(1_000_000 / ODR_HZ)

    state = "IDLE"
    event_start_ms = 0
    refract_until = 0

    while True:
        loop_start_us = time.ticks_us()
        now = time.ticks_ms()

        if state == "IDLE":
            for ch in active_channels:
                try:
                    x, y, z = fifo_flush_latest(ch)
                except Exception:
                    continue
                m = mag3(x, y, z)

                if time.ticks_diff(now, warmup_until) < 0:
                    # Warmup phase
                    update_baseline(ch, m, warmup=True)
                elif not armed:
                    # Quiet-arm phase
                    update_baseline(ch, m)
                    if time.ticks_diff(now, last_over_ts) > QUIET_ARM_MS:
                        armed = True
                        print("ARMED", now)
                else:
                    # Armed — check for trigger
                    if m > thr_now[ch] and m >= MIN_MAG:
                        # TRIGGER
                        print("TRIG", now, "ch", ch, "mag", round(m, 1))
                        state = "EVENT"
                        event_start_ms = now
                        first_ch = ch

                        # Snapshot thresholds and init event state
                        for c in active_channels:
                            snapshot_thr[c] = thr_now.get(c, 0.0)
                        init_event_state()

                        # Seed triggering channel with current sample
                        peak_mag[ch] = m
                        peak_xyz[ch] = (x, y, z)
                        peak_time_us[ch] = time.ticks_us()
                        sum_energy[ch] = max(0.0, m - running_mean.get(ch, 0.0))
                        e = m - running_mean.get(ch, 0.0)
                        sum_energy2[ch] = (e * e) if e > 0 else 0.0
                        sum_samples[ch] = 1
                        crossed_thr[ch] = True
                        ch_peak_state[ch] = "RISING"
                        waveform[ch].append((time.ticks_us(), m))
                        break
                    else:
                        update_baseline(ch, m)
                        # Track last time anything was elevated (for quiet-arm)
                        mu = running_mean.get(ch, 0.0)
                        sg = running_sigma.get(ch, 0.5)
                        if (m - mu) > (K_SIGMA * sg):
                            last_over_ts = now

            # Adaptive sleep to maintain target poll rate
            if state == "IDLE":
                elapsed_us = time.ticks_diff(time.ticks_us(), loop_start_us)
                if elapsed_us < target_us:
                    time.sleep_us(target_us - elapsed_us)

        elif state == "EVENT":
            # Round-robin: read one sample from each sensor per iteration
            any_read = False
            for ch in active_channels:
                if ch_peak_state.get(ch) == "PEAKED":
                    continue  # Already peaked, skip reads

                try:
                    sample = fifo_read_single(ch)
                except Exception:
                    continue

                if sample:
                    any_read = True
                    x, y, z = sample
                    process_event_sample(ch, x, y, z, time.ticks_us())

            # Small yield if no samples available (prevents tight spin)
            if not any_read:
                time.sleep_us(100)

            # Check exit conditions
            now = time.ticks_ms()
            all_peaked = all(ch_peak_state.get(c) == "PEAKED" for c in active_channels)
            timed_out = time.ticks_diff(now, event_start_ms) >= TRIGGER_TIMEOUT_MS

            if all_peaked or timed_out:
                reason = "all_peaked" if all_peaked else "timeout"
                latency = time.ticks_diff(time.ticks_ms(), event_start_ms)

                # Per-channel state summary
                states = {c: ch_peak_state.get(c, "?") for c in CHANNELS}
                peaks = {c: round(peak_mag.get(c, 0.0), 1) for c in CHANNELS}
                print("SEND", time.ticks_ms(), "seq", seq, "reason", reason,
                      "latency_ms", latency, "states", states, "peaks", peaks)

                send_bundle(build_bundle(time.ticks_ms()))
                seq += 1

                state = "REFRACTORY"
                refract_until = time.ticks_add(time.ticks_ms(), REFRACT_MS)
                armed = False

        elif state == "REFRACTORY":
            if time.ticks_diff(now, refract_until) >= 0:
                state = "IDLE"
                # Reset baselines gently after event
                last_over_ts = time.ticks_ms()
                armed = False
            else:
                time.sleep_ms(10)

# ---------- ENTRY ----------
def main():
    print("=== main_spi.py (SPI + peak-tracking) ===")

    print("Connecting Wi-Fi ...")
    connect_wifi()
    init_udp_unicast(PI_IP)

    print("Initializing SPI bus ...")
    init_spi()

    print("Detecting sensors ...")
    for ch in CHANNELS:
        if detect_sensor(ch):
            active_channels.append(ch)
            print("CH{}: ADXL345 found (CS=GP{})".format(ch, CS_PINS[ch]))
        else:
            devid = spi_read(cs_pins[ch], REG_DEVID, 1)[0]
            print("CH{}: NOT found (CS=GP{}, got 0x{:02X})".format(ch, CS_PINS[ch], devid))

    if not active_channels:
        raise OSError("No ADXL345 sensors detected! Check wiring.")

    print("{} sensor(s) active: {}".format(len(active_channels), active_channels))

    print("Configuring sensors ...")
    for ch in active_channels:
        init_adxl345(ch)
    time.sleep_ms(10)

    print("Config: full-res +/-16g, 3200Hz ODR, SPI @ {}MHz".format(SPI_BAUD // 1_000_000))
    print("Detection: K_SIGMA={}, MIN_MAG={}, SIGMA_CAP={}".format(K_SIGMA, MIN_MAG, SIGMA_CAP))
    print("Event: timeout={}ms, decline_threshold={}, refractory={}ms".format(
        TRIGGER_TIMEOUT_MS, DECLINE_COUNT_THRESHOLD, REFRACT_MS))
    print("Starting loop ...")
    main_loop()

main()
