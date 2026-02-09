// SPI test for 4x ADXL345 on Pico W (shared bus, individual CS)
// C port of spi_test.py — polled SPI, 3200 Hz ODR
#include <stdio.h>
#include <math.h>
#include "pico/stdlib.h"
#include "hardware/spi.h"
#include "hardware/gpio.h"

// --- SPI config (matches Python exactly) ---
#define SPI_PORT    spi0
#define PIN_SCK     18
#define PIN_MOSI    19  // SDA on ADXL breakout
#define PIN_MISO    16  // SDO on ADXL breakout
#define SPI_BAUD    5000000  // 5 MHz

// CS pin per sensor — must match wiring
#define NUM_SENSORS 4
static const uint CS_PINS[NUM_SENSORS] = {17, 20, 21, 22};

// --- ADXL345 registers ---
#define REG_DEVID       0x00
#define REG_BW_RATE     0x2C
#define REG_POWER_CTL   0x2D
#define REG_DATA_FORMAT 0x31
#define REG_DATAX0      0x32
#define REG_FIFO_CTL    0x38

// --- SPI helpers ---

static void spi_read_regs(uint cs_pin, uint8_t reg, uint8_t *buf, uint8_t len) {
    uint8_t cmd = reg | 0x80 | (len > 1 ? 0x40 : 0x00);
    gpio_put(cs_pin, 0);
    spi_write_blocking(SPI_PORT, &cmd, 1);
    spi_read_blocking(SPI_PORT, 0, buf, len);
    gpio_put(cs_pin, 1);
}

static void spi_write_reg(uint cs_pin, uint8_t reg, uint8_t val) {
    uint8_t buf[2] = {reg, val};
    gpio_put(cs_pin, 0);
    spi_write_blocking(SPI_PORT, buf, 2);
    gpio_put(cs_pin, 1);
}

static float mag3(int16_t x, int16_t y, int16_t z) {
    return sqrtf((float)x * x + (float)y * y + (float)z * z);
}

int main(void) {
    stdio_init_all();

    // Short delay so USB serial has time to enumerate
    sleep_ms(2000);

    // --- Init SPI bus ---
    spi_init(SPI_PORT, SPI_BAUD);
    spi_set_format(SPI_PORT, 8, SPI_CPOL_1, SPI_CPHA_1, SPI_MSB_FIRST);
    gpio_set_function(PIN_SCK,  GPIO_FUNC_SPI);
    gpio_set_function(PIN_MOSI, GPIO_FUNC_SPI);
    gpio_set_function(PIN_MISO, GPIO_FUNC_SPI);

    // --- Init CS pins (all high = deselected) ---
    for (int i = 0; i < NUM_SENSORS; i++) {
        gpio_init(CS_PINS[i]);
        gpio_set_dir(CS_PINS[i], GPIO_OUT);
        gpio_put(CS_PINS[i], 1);
    }

    // --- Detect sensors ---
    bool active[NUM_SENSORS] = {false};
    int num_active = 0;

    for (int ch = 0; ch < NUM_SENSORS; ch++) {
        uint8_t devid = 0;
        spi_read_regs(CS_PINS[ch], REG_DEVID, &devid, 1);

        if (devid == 0xE5) {
            printf("CH%d: ADXL345 found (CS=GP%u)\n", ch, CS_PINS[ch]);

            // Configure (matching Python settings)
            spi_write_reg(CS_PINS[ch], REG_POWER_CTL, 0x00);   // standby
            sleep_ms(2);
            spi_write_reg(CS_PINS[ch], REG_DATA_FORMAT, 0x09);  // full-res +/-4g
            spi_write_reg(CS_PINS[ch], REG_BW_RATE, 0x0F);      // 3200 Hz ODR
            spi_write_reg(CS_PINS[ch], REG_FIFO_CTL, 0x00);     // bypass mode
            spi_write_reg(CS_PINS[ch], REG_POWER_CTL, 0x08);    // measure mode

            active[ch] = true;
            num_active++;
        } else {
            printf("CH%d: NOT found (CS=GP%u, got 0x%02X)\n", ch, CS_PINS[ch], devid);
        }
    }

    sleep_ms(10);

    if (num_active == 0) {
        printf("\nNo sensors detected! Check wiring and R4 removal on each board.\n");
        return 1;
    }

    printf("\n%d sensor(s) active:", num_active);
    for (int ch = 0; ch < NUM_SENSORS; ch++) {
        if (active[ch]) printf(" %d", ch);
    }
    printf("\n");
    printf("Config: full-res +/-4g, 3200 Hz ODR, SPI @ %d MHz\n", SPI_BAUD / 1000000);
    printf("\nReading... (Ctrl+C to stop)\n\n");

    // --- Read loop ---
    uint32_t sample_count = 0;
    uint32_t cycle_count = 0;
    uint64_t t_start = time_us_64();

    // Latest reading per channel
    int16_t latest_x[NUM_SENSORS] = {0};
    int16_t latest_y[NUM_SENSORS] = {0};
    int16_t latest_z[NUM_SENSORS] = {0};
    float   latest_m[NUM_SENSORS] = {0};

    while (true) {
        for (int ch = 0; ch < NUM_SENSORS; ch++) {
            if (!active[ch]) continue;

            uint8_t raw[6];
            spi_read_regs(CS_PINS[ch], REG_DATAX0, raw, 6);

            // ADXL345 data is little-endian, same as ARM — direct cast
            int16_t x = (int16_t)(raw[0] | (raw[1] << 8));
            int16_t y = (int16_t)(raw[2] | (raw[3] << 8));
            int16_t z = (int16_t)(raw[4] | (raw[5] << 8));
            float m = mag3(x, y, z);

            sample_count++;
            latest_x[ch] = x;
            latest_y[ch] = y;
            latest_z[ch] = z;
            latest_m[ch] = m;
        }

        cycle_count++;
        if (cycle_count % 100 == 0) {
            uint64_t elapsed_us = time_us_64() - t_start;
            float elapsed_s = elapsed_us / 1e6f;
            float rate = (elapsed_s > 0) ? sample_count / elapsed_s : 0;
            float per_sensor = rate / num_active;

            for (int ch = 0; ch < NUM_SENSORS; ch++) {
                if (!active[ch]) continue;
                printf("CH%d  x=%6d y=%6d z=%6d mag=%7.1f  |  total=%.0f Hz (%.0f/sensor)\n",
                       ch, latest_x[ch], latest_y[ch], latest_z[ch], latest_m[ch],
                       rate, per_sensor);
            }
            printf("\n");
        }

        sleep_us(100);
    }

    return 0;
}
