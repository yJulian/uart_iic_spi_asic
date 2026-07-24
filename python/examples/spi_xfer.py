#!/usr/bin/env python3
"""Configure SPI mode 0 and shift a few bytes out CS0, printing MISO data.

Usage: python spi_xfer.py /dev/ttyUSB0
"""

import sys

from uart_iic_spi_bridge import UartIicSpiBridge


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    with UartIicSpiBridge(port, baudrate=115200) as bridge:
        bridge.set_spi_mode(0)
        rdata = bridge.spi_xfer(cs=0, data=bytes([0x9F, 0x00, 0x00, 0x00]))
        print("MISO:", rdata.hex())  # e.g. a JEDEC ID read on a SPI flash


if __name__ == "__main__":
    main()
