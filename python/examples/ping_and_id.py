#!/usr/bin/env python3
"""Sanity-check that the link is alive and print the device's ID bytes.

Usage: python ping_and_id.py /dev/ttyUSB0
"""

import sys

from uart_iic_spi_bridge import UartIicSpiBridge


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    with UartIicSpiBridge(port, baudrate=115200) as bridge:
        bridge.ping()
        version, device_id = bridge.get_id()
        print(f"link ok - version=0x{version:02x} device_id=0x{device_id:02x}")


if __name__ == "__main__":
    main()
