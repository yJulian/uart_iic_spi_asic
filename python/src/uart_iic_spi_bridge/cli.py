"""Small command-line tool for bringing up / poking at the bridge from a shell.

Examples::

    uart-iic-spi-bridge -p /dev/ttyUSB0 ping
    uart-iic-spi-bridge -p /dev/ttyUSB0 id
    uart-iic-spi-bridge -p /dev/ttyUSB0 self-test
    uart-iic-spi-bridge -p /dev/ttyUSB0 i2c-scan
    uart-iic-spi-bridge -p /dev/ttyUSB0 i2c-write 0x50 00 ab
    uart-iic-spi-bridge -p /dev/ttyUSB0 i2c-read 0x50 4
    uart-iic-spi-bridge -p /dev/ttyUSB0 i2c-write-read 0x50 00 --nread 2
    uart-iic-spi-bridge -p /dev/ttyUSB0 spi-xfer 0 de ad be ef
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Sequence

from .bridge import UartIicSpiBridge
from .exceptions import DeviceError, ProtocolError


def _parse_bytes(tokens: Sequence[str]) -> bytes:
    return bytes(int(tok, 0) for tok in tokens)


def _hex(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def _cmd_ping(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    bridge.ping()
    print("ok")


def _cmd_id(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    version, device_id = bridge.get_id()
    print(f"version=0x{version:02x} device_id=0x{device_id:02x}")


def _cmd_i2c_scan(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    found: List[int] = []
    for addr in range(0x03, 0x78):
        try:
            bridge.i2c_write(addr, b"")
        except DeviceError:
            continue
        found.append(addr)
    if not found:
        print("no devices found")
    else:
        print("found:", " ".join(f"0x{a:02x}" for a in found))


def _cmd_i2c_write(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    addr = int(args.addr, 0)
    data = _parse_bytes(args.data)
    bridge.i2c_write(addr, data)
    print("ok")


def _cmd_i2c_read(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    addr = int(args.addr, 0)
    data = bridge.i2c_read(addr, args.nbytes)
    print(_hex(data))


def _cmd_i2c_write_read(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    addr = int(args.addr, 0)
    wdata = _parse_bytes(args.wdata)
    data = bridge.i2c_write_read(addr, wdata, args.nread)
    print(_hex(data))


def _cmd_spi_xfer(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    data = _parse_bytes(args.data)
    rdata = bridge.spi_xfer(args.cs, data)
    print(_hex(rdata))


def _cmd_self_test(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    result = bridge.self_test()
    print(f"i2c_bus_idle={result.i2c_bus_idle} spi_loopback_ok={result.spi_loopback_ok} spi_echo=0x{result.spi_echo_byte:02x}")
    if not result.i2c_bus_idle:
        print("warning: I2C bus not idle after probe - stuck bus or missing pull-ups?", file=sys.stderr)


def _cmd_config(bridge: UartIicSpiBridge, args: argparse.Namespace) -> None:
    if args.i2c_clkdiv is not None:
        bridge.set_i2c_clkdiv(args.i2c_clkdiv)
    if args.spi_clkdiv is not None:
        bridge.set_spi_clkdiv(args.spi_clkdiv)
    if args.spi_mode is not None:
        bridge.set_spi_mode(args.spi_mode)
    if args.cs_polarity is not None:
        bridge.set_cs_polarity(args.cs_polarity)
    print("ok")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uart-iic-spi-bridge")
    parser.add_argument("-p", "--port", required=True, help="serial port, e.g. /dev/ttyUSB0 or COM3")
    parser.add_argument("-b", "--baudrate", type=int, default=115200)
    parser.add_argument("-t", "--timeout", type=float, default=1.0)

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping").set_defaults(func=_cmd_ping)
    sub.add_parser("id").set_defaults(func=_cmd_id)
    sub.add_parser("self-test").set_defaults(func=_cmd_self_test)
    sub.add_parser("i2c-scan").set_defaults(func=_cmd_i2c_scan)

    p = sub.add_parser("i2c-write")
    p.add_argument("addr")
    p.add_argument("data", nargs="*")
    p.set_defaults(func=_cmd_i2c_write)

    p = sub.add_parser("i2c-read")
    p.add_argument("addr")
    p.add_argument("nbytes", type=int)
    p.set_defaults(func=_cmd_i2c_read)

    p = sub.add_parser("i2c-write-read")
    p.add_argument("addr")
    p.add_argument("wdata", nargs="*")
    p.add_argument("--nread", type=int, required=True)
    p.set_defaults(func=_cmd_i2c_write_read)

    p = sub.add_parser("spi-xfer")
    p.add_argument("cs", type=int)
    p.add_argument("data", nargs="+")
    p.set_defaults(func=_cmd_spi_xfer)

    p = sub.add_parser("config")
    p.add_argument("--i2c-clkdiv", type=lambda x: int(x, 0))
    p.add_argument("--spi-clkdiv", type=lambda x: int(x, 0))
    p.add_argument("--spi-mode", type=int, choices=[0, 1, 2, 3])
    p.add_argument("--cs-polarity", type=lambda x: int(x, 0))
    p.set_defaults(func=_cmd_config)

    return parser


def main(argv: Sequence[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    bridge = UartIicSpiBridge(args.port, baudrate=args.baudrate, timeout=args.timeout)
    try:
        args.func(bridge, args)
    except (DeviceError, ProtocolError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
