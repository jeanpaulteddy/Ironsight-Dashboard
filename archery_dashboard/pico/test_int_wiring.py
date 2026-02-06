# test_int_wiring.py
# TDOA interrupt wiring verification for archery target
#
# Expected wiring:
#   ADXL345 #0 (ch0, North) INT1 -> Pico GP2
#   ADXL345 #1 (ch1, West)  INT1 -> Pico GP3
#   ADXL345 #2 (ch2, South) INT1 -> Pico GP4
#   ADXL345 #3 (ch3, East)  INT1 -> Pico GP5
#
# Test procedure:
# 1. Upload and run this script
# 2. Tap each sensor individually
# 3. The sensor you tap should show as "FIRST" in that event
# 4. If wrong sensor is first, INT1 wires are swapped

import machine
import time

# ---------- CONFIG ----------
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

# Activity threshold (62.5mg per LSB)
# 8 = 500mg, 16 = 1g, 32 = 2g, 64 = 4g
ACTIVITY_THRESHOLD = 48  # ~3g - ignores ambient vibration

# Event detection: interrupts within this window are grouped as one event
EVENT_WINDOW_MS = 20
# Minimum time between events
EVENT_COOLDOWN_MS = 200

# GPIO pins for INT1 from each sensor
# Note: West/South INT1 wires are physically swapped (GP3↔GP4)
INT_PINS = {
    0: 2,  # Channel 0 (North) -> GP2
    1: 4,  # Channel 1 (West)  -> GP4 (swapped)
    2: 3,  # Channel 2 (South) -> GP3 (swapped)
    3: 5,  # Channel 3 (East)  -> GP5
}

CH_NAMES = {0: "North", 1: "West", 2: "South", 3: "East"}

# ---------- STATE ----------
i2c = None
adxl_addr_by_ch = {}
gpio_pins = {}

# Interrupt timestamps (microseconds) - captured by IRQ handlers
int_timestamps = {}  # {ch: timestamp_us} for current event

# Event statistics
event_count = 0
first_wins = {ch: 0 for ch in CHANNELS}  # How many times each sensor fired first

# Event state
in_event = False
event_start_ms = 0

# ---------- I2C HELPERS ----------
def select_mux_channel(ch):
    i2c.writeto(TCA_ADDR, bytes([1 << ch]))
    time.sleep_ms(1)

def detect_adxl_addr(ch):
    select_mux_channel(ch)
    for addr in ADXL_ADDRS:
        try:
            data = i2c.readfrom_mem(addr, REG_DEVID, 1)
            if data and data[0] == 0xE5:
                return addr
        except:
            pass
    return None

def adxl_write(ch, addr, reg, val):
    select_mux_channel(ch)
    i2c.writeto_mem(addr, reg, bytes([val]))

def adxl_read(ch, addr, reg, length=1):
    select_mux_channel(ch)
    return i2c.readfrom_mem(addr, reg, length)

def init_adxl345(ch, addr):
    """Initialize ADXL345 with activity interrupt."""
    adxl_write(ch, addr, REG_POWER_CTL, 0x00)  # Standby
    time.sleep_ms(5)
    adxl_write(ch, addr, REG_DATA_FORMAT, 0x08)  # Full-res ±2g
    adxl_write(ch, addr, REG_BW_RATE, 0x0F)  # 3200 Hz ODR
    adxl_write(ch, addr, REG_THRESH_ACT, ACTIVITY_THRESHOLD)
    adxl_write(ch, addr, REG_ACT_INACT_CTL, 0x70)  # AC-coupled, XYZ
    adxl_write(ch, addr, REG_INT_MAP, 0x00)  # Activity -> INT1
    adxl_read(ch, addr, REG_INT_SOURCE, 1)  # Clear pending
    adxl_write(ch, addr, REG_INT_ENABLE, 0x10)  # Enable activity int
    adxl_write(ch, addr, REG_POWER_CTL, 0x08)  # Measure mode
    time.sleep_ms(5)

def clear_all_interrupts():
    """Clear interrupt flags on all sensors."""
    for ch, addr in adxl_addr_by_ch.items():
        try:
            adxl_read(ch, addr, REG_INT_SOURCE, 1)
        except:
            pass

# ---------- INTERRUPT HANDLERS ----------
def make_int_handler(ch):
    """Create ISR for a channel - captures timestamp on FIRST trigger only."""
    def handler(pin):
        global int_timestamps
        if ch not in int_timestamps:
            int_timestamps[ch] = time.ticks_us()
    return handler

def setup_gpio():
    """Configure GPIO pins with interrupt handlers."""
    global gpio_pins
    for ch, gpio in INT_PINS.items():
        pin = machine.Pin(gpio, machine.Pin.IN, machine.Pin.PULL_DOWN)
        pin.irq(trigger=machine.Pin.IRQ_RISING, handler=make_int_handler(ch))
        gpio_pins[ch] = pin
        print(f"  GP{gpio} -> Ch{ch} ({CH_NAMES[ch]})")

# ---------- EVENT PROCESSING ----------
def process_event():
    """Analyze timestamps from current event and print results."""
    global event_count, first_wins, int_timestamps

    if not int_timestamps:
        return

    event_count += 1
    fired = sorted(int_timestamps.keys())

    # Find first arrival
    first_ch = min(int_timestamps, key=int_timestamps.get)
    t0 = int_timestamps[first_ch]
    first_wins[first_ch] += 1

    # Compute relative timing
    rel_times = {ch: int_timestamps[ch] - t0 for ch in fired}

    # Format output
    fired_names = [CH_NAMES[ch] for ch in fired]
    timing_str = " ".join([f"{CH_NAMES[ch]}:{rel_times[ch]}us" for ch in fired])

    print(f"\n[Event {event_count}] FIRST: {CH_NAMES[first_ch]}")
    print(f"  Fired: {fired_names}")
    print(f"  Timing: {timing_str}")

    # Show spread
    if len(fired) > 1:
        spread = max(rel_times.values())
        print(f"  Spread: {spread}us ({spread/1000:.1f}ms)")

def print_summary():
    """Print test summary."""
    print("\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)
    print(f"Total events: {event_count}")
    print("\nFirst-place wins (which sensor detected impact first):")
    for ch in CHANNELS:
        pct = (first_wins[ch] / event_count * 100) if event_count > 0 else 0
        bar = "#" * int(pct / 5)
        print(f"  {CH_NAMES[ch]:6}: {first_wins[ch]:3} ({pct:5.1f}%) {bar}")

    if event_count > 0:
        winner = max(first_wins, key=first_wins.get)
        if first_wins[winner] == event_count:
            print(f"\n⚠ WARNING: {CH_NAMES[winner]} won ALL events!")
            print("  This suggests INT1 wires may be swapped, or")
            print("  the {CH_NAMES[winner]} sensor has different sensitivity.")
        elif first_wins[winner] > event_count * 0.8:
            print(f"\n⚠ NOTE: {CH_NAMES[winner]} won {first_wins[winner]}/{event_count} events")
            print("  Consider checking wiring if this doesn't match tap locations.")

# ---------- MAIN ----------
def main():
    global i2c, int_timestamps, in_event, event_start_ms

    print("=" * 50)
    print("TDOA Interrupt Wiring Test")
    print("=" * 50)

    # Initialize I2C
    print("\n[1/4] Initializing I2C...")
    i2c = machine.I2C(I2C_BUS_ID,
                      sda=machine.Pin(I2C_SDA_PIN),
                      scl=machine.Pin(I2C_SCL_PIN),
                      freq=I2C_FREQ)

    scan = i2c.scan()
    print(f"  Found: {[hex(a) for a in scan]}")
    if TCA_ADDR not in scan:
        print(f"  ERROR: Mux not found at {hex(TCA_ADDR)}")
        return

    # Detect sensors
    print("\n[2/4] Detecting sensors...")
    for ch in CHANNELS:
        addr = detect_adxl_addr(ch)
        if addr:
            adxl_addr_by_ch[ch] = addr
            print(f"  Ch{ch} ({CH_NAMES[ch]}): {hex(addr)}")
        else:
            print(f"  Ch{ch} ({CH_NAMES[ch]}): NOT FOUND")

    if not adxl_addr_by_ch:
        print("ERROR: No sensors found!")
        return

    # Configure sensors
    print("\n[3/4] Configuring interrupts...")
    for ch, addr in adxl_addr_by_ch.items():
        init_adxl345(ch, addr)
        print(f"  Ch{ch}: threshold={ACTIVITY_THRESHOLD} (~{ACTIVITY_THRESHOLD*62.5:.0f}mg)")

    # Setup GPIO
    print("\n[4/4] Setting up GPIO...")
    setup_gpio()
    clear_all_interrupts()

    # Check initial state
    print("\nGPIO states:")
    for ch, pin in gpio_pins.items():
        state = "HIGH!" if pin.value() else "low"
        print(f"  Ch{ch} ({CH_NAMES[ch]}): {state}")

    print("\n" + "=" * 50)
    print("READY - Tap sensors to test")
    print("=" * 50)
    print("\nExpected behavior:")
    print("- Tap North sensor -> North should be FIRST")
    print("- Tap South sensor -> South should be FIRST")
    print("- etc.")
    print("\nPress Ctrl+C to see summary and exit")
    print()

    last_event_end = 0

    try:
        while True:
            now = time.ticks_ms()

            # Check for new interrupt timestamps
            if int_timestamps and not in_event:
                # Start of new event
                if time.ticks_diff(now, last_event_end) > EVENT_COOLDOWN_MS:
                    in_event = True
                    event_start_ms = now
                else:
                    # Too soon after last event, clear and ignore
                    int_timestamps = {}
                    clear_all_interrupts()

            if in_event:
                # Wait for event window to collect all sensor triggers
                if time.ticks_diff(now, event_start_ms) >= EVENT_WINDOW_MS:
                    # Event window closed, process it
                    process_event()
                    int_timestamps = {}
                    in_event = False
                    last_event_end = now
                    clear_all_interrupts()

            # Periodically clear sensor interrupts (so they can fire again)
            clear_all_interrupts()
            time.sleep_ms(5)

    except KeyboardInterrupt:
        print_summary()

if __name__ == "__main__":
    main()
