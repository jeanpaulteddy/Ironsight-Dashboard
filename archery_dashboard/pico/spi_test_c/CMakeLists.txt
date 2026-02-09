cmake_minimum_required(VERSION 3.13)

include(pico_sdk_import.cmake)

project(spi_test C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

add_executable(spi_test spi_test.c)

target_link_libraries(spi_test
    pico_stdlib
    hardware_spi
    hardware_gpio
)

# USB serial output (matches MicroPython REPL-over-USB behavior)
pico_enable_stdio_usb(spi_test 1)
pico_enable_stdio_uart(spi_test 0)

pico_add_extra_outputs(spi_test)
