# SPI test for single ADXL345 on Pico W
# Wiring: CS=GP17, SCK=GP18, MOSI(SDA)=GP19, MISO(SDO)=GP16
import machine, time, math, struct # type: ignore

# --- SPI CONFIG ---
SPI_ID   = 0
PIN_SCK  = 18
PIN_MOSI = 19  # SDA on ADXL breakout
PIN_MISO = 16  # SDO on ADXL breakout
PIN_CS   = 17
SPI_BAUD = 5_000_000  # 5 MHz (ADXL345 max is 5 MHz)

# --- ADXL345 REGISTERS ---
REG_DEVID       = 0x00
REG_BW_RATE     = 0x2C
REG_POWER_CTL   = 0x2D
REG_DATA_FORMAT = 0x31
REG_DATAX0      = 0x32
REG_FIFO_CTL    = 0x38
REG_FIFO_STATUS = 0x39

# --- INIT ---
spi = machine.SPI(SPI_ID,
                  baudrate=SPI_BAUD,
                  polarity=1,      # CPOL=1, CPHA=1 (SPI mode 3 for ADXL345)
                  phase=1,
                  sck=machine.Pin(PIN_SCK),
                  mosi=machine.Pin(PIN_MOSI),
                  miso=machine.Pin(PIN_MISO))

cs = machine.Pin(PIN_CS, machine.Pin.OUT, value=1)

def spi_read(reg, n=1):
    """Read n bytes starting at reg. Bit 7=read, bit 6=multi-byte."""
    cmd = (reg | 0x80 | (0x40 if n > 1 else 0x00))
    cs.value(0)
    spi.write(bytes([cmd]))
    data = spi.read(n)
    cs.value(1)
    return data

def spi_write(reg, val):
    """Write single byte to reg."""
    cs.value(0)
    spi.write(bytes([reg, val]))
    cs.value(1)

def mag3(x, y, z):
    return math.sqrt(x*x + y*y + z*z)

# --- CHECK DEVICE ID ---
devid = spi_read(REG_DEVID, 1)[0]
if devid == 0xE5:
    print("ADXL345 found! DEVID=0x{:02X}".format(devid))
else:
    print("ERROR: expected DEVID 0xE5, got 0x{:02X}".format(devid))
    print("Check wiring. SPI0: SCK=GP{}, MOSI=GP{}, MISO=GP{}, CS=GP{}".format(
        PIN_SCK, PIN_MOSI, PIN_MISO, PIN_CS))
    raise SystemExit

# --- CONFIGURE ADXL345 (matching your I2C settings) ---
spi_write(REG_POWER_CTL, 0x00)      # standby
time.sleep_ms(2)
spi_write(REG_DATA_FORMAT, 0x09)    # full-res ±4g (matches your I2C config)
spi_write(REG_BW_RATE, 0x0F)        # 3200 Hz ODR (matches your I2C config)
spi_write(REG_FIFO_CTL, 0x00)       # bypass mode
spi_write(REG_POWER_CTL, 0x08)      # measure mode
time.sleep_ms(10)

print("Config: full-res +/-4g, 3200 Hz ODR, FIFO bypass")
print("SPI baud: {} Hz".format(SPI_BAUD))
print("")

# --- READ LOOP ---
print("Reading accelerometer... (Ctrl+C to stop)")
print("{:>8s} {:>8s} {:>8s} {:>8s}".format("X", "Y", "Z", "MAG"))
print("-" * 38)

sample_count = 0
t_start = time.ticks_us()

try:
    while True:
        raw = spi_read(REG_DATAX0, 6)
        x, y, z = struct.unpack('<hhh', raw)
        m = mag3(x, y, z)
        sample_count += 1

        # Print every 100th sample to avoid flooding REPL
        if sample_count % 100 == 0:
            elapsed_ms = time.ticks_diff(time.ticks_us(), t_start) / 1000
            rate = sample_count / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
            print("x={:>6d}  y={:>6d}  z={:>6d}  mag={:>7.1f}  rate={:.0f} Hz".format(
                x, y, z, m, rate))

        time.sleep_us(200)

except KeyboardInterrupt:
    elapsed_ms = time.ticks_diff(time.ticks_us(), t_start) / 1000
    rate = sample_count / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
    print("\nStopped. {} samples in {:.1f}s = {:.0f} Hz effective read rate".format(
        sample_count, elapsed_ms / 1000, rate))
