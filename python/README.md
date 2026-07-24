# uart-iic-spi-bridge (Python driver)

Host-side driver for the [UART-to-I2C/SPI bridge ASIC](../README.md). It
implements the framed command/response protocol from
[`../docs/PROTOCOL.md`](../docs/PROTOCOL.md) on top of a plain 8N1 UART
via [pyserial](https://pyserial.readthedocs.io/).

## Install

```console
$ pip install -e .            # from this directory (python/)
```

or, from the repository root:

```console
$ pip install -e python/
```

## Quick start

```python
from uart_iic_spi_bridge import UartIicSpiBridge, NackError

with UartIicSpiBridge("/dev/ttyUSB0", baudrate=115200) as bridge:
    bridge.ping()
    version, device_id = bridge.get_id()

    # I2C
    bridge.i2c_write(0x50, bytes([0x00, 0xAB]))          # write reg ptr + data
    data = bridge.i2c_read(0x50, 4)                       # plain read
    data = bridge.i2c_write_read(0x50, b"\x00", nread=2)  # write-ptr-then-read

    # SPI
    bridge.set_spi_mode(0)
    rdata = bridge.spi_xfer(cs=0, data=bytes([0x9F, 0x00, 0x00, 0x00]))

    # Runtime config (see docs/PROTOCOL.md SET_CONFIG for units)
    bridge.set_i2c_clkdiv(300)
    bridge.set_spi_clkdiv(50)
    bridge.set_cs_polarity(0x00)  # both CS lines active-low (default)
```

`baudrate` must match the `BAUD`/`CLK_FREQ_HZ` parameters the chip was
synthesized with (`rtl/uart_iic_spi_bridge.v`) - it's not runtime
negotiable, per the protocol's known limitations.

## API

One method per opcode in `docs/PROTOCOL.md`, all on `UartIicSpiBridge`:

| Method | Opcode |
|---|---|
| `ping()` | `0x01` |
| `get_id()` | `0x02` |
| `i2c_write(addr, data)` | `0x10` |
| `i2c_read(addr, nbytes)` | `0x11` |
| `i2c_write_read(addr, wdata, nread)` | `0x12` |
| `spi_xfer(cs, data)` | `0x20` |
| `set_i2c_clkdiv`, `set_spi_clkdiv`, `set_spi_mode`, `set_cs_polarity` | `0x30` |
| `get_status()` | `0x31` |

Errors surface as exceptions rather than status codes:

- `NackError` - an I2C slave didn't acknowledge (address or data byte).
- `ChecksumRejected` / `LengthRejected` / `OpcodeRejected` - the device
  rejected the command frame itself (usually a driver bug, not a wiring
  issue).
- `ResponseChecksumError` - the response frame's checksum didn't match
  (line noise, wrong baud rate, ...).
- `ProtocolError` - anything else malformed about the response (short
  read/timeout, unexpected RLEN for the opcode).

All of the above derive from `BridgeError`.

`UartIicSpiBridge.from_serial(ser)` wraps an already-open serial-like
object (anything with blocking `read(n) -> bytes` / `write(bytes)`)
instead of opening a port - this is how the test suite drives the client
half of the protocol against a fake device without real hardware.

## CLI

Installing the package also installs a small CLI for bring-up:

```console
$ uart-iic-spi-bridge -p /dev/ttyUSB0 ping
$ uart-iic-spi-bridge -p /dev/ttyUSB0 id
$ uart-iic-spi-bridge -p /dev/ttyUSB0 i2c-scan
$ uart-iic-spi-bridge -p /dev/ttyUSB0 i2c-write 0x50 00 ab
$ uart-iic-spi-bridge -p /dev/ttyUSB0 i2c-read 0x50 4
$ uart-iic-spi-bridge -p /dev/ttyUSB0 i2c-write-read 0x50 00 --nread 2
$ uart-iic-spi-bridge -p /dev/ttyUSB0 spi-xfer 0 9f 00 00 00
$ uart-iic-spi-bridge -p /dev/ttyUSB0 config --spi-mode 0 --i2c-clkdiv 300
```

## Examples

See `examples/` for runnable scripts: `ping_and_id.py`, `i2c_eeprom.py`,
`i2c_scan.py`, `spi_xfer.py`.

## Tests

The test suite runs against a fake in-process serial transport that
speaks the `cmd_engine` framing, so it needs no hardware:

```console
$ pip install -e ".[test]"
$ pytest tests/
```
