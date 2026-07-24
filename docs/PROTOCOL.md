# UART Command Protocol

This document specifies the framed command/response protocol implemented
by `rtl/cmd_engine.v`. A host (PC, microcontroller, ...) drives the chip
over a plain 8N1 UART; every command gets exactly one response.

## Framing

Host to device:

```
| OPCODE (1B) | LEN (1B) | PAYLOAD (LEN bytes) | CHECKSUM (1B) |
```

Device to host:

```
| STATUS (1B) | RLEN (1B) | RDATA (RLEN bytes) | CHECKSUM (1B) |
```

`CHECKSUM` is the XOR of every preceding byte in the frame (OPCODE, LEN,
and all payload bytes for a command; STATUS, RLEN, and all data bytes for
a response).

The device only starts parsing a new command once the previous one's
response has been fully sent - there is no pipelining. Maximum payload
size (both directions) is `MAX_PAYLOAD` bytes, a `cmd_engine` parameter
(default 16). A command whose `LEN` exceeds `MAX_PAYLOAD` is rejected with
`ERR_LEN`.

## Status codes

| Code | Name | Meaning |
|---|---|---|
| `0x06` | `ACK` | Command completed successfully |
| `0x15` | `NACK` | An I2C slave did not acknowledge (address or a data byte) |
| `0x16` | `ERR_CHECKSUM` | The command frame's checksum did not match |
| `0x17` | `ERR_LEN` | `LEN` was invalid for the given opcode |
| `0x18` | `ERR_OPCODE` | Unrecognized opcode |

## Opcodes

### `0x01` PING

No payload. Always responds `ACK` with `RLEN = 0`. Useful for verifying
the link is alive at a given baud rate.

### `0x02` GET_ID

No payload. Responds `ACK`, `RLEN = 2`, `RDATA = [VERSION, DEVICE_ID]`
(currently `0x01, 0xA5`).

### `0x10` I2C_WRITE

Payload: `[ADDR, DATA[0..N-1]]` - `ADDR` is the plain 7-bit I2C address in
bits `[6:0]` (bit 7 reserved), `N = LEN - 1` data bytes follow. Issues a
START, the address byte with R/W=0, then each data byte, then STOP.
Responds `ACK` (`RLEN = 0`) if every byte was acknowledged, or `NACK` if
the address or any data byte was not.

### `0x11` I2C_READ

Payload: `[ADDR, NBYTES]` (`LEN` must be exactly 2, `NBYTES <= MAX_PAYLOAD`).
Issues START, the address byte with R/W=1, then reads `NBYTES` bytes
(ACKing all but the last, which is NACKed per the I2C spec), then STOP.
Responds `ACK` with `RDATA` holding the bytes read, or `NACK` (`RLEN = 0`)
if the address was not acknowledged.

### `0x12` I2C_WRITE_READ

Payload: `[ADDR, NWRITE, WDATA[0..NWRITE-1], NREAD]` - `LEN` must equal
`NWRITE + 3`. Performs a write phase (START, address R/W=0, `NWRITE` data
bytes) followed immediately by a repeated START and a read phase (address
R/W=1, `NREAD` bytes), then STOP. This is the standard "write a register
pointer, then read back" I2C idiom. Responds `ACK` with the `NREAD` bytes
read, or `NACK` (`RLEN = 0`) if any address or write byte was not
acknowledged.

### `0x20` SPI_XFER

Payload: `[CS_SEL, DATA[0..N-1]]`, `N = LEN - 1`. `CS_SEL` selects which
chip-select line to assert (0 or 1 on this design). Performs one
full-duplex SPI transaction: asserts the selected CS, shifts out all `N`
bytes back-to-back (CS stays asserted between bytes), then deasserts CS.
Responds `ACK` with `RDATA` holding the `N` bytes shifted back on MISO.

### `0x30` SET_CONFIG

Payload: `[PARAM_ID, VALUE...]`.

| `PARAM_ID` | Name | Value | Effect |
|---|---|---|---|
| `0x01` | `I2C_CLKDIV` | 2 bytes, big-endian | I2C quarter-bit-period divider |
| `0x02` | `SPI_CLKDIV` | 2 bytes, big-endian | SPI half-SCLK-period divider |
| `0x03` | `SPI_MODE` | 1 byte, bits `[1:0] = {CPOL, CPHA}` | SPI mode 0-3 |
| `0x04` | `CS_POLARITY` | 1 byte, bit per CS line | 1 = active-high, 0 = active-low (default) |

Responds `ACK` (`RLEN = 0`) on success, `ERR_LEN` if the value length
doesn't match the parameter, or `NACK` for an unknown `PARAM_ID`.

Clock dividers are counted in system clock cycles and apply immediately
to the *next* I2C/SPI transaction; there's no separate "apply" step.

### `0x31` GET_STATUS

No payload. Responds `ACK` with `RLEN = 1`, `RDATA[0]` holding the
`STATUS` byte of the *previous* command (useful for polling after a
command whose own response you didn't check, or just for a sanity check).

### `0x40` SELF_TEST

No payload. Runs a built-in power-on self test (POST) of the I2C and SPI
hardware and reports back a diagnostic byte:

1. Issues a bare I2C START immediately followed by STOP - no address byte
   is sent, so there is nothing to NACK. This exercises the I2C master's
   line drivers and leaves the bus idle.
2. Samples `SCL`/`SDA` right after the STOP. On a healthy bus with
   pull-ups populated and nothing stuck, both read high.
3. Shifts the fixed byte `0x5A` out on SPI CS0 (using whatever
   `SPI_CLKDIV`/`SPI_MODE` is currently configured) and captures whatever
   comes back on MISO.

Responds `RLEN = 2`, `RDATA = [FLAGS, SPI_ECHO]`:

| `FLAGS` bit | Name | Meaning |
|---|---|---|
| `0` | `I2C_BUS_IDLE` | `SCL` and `SDA` both sampled high after the probe |
| `1` | `SPI_LOOPBACK` | `SPI_ECHO == 0x5A` - only set if `MOSI` is externally jumpered to `MISO` on the test fixture |
| `2-7` | *(reserved)* | always `0` |

`SPI_ECHO` is the raw byte read back on MISO during the probe, reported
regardless of whether it matched, for diagnostics.

`STATUS` is `ACK` if `I2C_BUS_IDLE` is set, `NACK` otherwise (a stuck bus
or missing pull-ups is the one condition this test can detect without any
external jig). `SPI_LOOPBACK` never affects `STATUS` since most setups
have no loopback jumper installed.

## Example: reading two registers from an I2C EEPROM at address 0x50

Write the register pointer, then read back 2 bytes with a repeated START:

```
Host -> Device: 12 04 50 01 00 02 <checksum>
                 (opcode=I2C_WRITE_READ, len=4, addr=0x50, nwrite=1,
                  wdata=[0x00], nread=2)
Device -> Host: 06 02 <byte0> <byte1> <checksum>
```

## Known limitations

- I2C clock stretching is not supported by `rtl/i2c_master.v` - the
  master does not wait for a slave to hold SCL low.
- UART baud rate is a synthesis-time parameter (`rtl/uart_iic_spi_bridge.v`
  `BAUD`/`CLK_FREQ_HZ`), not runtime-configurable, since the host has no
  channel to negotiate a new baud rate before the change takes effect.
- No command pipelining: send one command, wait for its full response,
  then send the next.
