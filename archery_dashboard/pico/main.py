# Pico W code for detector-only mode: always sends hit candidates to Pi, which classifies + maps them.
# Running on the Pico so ignore errors about missing modules (e.g. network, socket). This is the "main.py" that the Pico executes on boot.
# ================== main.py (PICO detector-only: always sends events; Pi classifies + maps) ==================
import machine, network, socket, time, math, ujson, struct
from secrets import SSID, PASSWORD

# ---------- USER CONFIG ----------
NODE_ID      = "PICO_A01"
CHANNELS     = [0, 1, 2, 3]

PI_IP        = "192.168.41.62"
DEST_PORT    = 5005
DEBUG_PINGS  = False

ODR_HZ       = 400   # Loop rate (actual I2C limits this)
ADXL_ODR     = 0x0F  # ADXL345 internal ODR: 3200 Hz for TDOA precision
EVENT_MS     = 120
REFRACT_MS   = 500

K_SIGMA      = 6.0
SIGMA_CAP    = 20.0
ALPHA_MEAN   = 0.02
ALPHA_SIGMA  = 0.02
ALPHA_MEAN_WARMUP  = 0.1   # 5x faster convergence during warmup
ALPHA_SIGMA_WARMUP = 0.1

WARMUP_MS        = 4000    # Reduced from 8000 (warmup alpha 5x faster)
QUIET_ARM_MS     = 400     # was 1500; shorter since Pi now classifies
MIN_MAG          = 350.0
CONSEC_REQUIRED  = 1

I2C_BUS_ID   = 0
I2C_SDA_PIN  = 0
I2C_SCL_PIN  = 1
I2C_FREQ     = 140_000  # 140kHz (max stable with long cables, was 100kHz)

TCA_ADDR     = 0x70
ADXL_ADDRS   = [0x53, 0x1D]

# ---------- TDOA CONFIG ----------
# GPIO pins for INT1 from each sensor (direct wires, not through mux)
INT_PINS = {0: 5, 1: 3, 2: 4, 3: 2}  # channel: GPIO pin

# ADXL345 interrupt registers
REG_THRESH_ACT    = 0x24  # Activity threshold
REG_ACT_INACT_CTL = 0x27  # Activity control
REG_INT_ENABLE    = 0x2E  # Interrupt enable
REG_INT_MAP       = 0x2F  # Interrupt mapping
REG_INT_SOURCE    = 0x30  # Interrupt source (read to clear)

# ADXL345 FIFO registers
REG_FIFO_CTL      = 0x38  # FIFO control (mode, watermark)
REG_FIFO_STATUS   = 0x39  # FIFO status (entry count)
FIFO_STREAM_MODE  = 0x80  # bits[7:6]=10 = stream mode, continuously overwrites oldest

# Activity threshold for TDOA interrupts (62.5mg per LSB)
# 8 = 500mg, 16 = 1g, 32 = 2g, 48 = 3g
# Higher = less sensitive to vibration, but still catches arrow impacts
ACTIVITY_THRESHOLD = 80
TDOA_ENABLED = True  # Feature flag for easy disable

# ---------- RUNTIME STATE ----------
running_mean, running_sigma, thr_now = {}, {}, {}
in_event = False
in_refract = False
event_start_ms = 0
refract_until = 0
snapshot_thr, peak_mag, peak_xyz = {}, {}, {}

sum_energy, sum_energy2, sum_samples = {}, {}, {}

first_ch, seq = None, 0
adxl_addr_by_ch = {}

armed = False
warmup_until = 0
last_over_ts = 0
consec_over = {}
last_debug_ping = 0

# TDOA interrupt state
int_timestamps = {}  # {channel: timestamp_us}
int_pins = {}        # {channel: Pin object}
tdoa_snapshot = {}   # Snapshot of timestamps at trigger time

# Waveform buffer for peak-time TDOA (full waveform capture)
waveform = {}        # {channel: [(time_us, magnitude), ...]}
MAX_WAVEFORM_SAMPLES = 400  # FIFO burst-reads at 3200Hz ODR: ~384 samples in 120ms event

# ---------- I2C AUTO-DISCOVERY ----------
MUX_ADDRS = [0x70,0x71,0x72,0x73,0x74,0x75,0x76,0x77]
COMMON_PINS = {
    0: [(0,1),(4,5),(8,9),(12,13),(16,17),(20,21)],
    1: [(2,3),(6,7),(10,11),(14,15),(18,19)]
}

def try_make_i2c(bus, sda, scl, freq):
    try:
        i2c_try = machine.I2C(bus, sda=machine.Pin(sda), scl=machine.Pin(scl), freq=freq)
        _ = i2c_try.scan()
        return i2c_try
    except Exception:
        return None

def auto_find_mux(initial_bus, initial_sda, initial_scl, freq):
    candidates = [(initial_bus, initial_sda, initial_scl)]
    for bus in [initial_bus, 1-initial_bus]:
        for (sda, scl) in COMMON_PINS.get(bus, []):
            if (bus,sda,scl) not in candidates:
                candidates.append((bus,sda,scl))
    tried_notes = []
    for (bus, sda, scl) in candidates:
        i2c_try = try_make_i2c(bus, sda, scl, freq)
        if not i2c_try:
            continue
        found = i2c_try.scan()
        for a in MUX_ADDRS:
            if a in found:
                return (i2c_try,bus,sda,scl,a)
        for a in MUX_ADDRS:
            try:
                i2c_try.writeto(a,b"\x00")
                if a in i2c_try.scan():
                    return (i2c_try,bus,sda,scl,a)
            except Exception:
                pass
        tried_notes.append("I2C{} GP{}/GP{} saw {}".format(bus,sda,scl,[hex(x) for x in found]))
    raise OSError("No PCA/TCA9548A found (0x70–0x77). Tried: " + " | ".join(tried_notes))

# ---------- LOW-LEVEL I2C + RECOVERY ----------
i2c = None
def mag3(x,y,z): return math.sqrt(x*x + y*y + z*z)

def _bitbang_bus_reset():
    scl = machine.Pin(I2C_SCL_PIN, machine.Pin.OUT, value=1)
    sda_in = machine.Pin(I2C_SDA_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    for _ in range(9):
        if sda_in.value() == 1:
            break
        scl.value(0); time.sleep_us(5)
        scl.value(1); time.sleep_us(5)
    sda_out = machine.Pin(I2C_SDA_PIN, machine.Pin.OUT, value=1)
    scl.value(1); time.sleep_us(5)
    sda_out.value(1); time.sleep_us(5)

def _reinit_i2c():
    global i2c
    i2c = machine.I2C(I2C_BUS_ID, sda=machine.Pin(I2C_SDA_PIN), scl=machine.Pin(I2C_SCL_PIN), freq=I2C_FREQ)
    time.sleep_ms(2)
    try:
        i2c.writeto(TCA_ADDR, b"\x00")
    except Exception:
        pass
    time.sleep_ms(1)

def _recover_bus_and_retry(func, *args, tries=2, **kwargs):
    last = None
    for _ in range(tries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last = e
            try:
                _bitbang_bus_reset()
                _reinit_i2c()
            except Exception:
                pass
            time.sleep_ms(2)
    raise last

def select_mux_channel(ch):
    def _do():
        # Simplified: direct channel select without deselect step
        # At 400kHz I2C, no delays needed - mux switches in <1µs
        i2c.writeto(TCA_ADDR, bytes([1<<ch]))
    try:
        _do()
    except Exception:
        _recover_bus_and_retry(_do)

def i2c_read_mem_retry(addr, reg, n, tries=3):
    last = None
    for _ in range(tries):
        try:
            return i2c.readfrom_mem(addr, reg, n)
        except Exception as e:
            last = e
            time.sleep_ms(2)
    raise last

def i2c_write_mem_retry(addr, reg, valbytes, tries=3):
    last = None
    for _ in range(tries):
        try:
            i2c.writeto_mem(addr, reg, valbytes)
            return
        except Exception as e:
            last = e
            time.sleep_ms(2)
    raise last

def adxl_read(ch, addr, start, length):
    select_mux_channel(ch)
    return i2c_read_mem_retry(addr, start, length)

def adxl_write(ch, addr, reg, val):
    select_mux_channel(ch)
    i2c_write_mem_retry(addr, reg, bytes([val]))

# ---------- ADXL345 SETUP/READ ----------
def detect_adxl_addr(ch):
    for addr in ADXL_ADDRS:
        try:
            d = adxl_read(ch, addr, 0x00, 1)
            if d and d[0] == 0xE5:
                return addr
        except Exception:
            pass
    return None

def init_adxl345(ch, addr):
    adxl_write(ch, addr, 0x2D, 0x00); time.sleep_ms(2)  # standby
    adxl_write(ch, addr, 0x31, 0x09)                  # full-res ±4g (prevents clipping on 38lb recurve impacts)
    adxl_write(ch, addr, 0x2C, ADXL_ODR)              # 3200 Hz ODR for TDOA precision

    # Configure activity interrupt for TDOA
    if TDOA_ENABLED:
        adxl_write(ch, addr, REG_THRESH_ACT, ACTIVITY_THRESHOLD)  # Activity threshold
        adxl_write(ch, addr, REG_ACT_INACT_CTL, 0x70)  # AC-coupled, XYZ axes
        adxl_write(ch, addr, REG_INT_MAP, 0x00)        # Map activity to INT1
        adxl_write(ch, addr, REG_INT_ENABLE, 0x10)     # Enable activity interrupt

    adxl_write(ch, addr, REG_FIFO_CTL, 0x00)           # bypass mode first (resets FIFO)
    adxl_write(ch, addr, 0x2D, 0x08)                  # measure mode
    adxl_write(ch, addr, REG_FIFO_CTL, FIFO_STREAM_MODE)  # stream mode (keeps latest 32 samples)

def read_adxl345(ch):
    addr = adxl_addr_by_ch[ch]
    try:
        d = adxl_read(ch, addr, 0x32, 6)
    except Exception:
        select_mux_channel(ch)
        d = i2c_read_mem_retry(addr, 0x32, 6)
    x, y, z = struct.unpack('<hhh', d)
    return (x, y, z)

def fifo_entry_count(ch):
    """Read number of entries currently in FIFO (0-32)."""
    addr = adxl_addr_by_ch[ch]
    d = adxl_read(ch, addr, REG_FIFO_STATUS, 1)
    return d[0] & 0x3F  # bits [5:0] = entry count

def fifo_read_all(ch):
    """Read and return all FIFO entries as list of (x, y, z) tuples.
    Each read from 0x32 pops the oldest entry. Returns newest last."""
    addr = adxl_addr_by_ch[ch]
    select_mux_channel(ch)
    count = i2c_read_mem_retry(addr, REG_FIFO_STATUS, 1)[0] & 0x3F
    samples = []
    for _ in range(count):
        d = i2c_read_mem_retry(addr, 0x32, 6)
        x, y, z = struct.unpack('<hhh', d)
        samples.append((x, y, z))
    return samples

def fifo_flush_get_latest(ch):
    """Flush FIFO and return only the most recent sample (x, y, z).
    Used during idle polling to get current data, not stale."""
    addr = adxl_addr_by_ch[ch]
    select_mux_channel(ch)
    count = i2c_read_mem_retry(addr, REG_FIFO_STATUS, 1)[0] & 0x3F
    if count == 0:
        d = i2c_read_mem_retry(addr, 0x32, 6)
        return struct.unpack('<hhh', d)
    # Read all, keep last (newest)
    for _ in range(count):
        d = i2c_read_mem_retry(addr, 0x32, 6)
    return struct.unpack('<hhh', d)

# ---------- BASELINE ----------
def update_baseline(ch, m, warmup=False):
    a_mean = ALPHA_MEAN_WARMUP if warmup else ALPHA_MEAN
    a_sigma = ALPHA_SIGMA_WARMUP if warmup else ALPHA_SIGMA
    mu = running_mean[ch]
    sg = running_sigma[ch]
    mu = (1-a_mean)*mu + a_mean*m
    dev = abs(m-mu)
    sg = (1-a_sigma)*sg + a_sigma*dev
    if sg > SIGMA_CAP: sg = SIGMA_CAP
    running_mean[ch], running_sigma[ch] = mu, sg
    thr_now[ch] = mu + K_SIGMA*sg

# ---------- TDOA INTERRUPTS ----------
def make_int_handler(ch):
    """Factory function to create channel-specific interrupt handler."""
    def handler(pin):
        global int_timestamps
        if ch not in int_timestamps:
            int_timestamps[ch] = time.ticks_us()
    return handler

def setup_interrupts():
    """Configure GPIO interrupts for each sensor's INT1 pin."""
    global int_pins
    if not TDOA_ENABLED:
        return
    for ch, gpio in INT_PINS.items():
        if ch in CHANNELS:
            pin = machine.Pin(gpio, machine.Pin.IN, machine.Pin.PULL_DOWN)
            pin.irq(trigger=machine.Pin.IRQ_RISING, handler=make_int_handler(ch))
            int_pins[ch] = pin
            print("INT{}: GP{} configured".format(ch, gpio))

def clear_interrupts():
    """Reset interrupt state and clear ADXL345 interrupt registers."""
    global int_timestamps
    int_timestamps = {}
    if not TDOA_ENABLED:
        return
    for ch in CHANNELS:
        if ch in adxl_addr_by_ch:
            try:
                adxl_read(ch, adxl_addr_by_ch[ch], REG_INT_SOURCE, 1)
            except:
                pass

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

# ---------- PEAK TIME INTERPOLATION ----------
def find_peak_time_interpolated(samples):
    """
    Find peak time with sub-sample accuracy using parabolic interpolation.
    samples: list of (time_us, magnitude) tuples
    Returns: interpolated peak time in microseconds
    """
    if len(samples) < 3:
        # Not enough for interpolation, return raw peak
        if not samples:
            return 0
        peak_idx = 0
        peak_val = samples[0][1]
        for i in range(1, len(samples)):
            if samples[i][1] > peak_val:
                peak_val = samples[i][1]
                peak_idx = i
        return samples[peak_idx][0]

    # Find peak index
    peak_idx = 0
    peak_val = samples[0][1]
    for i in range(1, len(samples)):
        if samples[i][1] > peak_val:
            peak_val = samples[i][1]
            peak_idx = i

    # Need neighbors for interpolation
    if peak_idx == 0 or peak_idx == len(samples) - 1:
        return samples[peak_idx][0]

    # Get three points around peak
    t0, y0 = samples[peak_idx - 1]
    t1, y1 = samples[peak_idx]
    t2, y2 = samples[peak_idx + 1]

    # Parabolic interpolation: find vertex of parabola through 3 points
    dt = (t2 - t0) / 2.0  # Average sample interval

    # Parabola vertex offset from center point
    denom = 2.0 * (y0 - 2.0*y1 + y2)
    if abs(denom) < 0.001:
        return t1  # Flat peak, return center

    offset = (y0 - y2) / denom  # Offset in sample units (-0.5 to +0.5)

    # Clamp offset to reasonable range
    if offset > 0.5:
        offset = 0.5
    elif offset < -0.5:
        offset = -0.5

    # Convert to microseconds
    peak_time = t1 + int(offset * dt)
    return peak_time

# ---------- PACKETS ----------
def build_bundle(now):
    chs = {}
    for ch in CHANNELS:
        x,y,z = peak_xyz.get(ch,(0,0,0))
        chs[str(ch)] = {
            "peak": round(peak_mag.get(ch,0.0),1),
            "energy":  round(sum_energy.get(ch, 0.0), 1),
            "energy2": round(sum_energy2.get(ch, 0.0), 1),
            "samples": int(sum_samples.get(ch, 0)),
            "x": x, "y": y, "z": z,
            "thr": round(snapshot_thr.get(ch,0.0),1),
            "int_us": tdoa_snapshot.get(ch, 0)  # Raw interrupt timestamp from snapshot
        }

    # Compute relative TDOA (reference to first interrupt) - legacy method
    tdoa = {}
    if tdoa_snapshot:
        t0 = min(tdoa_snapshot.values())
        for ch in CHANNELS:
            tdoa[str(ch)] = tdoa_snapshot.get(ch, t0) - t0

    # Compute interpolated peak times from waveform (new accurate method)
    peak_times = {}
    sample_counts = {}
    for ch in CHANNELS:
        samples = waveform.get(ch, [])
        sample_counts[str(ch)] = len(samples)
        if len(samples) >= 3:
            peak_times[ch] = find_peak_time_interpolated(samples)

    # Compute relative TDOA from interpolated peaks
    peak_tdoa = {}
    if peak_times:
        t0 = min(peak_times.values())
        for ch in CHANNELS:
            if ch in peak_times:
                peak_tdoa[str(ch)] = peak_times[ch] - t0
            else:
                peak_tdoa[str(ch)] = 0

    return {
        "type":"hit_bundle","node":NODE_ID,"seq":seq,"t_ms":now,
        "first":first_ch,"order":[first_ch] if first_ch is not None else [],
        "event_ms":EVENT_MS,"refract_ms":REFRACT_MS,
        "fw_ver":"tdoa_v2",  # Updated version for peak TDOA
        "K_SIGMA":K_SIGMA,"SIGMA_CAP":SIGMA_CAP,
        "channels": CHANNELS,
        "ch": chs,
        "tdoa_us": tdoa,           # Legacy: interrupt-based TDOA
        "peak_tdoa_us": peak_tdoa, # NEW: Interpolated peak-based TDOA
        "sample_count": sample_counts  # Debug: samples per channel
    }

# ---------- INIT BASELINES ----------
def init_baselines(now):
    global warmup_until, armed, consec_over
    for ch in CHANNELS:
        running_mean[ch] = 0.0
        running_sigma[ch] = 0.5
        thr_now[ch] = K_SIGMA * 0.5
        consec_over[ch] = 0
    warmup_until = time.ticks_add(now, WARMUP_MS)
    armed = False

# ---------- MAIN LOOP ----------
def main_loop():
    global in_event, in_refract, event_start_ms, refract_until
    global snapshot_thr, peak_mag, peak_xyz, first_ch, seq
    global sum_energy, sum_energy2, sum_samples
    global armed, last_over_ts, last_debug_ping
    global waveform  # For peak-time TDOA

    now0 = time.ticks_ms()
    init_baselines(now0)
    target_us = int(1_000_000 / ODR_HZ)  # Target loop period in microseconds

    while True:
        loop_start_us = time.ticks_us()
        now = time.ticks_ms()

        if in_event:
            # --- EVENT MODE: burst-read FIFO for dense 3200Hz waveforms ---
            t_us = time.ticks_us()
            for ch in CHANNELS:
                if ch not in adxl_addr_by_ch:
                    continue
                try:
                    samples = fifo_read_all(ch)
                except Exception:
                    continue
                for sx, sy, sz in samples:
                    m = mag3(sx, sy, sz)
                    if ch not in waveform:
                        waveform[ch] = []
                    if len(waveform[ch]) < MAX_WAVEFORM_SAMPLES:
                        waveform[ch].append((t_us, m))
                    if m > peak_mag.get(ch, 0.0):
                        peak_mag[ch] = m
                        peak_xyz[ch] = (sx, sy, sz)
                    mu = running_mean.get(ch, 0.0)
                    e = m - mu
                    if e > 0:
                        sum_energy[ch]  = sum_energy.get(ch, 0.0)  + e
                        sum_energy2[ch] = sum_energy2.get(ch, 0.0) + (e * e)
                    sum_samples[ch] = sum_samples.get(ch, 0) + 1

            if time.ticks_diff(now, event_start_ms) >= EVENT_MS:
                # Always send candidate bundle; Pi classifies
                event_max_peak = 0.0
                for c in CHANNELS:
                    pv = peak_mag.get(c, 0.0)
                    if pv > event_max_peak:
                        event_max_peak = pv
                e2_sum = 0.0
                for c in CHANNELS:
                    e2_sum += sum_energy2.get(c, 0.0)

                print("SEND", now, "seq", seq, "maxPeak", event_max_peak, "sumE2", e2_sum)
                send_bundle(build_bundle(now))
                seq += 1

                in_event = False; in_refract = True; armed = False
                refract_until = time.ticks_add(now, REFRACT_MS)
        else:
            # --- IDLE MODE: flush FIFO, use latest sample for baseline/trigger ---
            xyz, magv = {}, {}
            for ch in CHANNELS:
                if ch not in adxl_addr_by_ch:
                    continue
                try:
                    x, y, z = fifo_flush_get_latest(ch)
                except Exception:
                    try:
                        _bitbang_bus_reset(); _reinit_i2c()
                        x, y, z = fifo_flush_get_latest(ch)
                    except Exception:
                        continue
                xyz[ch] = (x, y, z)
                magv[ch] = mag3(x, y, z)

            if not magv:
                pass
            elif in_refract:
                if time.ticks_diff(refract_until, now) <= 0:
                    in_refract = False
            elif time.ticks_diff(now, warmup_until) < 0:
                for ch in magv: update_baseline(ch, magv[ch], warmup=True)
            else:
                if time.ticks_diff(now, last_over_ts) > QUIET_ARM_MS:
                    armed = True

                trig = None; any_over = False
                for ch in magv:
                    mu = running_mean.get(ch, 0.0)
                    sg = running_sigma.get(ch, 0.5)
                    delta = magv[ch] - mu
                    over = (delta > (K_SIGMA * sg)) and (magv[ch] >= MIN_MAG)
                    if over:
                        any_over = True
                        consec_over[ch] = min(CONSEC_REQUIRED, consec_over.get(ch,0)+1)
                        if consec_over[ch] >= CONSEC_REQUIRED and armed:
                            trig = ch; break
                    else:
                        consec_over[ch] = 0

                if any_over:
                    last_over_ts = now

                if trig is not None:
                    global tdoa_snapshot
                    # Adaptive TDOA wait: poll for all sensors, timeout at 15ms
                    if TDOA_ENABLED:
                        deadline = time.ticks_add(time.ticks_ms(), 15)
                        while len(int_timestamps) < len(CHANNELS):
                            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                                break
                            time.sleep_ms(1)
                        tdoa_snapshot = dict(int_timestamps)

                        # Diagnostic: show raw and relative timestamps
                        fired = [ch for ch in CHANNELS if ch in tdoa_snapshot]
                        missing = [ch for ch in CHANNELS if ch not in tdoa_snapshot]
                        if tdoa_snapshot:
                            t0 = min(tdoa_snapshot.values())
                            rel = {ch: tdoa_snapshot[ch] - t0 for ch in tdoa_snapshot}
                            first_int = min(tdoa_snapshot, key=tdoa_snapshot.get)
                            print("TDOA: fired={} missing={} first_int=ch{} rel_us={}".format(
                                fired, missing, first_int, rel))
                        else:
                            print("TDOA: NO INTERRUPTS FIRED! Check INT1 wiring.")

                        clear_interrupts()  # Clear for accurate timing on next event
                    else:
                        tdoa_snapshot = {}
                    print("TRIG", now, "ch", trig, "mag", magv.get(trig))
                    in_event = True; first_ch = trig; event_start_ms = now
                    snapshot_thr = {c: thr_now.get(c, 0.0) for c in CHANNELS}
                    peak_mag = {c: magv.get(c, 0.0) for c in CHANNELS}
                    peak_xyz = {c: xyz.get(c, (0,0,0)) for c in CHANNELS}
                    sum_energy  = {c: 0.0 for c in CHANNELS}
                    sum_energy2 = {c: 0.0 for c in CHANNELS}
                    sum_samples = {c: 0 for c in CHANNELS}
                    waveform = {c: [] for c in CHANNELS}  # Reset waveform buffer for new event
                else:
                    for ch in magv:
                        update_baseline(ch, magv[ch])

                    # Clear stale interrupts during idle (prevents old timestamps from persisting)
                    if TDOA_ENABLED and int_timestamps:
                        clear_interrupts()

        # Adaptive sleep: only sleep remaining time to hit target loop period
        elapsed_us = time.ticks_diff(time.ticks_us(), loop_start_us)
        if elapsed_us < target_us:
            time.sleep_us(target_us - elapsed_us)

# ---------- ENTRY ----------
def main():
    global i2c, I2C_BUS_ID, I2C_SDA_PIN, I2C_SCL_PIN, TCA_ADDR

    print("Connecting Wi-Fi …")
    connect_wifi()
    init_udp_unicast(PI_IP)

    print("Probing I2C …")
    i2c, bus, sda, scl, mux_addr = auto_find_mux(I2C_BUS_ID, I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ)
    I2C_BUS_ID, I2C_SDA_PIN, I2C_SCL_PIN, TCA_ADDR = bus, sda, scl, mux_addr
    print("I2C:", {"bus": bus, "sda": sda, "scl": scl, "freq": I2C_FREQ}, "mux", hex(mux_addr))
    print("I2C scan:", [hex(a) for a in i2c.scan()])

    ok = []
    for ch in CHANNELS:
        addr = detect_adxl_addr(ch)
        if addr:
            adxl_addr_by_ch[ch] = addr
            ok.append(ch)
            print("CH{}: ADXL345 @{}".format(ch, hex(addr)))
        else:
            print("CH{}: no ADXL345".format(ch))
    if not ok:
        raise OSError("No ADXL devices detected on any channel.")

    for ch in ok:
        init_adxl345(ch, adxl_addr_by_ch[ch])

    # Setup TDOA hardware interrupts
    setup_interrupts()
    clear_interrupts()  # Clear any initial triggers

    # Diagnostic: verify INT pin states
    if TDOA_ENABLED:
        print("INT pin states:", {ch: int_pins[ch].value() for ch in int_pins})

    print("Starting loop.")
    main_loop()

main()
