# SPI test for 4x ADXL345 on Pico W (shared bus, individual CS)
# Shared: SCK=GP18, MOSI(SDA)=GP19, MISO(SDO)=GP16
import machine, time, math, struct  # type: ignore

# --- SPI CONFIG ---
SPI_ID   = 0
PIN_SCK  = 18
PIN_MOSI = 19  # SDA on ADXL breakout
PIN_MISO = 16  # SDO on ADXL breakout
SPI_BAUD = 5_000_000  # 5 MHz (ADXL345 max)

# CS pin per sensor — UPDATE THESE to match your wiring
CS_PINS = {
    0: 17,   # Sensor 0 CS -> GP17
    1: 20,   # Sensor 1 CS -> GP??  <-- UPDATE
    2: 21,   # Sensor 2 CS -> GP??  <-- UPDATE
    3: 22,   # Sensor 3 CS -> GP??  <-- UPDATE
}

# --- ADXL345 REGISTERS ---
REG_DEVID       = 0x00
REG_BW_RATE     = 0x2C
REG_POWER_CTL   = 0x2D
REG_DATA_FORMAT = 0x31
REG_DATAX0      = 0x32
REG_FIFO_CTL    = 0x38
REG_FIFO_STATUS = 0x39

# --- INIT SPI BUS (shared) ---
spi = machine.SPI(SPI_ID,
                  baudrate=SPI_BAUD,
                  polarity=1, phase=1,
                  sck=machine.Pin(PIN_SCK),
                  mosi=machine.Pin(PIN_MOSI),
                  miso=machine.Pin(PIN_MISO))

# Create CS pin objects — all start HIGH (deselected)
cs_pins = {}
for ch, gpio in CS_PINS.items():
    cs_pins[ch] = machine.Pin(gpio, machine.Pin.OUT, value=1)

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

def mag3(x, y, z):
    return math.sqrt(x*x + y*y + z*z)

# --- DETECT & CONFIGURE EACH SENSOR ---
active_channels = []

for ch in sorted(CS_PINS.keys()):
    cs = cs_pins[ch]
    devid = spi_read(cs, REG_DEVID, 1)[0]
    if devid == 0xE5:
        print("CH{}: ADXL345 found (CS=GP{})".format(ch, CS_PINS[ch]))
        # Configure (matching your I2C settings)
        spi_write(cs, REG_POWER_CTL, 0x00)      # standby
        time.sleep_ms(2)
        spi_write(cs, REG_DATA_FORMAT, 0x09)     # full-res ±4g
        spi_write(cs, REG_BW_RATE, 0x0F)         # 3200 Hz ODR
        spi_write(cs, REG_FIFO_CTL, 0x00)        # bypass mode
        spi_write(cs, REG_POWER_CTL, 0x08)       # measure mode
        active_channels.append(ch)
    else:
        print("CH{}: NOT found (CS=GP{}, got 0x{:02X})".format(ch, CS_PINS[ch], devid))

time.sleep_ms(10)

if not active_channels:
    print("\nNo sensors detected! Check wiring and R4 removal on each board.")
    raise SystemExit

print("\n{} sensor(s) active: {}".format(len(active_channels), active_channels))
print("Config: full-res +/-4g, 3200 Hz ODR, SPI @ {} MHz".format(SPI_BAUD // 1_000_000))
print("\nReading... (Ctrl+C to stop)\n")

# --- READ LOOP ---
sample_count = 0
cycle_count = 0
t_start = time.ticks_us()
latest = {}  # store latest reading per channel

try:
    while True:
        for ch in active_channels:
            cs = cs_pins[ch]
            raw = spi_read(cs, REG_DATAX0, 6)
            x, y, z = struct.unpack('<hhh', raw)
            m = mag3(x, y, z)
            sample_count += 1
            latest[ch] = (x, y, z, m)

        cycle_count += 1
        if cycle_count % 100 == 0:
            elapsed_ms = time.ticks_diff(time.ticks_us(), t_start) / 1000
            rate = sample_count / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
            per_sensor = rate / len(active_channels)
            for ch in active_channels:
                x, y, z, m = latest[ch]
                print("CH{}  x={:>6d} y={:>6d} z={:>6d} mag={:>7.1f}  |  total={:.0f} Hz ({:.0f}/sensor)".format(
                    ch, x, y, z, m, rate, per_sensor))
            print()

        time.sleep_us(100)

except KeyboardInterrupt:
    elapsed_ms = time.ticks_diff(time.ticks_us(), t_start) / 1000
    rate = sample_count / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
    per_sensor = rate / len(active_channels) if active_channels else 0
    print("\nStopped. {} samples in {:.1f}s".format(sample_count, elapsed_ms / 1000))
    print("Total: {:.0f} Hz | Per sensor: {:.0f} Hz | Sensors: {}".format(
        rate, per_sensor, len(active_channels)))
