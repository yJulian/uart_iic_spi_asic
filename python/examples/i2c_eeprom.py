#!/usr/bin/env python3
"""Read two bytes from an I2C EEPROM at 0x50, register pointer 0x00.

This mirrors the worked example in docs/PROTOCOL.md.
Usage: python i2c_eeprom.py /dev/ttyUSB0
"""

import sys

from uart_iic_spi_bridge import NackError, UartIicSpiBridge


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    with UartIicSpiBridge(port, baudrate=115200) as bridge:
        try:
            data = bridge.i2c_write_read(0x50, wdata=bytes([0x00]), nread=2)
        except NackError:
            print("no ACK from EEPROM at 0x50 - check wiring/address")
            return
        print("bytes:", data.hex())


if __name__ == "__main__":
    main()
