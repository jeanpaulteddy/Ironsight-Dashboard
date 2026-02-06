# test_int_wiring.py
# Hardware verification script for TDOA INT1 wiring
# Run this on the Pico to verify interrupt wiring is correct.
#
# Expected wiring:
#   ADXL345 #0 (ch0, North) INT1 -> Pico GP2
#   ADXL345 #1 (ch1, West)  INT1 -> Pico GP3
#   ADXL345 #2 (ch2, South) INT1 -> Pico GP4
#   ADXL345 #3 (ch3, East)  INT1 -> Pico GP5
#
# Test procedure:
# 1. Upload this script to the Pico
# 2. Run it (the Pico will print status)
# 3. Tap each sensor one at a time
# 4. Verify the correct channel triggers

import machine
import time
import struct

# ---------- I2C CONFIG ----------
I2C_BUS_ID = 0
I2C_SDA_PIN = 0
I2C_SCL_PIN = 1
I2C_FREQ = 50000

TCA_ADDR = 0x70
ADXL_ADDRS = [0x53, 0x1D]
CHANNELS = [0, 1, 2, 3]

# ADXL345 registers
REG_DEVID = 0x00
REG_BW_RATE = 0x2C
REG_POWER_CTL = 0x2D
REG_DATA_FORMAT = 0x31
REG_THRESH_ACT = 0x24
REG_ACT_INACT_CTL = 0x27
REG_INT_ENABLE = 0x2E
REG_INT_MAP = 0x2F
REG_INT_SOURCE = 0x30

# Activity threshold (62.5mg per LSB, so 8 = 500mg)
ACTIVITY_THRESHOLD = 8

# GPIO pins for INT1 from each sensor
INT_PINS = {
    0: 2,  # Channel 0 (North) -> GP2
    1: 3,  # Channel 1 (West)  -> GP3
    2: 4,  # Channel 2 (South) -> GP4
    3: 5,  # Channel 3 (East)  -> GP5
}

# Compass mapping for display
CH_NAMES = {
    0: "North",
    1: "West",
    2: "South",
    3: "East",
}

# Runtime state
i2c = None
adxl_addr_by_ch = {}
int_counts = {ch: 0 for ch in INT_PINS}
int_last_time = {ch: 0 for ch in INT_PINS}
int_timestamps_us = {ch: 0 for ch in INT_PINS}

# ---------- I2C HELPERS ----------
def select_mux_channel(ch):
    """Select a channel on the TCA9548A mux."""
    i2c.writeto(TCA_ADDR, bytes([1 << ch]))
    time.sleep_ms(1)

def detect_adxl_addr(ch):
    """Detect ADXL345 address on a mux channel."""
    select_mux_channel(ch)
    for addr in ADXL_ADDRS:
        try:
            data = i2c.readfrom_mem(addr, REG_DEVID, 1)
            if data and data[0] == 0xE5:
                return addr
        except Exception:
            pass
    return None

def adxl_write(ch, addr, reg, val):
    """Write a byte to an ADXL345 register."""
    select_mux_channel(ch)
    i2c.writeto_mem(addr, reg, bytes([val]))

def adxl_read(ch, addr, reg, length=1):
    """Read bytes from an ADXL345 register."""
    select_mux_channel(ch)
    return i2c.readfrom_mem(addr, reg, length)

def init_adxl345_with_interrupt(ch, addr):
    """Initialize ADXL345 with activity interrupt enabled."""
    # Standby mode
    adxl_write(ch, addr, REG_POWER_CTL, 0x00)
    time.sleep_ms(5)

    # Data format: full resolution, ±2g
    adxl_write(ch, addr, REG_DATA_FORMAT, 0x08)

    # Output data rate: 3200 Hz (0x0F) for fast response
    adxl_write(ch, addr, REG_BW_RATE, 0x0F)

    # Activity threshold
    adxl_write(ch, addr, REG_THRESH_ACT, ACTIVITY_THRESHOLD)

    # Activity control: AC-coupled, enable X, Y, Z axes
    adxl_write(ch, addr, REG_ACT_INACT_CTL, 0x70)

    # Map activity interrupt to INT1 pin (0 = INT1)
    adxl_write(ch, addr, REG_INT_MAP, 0x00)

    # Clear any pending interrupts
    adxl_read(ch, addr, REG_INT_SOURCE, 1)

    # Enable activity interrupt
    adxl_write(ch, addr, REG_INT_ENABLE, 0x10)

    # Start measuring
    adxl_write(ch, addr, REG_POWER_CTL, 0x08)
    time.sleep_ms(5)

def clear_adxl_interrupt(ch, addr):
    """Clear pending interrupt by reading INT_SOURCE."""
    try:
        adxl_read(ch, addr, REG_INT_SOURCE, 1)
    except Exception:
        pass

# ---------- GPIO INTERRUPT HANDLERS ----------
def make_handler(ch):
    """Create interrupt handler for a specific channel."""
    def handler(pin):
        global int_timestamps_us
        now_ms = time.ticks_ms()
        now_us = time.ticks_us()
        # Debounce: ignore triggers within 100ms
        if time.ticks_diff(now_ms, int_last_time[ch]) > 100:
            int_counts[ch] += 1
            int_last_time[ch] = now_ms
            int_timestamps_us[ch] = now_us
            print(f">>> [INT] Ch{ch} ({CH_NAMES[ch]}) triggered! Count: {int_counts[ch]}, t={now_us}us")
    return handler

def setup_gpio_interrupts():
    """Configure GPIO pins as inputs with interrupts."""
    pins = {}
    for ch, gpio in INT_PINS.items():
        pin = machine.Pin(gpio, machine.Pin.IN, machine.Pin.PULL_DOWN)
        pin.irq(trigger=machine.Pin.IRQ_RISING, handler=make_handler(ch))
        pins[ch] = pin
        print(f"  GPIO: GP{gpio} configured for Ch{ch} ({CH_NAMES[ch]})")
    return pins

def check_gpio_state(pins):
    """Check current state of all GPIO pins."""
    print("\n--- GPIO Pin States ---")
    for ch, pin in pins.items():
        val = pin.value()
        status = "HIGH (interrupt pending!)" if val else "LOW (idle, OK)"
        print(f"  Ch{ch} ({CH_NAMES[ch]}, GP{INT_PINS[ch]}): {status}")

# ---------- MAIN ----------
def main():
    global i2c, adxl_addr_by_ch

    print("=" * 50)
    print("TDOA INT1 Wiring + Sensor Test")
    print("=" * 50)
    print()

    # Step 1: Initialize I2C
    print("[1/4] Initializing I2C...")
    i2c = machine.I2C(I2C_BUS_ID,
                      sda=machine.Pin(I2C_SDA_PIN),
                      scl=machine.Pin(I2C_SCL_PIN),
                      freq=I2C_FREQ)

    # Check for mux
    scan = i2c.scan()
    print(f"  I2C scan: {[hex(a) for a in scan]}")
    if TCA_ADDR not in scan:
        print(f"  ERROR: TCA9548A mux not found at {hex(TCA_ADDR)}!")
        print("  Check I2C wiring (SDA=GP0, SCL=GP1)")
        return
    print(f"  TCA9548A mux found at {hex(TCA_ADDR)}")

    # Step 2: Detect and configure ADXL345 sensors
    print("\n[2/4] Detecting ADXL345 sensors...")
    for ch in CHANNELS:
        addr = detect_adxl_addr(ch)
        if addr:
            adxl_addr_by_ch[ch] = addr
            print(f"  Ch{ch} ({CH_NAMES[ch]}): ADXL345 found at {hex(addr)}")
        else:
            print(f"  Ch{ch} ({CH_NAMES[ch]}): NOT FOUND!")

    if not adxl_addr_by_ch:
        print("\nERROR: No ADXL345 sensors detected!")
        return

    # Step 3: Configure sensors with interrupts
    print("\n[3/4] Configuring ADXL345 activity interrupts...")
    for ch, addr in adxl_addr_by_ch.items():
        init_adxl345_with_interrupt(ch, addr)
        print(f"  Ch{ch} ({CH_NAMES[ch]}): Interrupt enabled (threshold={ACTIVITY_THRESHOLD})")

    # Step 4: Setup GPIO interrupts
    print("\n[4/4] Setting up GPIO interrupts...")
    pins = setup_gpio_interrupts()

    # Clear any pending interrupts
    print("\nClearing pending interrupts...")
    for ch, addr in adxl_addr_by_ch.items():
        clear_adxl_interrupt(ch, addr)

    check_gpio_state(pins)

    print("\n" + "=" * 50)
    print("READY FOR TESTING")
    print("=" * 50)
    print("\nExpected wiring:")
    for ch, gpio in INT_PINS.items():
        sensor_status = "OK" if ch in adxl_addr_by_ch else "NO SENSOR"
        print(f"  ADXL345 #{ch} ({CH_NAMES[ch]}) INT1 -> GP{gpio}  [{sensor_status}]")

    print("\nInstructions:")
    print("1. Tap each sensor ONE AT A TIME")
    print("2. Watch which channel triggers")
    print("3. If wrong channel triggers, wiring is swapped")
    print("4. Press Ctrl+C to exit")
    print()
    print("Waiting for taps...")
    print()

    last_status = time.ticks_ms()
    try:
        while True:
            # Periodically clear ADXL interrupts so they can fire again
            for ch, addr in adxl_addr_by_ch.items():
                clear_adxl_interrupt(ch, addr)

            time.sleep_ms(50)

            # Print status every 3 seconds
            now = time.ticks_ms()
            if time.ticks_diff(now, last_status) > 3000:
                total = sum(int_counts.values())
                if total > 0:
                    print(f"[STATUS] N={int_counts[0]} W={int_counts[1]} S={int_counts[2]} E={int_counts[3]}")
                    # Show timing differences if multiple triggered
                    active = [ch for ch in CHANNELS if int_counts[ch] > 0]
                    if len(active) > 1:
                        t_min = min(int_timestamps_us[ch] for ch in active if int_timestamps_us[ch] > 0)
                        print(f"         Last timing (relative): ", end="")
                        for ch in active:
                            dt = int_timestamps_us[ch] - t_min if int_timestamps_us[ch] > 0 else 0
                            print(f"{CH_NAMES[ch]}={dt}us ", end="")
                        print()
                else:
                    print("[STATUS] No triggers yet - try tapping a sensor harder")
                last_status = now

    except KeyboardInterrupt:
        print("\n\nTest complete!")
        print("\nFinal counts:")
        for ch, count in int_counts.items():
            sensor = f"(sensor {'OK' if ch in adxl_addr_by_ch else 'MISSING'})"
            print(f"  Ch{ch} ({CH_NAMES[ch]}): {count} triggers {sensor}")

if __name__ == "__main__":
    main()
