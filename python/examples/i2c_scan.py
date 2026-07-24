#!/usr/bin/env python3
"""Probe I2C addresses 0x03-0x77 and print which ones ACK.

Usage: python i2c_scan.py /dev/ttyUSB0
"""

import sys

from uart_iic_spi_bridge import DeviceError, UartIicSpiBridge


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    with UartIicSpiBridge(port, baudrate=115200) as bridge:
        found = []
        for addr in range(0x03, 0x78):
            try:
                bridge.i2c_write(addr, b"")  # address-only probe, no data
            except DeviceError:
                continue
            found.append(addr)

        if not found:
            print("no devices found")
        else:
            print("found:", ", ".join(f"0x{a:02x}" for a in found))


if __name__ == "__main__":
    main()
