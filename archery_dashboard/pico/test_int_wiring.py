# test_int_wiring.py
# Hardware verification script for TDOA INT1 wiring
# Run this on the Pico BEFORE flashing the main firmware to verify wiring is correct.
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

# Interrupt counters
int_counts = {ch: 0 for ch in INT_PINS}
int_last_time = {ch: 0 for ch in INT_PINS}

def make_handler(ch):
    """Create interrupt handler for a specific channel."""
    def handler(pin):
        now = time.ticks_ms()
        # Debounce: ignore triggers within 100ms
        if time.ticks_diff(now, int_last_time[ch]) > 100:
            int_counts[ch] += 1
            int_last_time[ch] = now
            print(f"[INT] Channel {ch} ({CH_NAMES[ch]}) triggered! Count: {int_counts[ch]}")
    return handler

def setup_pins():
    """Configure GPIO pins as inputs with interrupts."""
    pins = {}
    for ch, gpio in INT_PINS.items():
        pin = machine.Pin(gpio, machine.Pin.IN, machine.Pin.PULL_DOWN)
        pin.irq(trigger=machine.Pin.IRQ_RISING, handler=make_handler(ch))
        pins[ch] = pin
        print(f"Configured GP{gpio} for channel {ch} ({CH_NAMES[ch]})")
    return pins

def check_initial_state(pins):
    """Check initial state of all pins."""
    print("\n--- Initial Pin States ---")
    for ch, pin in pins.items():
        val = pin.value()
        status = "HIGH (sensor active?)" if val else "LOW (idle, OK)"
        print(f"  Channel {ch} ({CH_NAMES[ch]}, GP{INT_PINS[ch]}): {status}")
    print()

def main():
    print("=" * 50)
    print("TDOA INT1 Wiring Verification Test")
    print("=" * 50)
    print()
    print("Expected wiring:")
    for ch, gpio in INT_PINS.items():
        print(f"  ADXL345 #{ch} ({CH_NAMES[ch]}) INT1 -> GP{gpio}")
    print()

    pins = setup_pins()
    check_initial_state(pins)

    print("Test Instructions:")
    print("1. Tap each sensor ONE AT A TIME")
    print("2. Verify the correct channel triggers")
    print("3. If wrong channel triggers, check your wiring")
    print("4. Press Ctrl+C to exit")
    print()
    print("Waiting for interrupts...")
    print()

    try:
        while True:
            time.sleep(1)
            # Print periodic status
            total = sum(int_counts.values())
            if total > 0:
                print(f"[STATUS] Counts: N={int_counts[0]} W={int_counts[1]} S={int_counts[2]} E={int_counts[3]}")
    except KeyboardInterrupt:
        print("\n\nTest complete!")
        print("Final counts:")
        for ch, count in int_counts.items():
            print(f"  Channel {ch} ({CH_NAMES[ch]}): {count} triggers")

if __name__ == "__main__":
    main()
