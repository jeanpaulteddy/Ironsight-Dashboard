# Single-sensor peak detection test script
# Minimal script to verify peak detection on one sensor without WiFi/UDP
# ================== test_single_sensor.py ==================
import machine, time, math, struct  # type: ignore

# ---------- CONFIG ----------
SENSOR = "N"  # Options: "N", "S", "W", "E"

# Sensor mapping (CS pin for each direction)
SENSOR_MAP = {
    "N": (0, 17),  # (channel, CS GPIO)
    "W": (1, 20),
    "S": (2, 21),
    "E": (3, 22),
}

# Thresholds
K_SIGMA = 6.0
SIGMA_CAP = 20.0
ALPHA_MEAN = 0.02
ALPHA_SIGMA = 0.02
ALPHA_MEAN_WARMUP = 0.1
ALPHA_SIGMA_WARMUP = 0.1

WARMUP_MS = 4000
MIN_MAG = 350.0
DECLINE_COUNT_THRESHOLD = 16
REFRACT_MS = 500

# ---------- SPI CONFIG ----------
SPI_ID = 0
PIN_SCK = 18
PIN_MOSI = 19
PIN_MISO = 16
SPI_BAUD = 5_000_000

# ---------- ADXL345 REGISTERS ----------
REG_DEVID = 0x00
REG_BW_RATE = 0x2C
REG_POWER_CTL = 0x2D
REG_DATA_FORMAT = 0x31
REG_DATAX0 = 0x32
REG_FIFO_CTL = 0x38
REG_FIFO_STATUS = 0x39

FIFO_STREAM_MODE = 0x80

# ---------- GLOBALS ----------
spi = None
cs_pin = None
running_mean = 0.0
running_sigma = 0.5
thr_now = K_SIGMA * 0.5

# ---------- SPI FUNCTIONS ----------
def init_spi():
    global spi
    spi = machine.SPI(SPI_ID,
                      baudrate=SPI_BAUD,
                      polarity=1, phase=1,
                      sck=machine.Pin(PIN_SCK),
                      mosi=machine.Pin(PIN_MOSI),
                      miso=machine.Pin(PIN_MISO))

def spi_read(reg, n=1):
    """Read n bytes from reg."""
    cmd = (reg | 0x80 | (0x40 if n > 1 else 0x00))
    cs_pin.value(0)
    spi.write(bytes([cmd]))
    data = spi.read(n)
    cs_pin.value(1)
    return data

def spi_write(reg, val):
    """Write single byte to reg."""
    cs_pin.value(0)
    spi.write(bytes([reg, val]))
    cs_pin.value(1)

# ---------- SENSOR FUNCTIONS ----------
def detect_sensor():
    """Check DEVID=0xE5. Returns True if ADXL345 found."""
    devid = spi_read(REG_DEVID, 1)[0]
    return devid == 0xE5

def init_adxl345():
    """Configure ADXL345 via SPI."""
    spi_write(REG_POWER_CTL, 0x00)      # standby
    time.sleep_ms(2)
    spi_write(REG_DATA_FORMAT, 0x09)    # full-res +/-4g
    spi_write(REG_BW_RATE, 0x0F)        # 3200 Hz ODR
    spi_write(REG_FIFO_CTL, 0x00)       # bypass mode (flush FIFO)
    spi_write(REG_POWER_CTL, 0x08)      # measure mode
    spi_write(REG_FIFO_CTL, FIFO_STREAM_MODE)  # stream mode

def fifo_flush_latest():
    """Flush FIFO, return only the newest sample (x, y, z)."""
    count = spi_read(REG_FIFO_STATUS, 1)[0] & 0x3F
    if count == 0:
        raw = spi_read(REG_DATAX0, 6)
        return struct.unpack('<hhh', raw)
    # Read all, keep last (newest)
    for _ in range(count):
        raw = spi_read(REG_DATAX0, 6)
    return struct.unpack('<hhh', raw)

# ---------- UTILITY ----------
def mag3(x, y, z):
    return math.sqrt(x*x + y*y + z*z)

def update_baseline(m, warmup=False):
    global running_mean, running_sigma, thr_now
    a_mean = ALPHA_MEAN_WARMUP if warmup else ALPHA_MEAN
    a_sigma = ALPHA_SIGMA_WARMUP if warmup else ALPHA_SIGMA
    running_mean = (1 - a_mean) * running_mean + a_mean * m
    dev = abs(m - running_mean)
    running_sigma = (1 - a_sigma) * running_sigma + a_sigma * dev
    if running_sigma > SIGMA_CAP:
        running_sigma = SIGMA_CAP
    thr_now = running_mean + K_SIGMA * running_sigma

# ---------- MAIN ----------
def main():
    global cs_pin

    ch, cs_gpio = SENSOR_MAP[SENSOR]
    print("Initializing sensor {} (CS=GP{})...".format(SENSOR, cs_gpio))

    # Init SPI and CS pin
    init_spi()
    cs_pin = machine.Pin(cs_gpio, machine.Pin.OUT, value=1)

    # Detect sensor
    if detect_sensor():
        print("ADXL345 detected")
    else:
        devid = spi_read(REG_DEVID, 1)[0]
        print("ADXL345 NOT found (got 0x{:02X})".format(devid))
        return

    # Configure sensor
    init_adxl345()
    print("Config: 3200Hz, +/-4g")

    # Warmup phase
    print("Warmup...")
    warmup_until = time.ticks_add(time.ticks_ms(), WARMUP_MS)
    while time.ticks_diff(time.ticks_ms(), warmup_until) < 0:
        try:
            x, y, z = fifo_flush_latest()
            m = mag3(x, y, z)
            update_baseline(m, warmup=True)
        except Exception as e:
            print("Read error:", e)
        time.sleep_ms(1)

    print("ARMED")

    # State machine
    state = "IDLE"
    last_print_ms = 0
    peak_mag = 0.0
    peak_xyz = (0, 0, 0)
    decline_count = 0
    refract_until = 0

    while True:
        now = time.ticks_ms()

        if state == "IDLE":
            try:
                x, y, z = fifo_flush_latest()
            except Exception:
                continue
            m = mag3(x, y, z)

            # Check for peak trigger
            if m > thr_now and m >= MIN_MAG:
                state = "PEAK_TRACKING"
                peak_mag = m
                peak_xyz = (x, y, z)
                decline_count = 0
                snapshot_thr = thr_now
                print("TRIGGER! mag={:.1f}, thr={:.1f}".format(m, snapshot_thr))
            else:
                update_baseline(m)
                # Print idle status every second
                if time.ticks_diff(now, last_print_ms) >= 1000:
                    print("Idle... mag={:.1f}, thr={:.1f}".format(m, thr_now))
                    last_print_ms = now

            time.sleep_us(312)  # ~3200Hz

        elif state == "PEAK_TRACKING":
            try:
                x, y, z = fifo_flush_latest()
            except Exception:
                continue
            m = mag3(x, y, z)

            if m > peak_mag:
                peak_mag = m
                peak_xyz = (x, y, z)
                decline_count = 0
            else:
                decline_count += 1

            if decline_count >= DECLINE_COUNT_THRESHOLD:
                print("PEAK DETECTED! mag={:.1f}, xyz={}, thr={:.1f}".format(
                    peak_mag, peak_xyz, snapshot_thr))
                state = "REFRACTORY"
                refract_until = time.ticks_add(now, REFRACT_MS)
                print("Refractory...")

        elif state == "REFRACTORY":
            if time.ticks_diff(now, refract_until) >= 0:
                state = "IDLE"
                print("ARMED")
                last_print_ms = now
            else:
                time.sleep_ms(10)

main()
