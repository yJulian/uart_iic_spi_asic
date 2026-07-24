"""Host-side driver for the UART-to-I2C/SPI bridge ASIC.

Implements the framed command/response protocol from ``docs/PROTOCOL.md``
on top of a plain 8N1 UART (via `pyserial <https://pyserial.readthedocs.io/>`_).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from . import constants as c
from .exceptions import (
    ChecksumRejected,
    DeviceError,
    LengthRejected,
    NackError,
    OpcodeRejected,
    ProtocolError,
    ResponseChecksumError,
)


def _checksum(data: bytes) -> int:
    cksum = 0
    for b in data:
        cksum ^= b
    return cksum


@dataclass
class SelfTestResult:
    """Result of `UartIicSpiBridge.self_test`."""

    i2c_bus_idle: bool
    spi_loopback_ok: bool
    spi_echo_byte: int

    @property
    def passed(self) -> bool:
        """Overall pass/fail, mirroring the device's ACK/NACK verdict.

        Only `i2c_bus_idle` gates this - `spi_loopback_ok` is
        informational, since most setups have no MOSI-MISO jumper.
        """
        return self.i2c_bus_idle


class UartIicSpiBridge:
    """Driver for one bridge device reachable over a serial port.

    Usage::

        with UartIicSpiBridge("/dev/ttyUSB0", baudrate=115200) as bridge:
            bridge.ping()
            version, device_id = bridge.get_id()
            bridge.i2c_write(0x50, bytes([0x00, 0xAB]))
            data = bridge.i2c_read(0x50, 4)

    The command protocol has no pipelining (one command, one response, no
    overlap) so a single instance is not safe to share across threads
    without external locking.
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 1.0,
        max_payload: Optional[int] = c.DEFAULT_MAX_PAYLOAD,
        **serial_kwargs,
    ):
        """Open ``port`` and wrap it as a bridge.

        ``max_payload`` mirrors ``cmd_engine``'s synthesis-time
        ``MAX_PAYLOAD`` parameter (default 16) and is used only for an
        early, local sanity check before sending a command that would
        otherwise be rejected with ``ERR_LEN``; pass ``None`` to skip it
        if your build was synthesized with a different value.
        Extra keyword arguments are forwarded to ``serial.Serial``.
        """
        import serial  # imported lazily so the package has no hard

        # dependency on pyserial for users who only want the pure-Python
        # framing helpers (e.g. to drive a cocotb testbench).
        self._serial = serial.Serial(port, baudrate=baudrate, timeout=timeout, **serial_kwargs)
        self.max_payload = max_payload

    @classmethod
    def from_serial(cls, ser, max_payload: Optional[int] = c.DEFAULT_MAX_PAYLOAD) -> "UartIicSpiBridge":
        """Wrap an already-open serial-like object (must have blocking
        ``read(n) -> bytes`` and ``write(bytes)`` methods). Useful for
        testing against a fake transport or a non-pyserial backend."""
        self = cls.__new__(cls)
        self._serial = ser
        self.max_payload = max_payload
        return self

    def close(self) -> None:
        self._serial.close()

    def __enter__(self) -> "UartIicSpiBridge":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Framing
    # ------------------------------------------------------------------

    def _read_exact(self, n: int) -> bytes:
        if n == 0:
            return b""
        data = self._serial.read(n)
        if len(data) != n:
            raise ProtocolError(
                f"timed out reading response: expected {n} byte(s), got {len(data)}"
            )
        return data

    @staticmethod
    def _raise_for_status(status: int, rdata: bytes) -> None:
        if status == c.STATUS_ACK:
            return
        if status == c.STATUS_NACK:
            raise NackError(status, rdata)
        if status == c.STATUS_ERR_CHECKSUM:
            raise ChecksumRejected(status, rdata)
        if status == c.STATUS_ERR_LEN:
            raise LengthRejected(status, rdata)
        if status == c.STATUS_ERR_OPCODE:
            raise OpcodeRejected(status, rdata)
        raise DeviceError(status, rdata)

    def _transact(self, opcode: int, payload: bytes = b"", expect_rlen: Optional[int] = None) -> bytes:
        """Send one command frame and return RDATA from its response.

        Raises a `DeviceError` subclass if the device responds with a
        non-ACK status, or a `ProtocolError` if the response frame itself
        is malformed (bad checksum, short read, unexpected RLEN).
        """
        if self.max_payload is not None and len(payload) > self.max_payload:
            raise ValueError(
                f"payload length {len(payload)} exceeds max_payload {self.max_payload}"
            )
        if len(payload) > 0xFF:
            raise ValueError("payload length must fit in a single byte (<= 255)")

        frame = bytes([opcode, len(payload)]) + payload
        frame += bytes([_checksum(frame)])
        self._serial.write(frame)

        header = self._read_exact(2)
        status, rlen = header[0], header[1]
        rdata = self._read_exact(rlen)
        rx_checksum = self._read_exact(1)[0]

        expected = _checksum(header + rdata)
        if rx_checksum != expected:
            raise ResponseChecksumError(
                f"response checksum mismatch: got 0x{rx_checksum:02x}, expected 0x{expected:02x}"
            )

        self._raise_for_status(status, rdata)

        if expect_rlen is not None and len(rdata) != expect_rlen:
            raise ProtocolError(
                f"expected {expect_rlen} response byte(s) on ACK, got {len(rdata)}"
            )
        return rdata

    @staticmethod
    def _check_addr(addr: int) -> None:
        if not 0 <= addr <= 0x7F:
            raise ValueError(f"I2C address must be a 7-bit value (0x00-0x7F), got {addr!r}")

    # ------------------------------------------------------------------
    # 0x01 / 0x02 - link / identity
    # ------------------------------------------------------------------

    def ping(self) -> None:
        """Verify the link is alive at the current baud rate. Raises on failure."""
        self._transact(c.OP_PING, expect_rlen=0)

    def get_id(self) -> Tuple[int, int]:
        """Return ``(version, device_id)``."""
        rdata = self._transact(c.OP_GET_ID, expect_rlen=2)
        return rdata[0], rdata[1]

    # ------------------------------------------------------------------
    # 0x10 / 0x11 / 0x12 - I2C
    # ------------------------------------------------------------------

    def i2c_write(self, addr: int, data: bytes = b"") -> None:
        """START, ``addr`` with R/W=0, each byte of ``data``, STOP.

        Raises `NackError` if the address or any data byte was not
        acknowledged.
        """
        self._check_addr(addr)
        data = bytes(data)
        self._transact(c.OP_I2C_WRITE, bytes([addr]) + data, expect_rlen=0)

    def i2c_read(self, addr: int, nbytes: int) -> bytes:
        """START, ``addr`` with R/W=1, read ``nbytes`` bytes, STOP.

        Returns the bytes read. Raises `NackError` if the address was not
        acknowledged.
        """
        self._check_addr(addr)
        if not 0 <= nbytes <= 0xFF:
            raise ValueError(f"nbytes must fit in a byte, got {nbytes!r}")
        return self._transact(c.OP_I2C_READ, bytes([addr, nbytes]), expect_rlen=nbytes)

    def i2c_write_read(self, addr: int, wdata: bytes, nread: int) -> bytes:
        """Write phase, repeated START, read phase, STOP.

        The standard "write a register pointer, then read back" idiom.
        Returns the ``nread`` bytes read back. Raises `NackError` if any
        address or write byte was not acknowledged.
        """
        self._check_addr(addr)
        wdata = bytes(wdata)
        if len(wdata) > 0xFF:
            raise ValueError("wdata is too long to fit its length in a byte")
        if not 0 <= nread <= 0xFF:
            raise ValueError(f"nread must fit in a byte, got {nread!r}")
        payload = bytes([addr, len(wdata)]) + wdata + bytes([nread])
        return self._transact(c.OP_I2C_WRITE_READ, payload, expect_rlen=nread)

    # ------------------------------------------------------------------
    # 0x20 - SPI
    # ------------------------------------------------------------------

    def spi_xfer(self, cs: int, data: bytes) -> bytes:
        """Full-duplex transfer of ``data`` with chip-select ``cs`` held
        asserted across the whole burst. Returns the bytes shifted back
        on MISO (same length as ``data``)."""
        data = bytes(data)
        rdata = self._transact(c.OP_SPI_XFER, bytes([cs]) + data, expect_rlen=len(data))
        return rdata

    # ------------------------------------------------------------------
    # 0x30 - runtime configuration
    # ------------------------------------------------------------------

    def _set_config(self, param_id: int, value: bytes) -> None:
        self._transact(c.OP_SET_CONFIG, bytes([param_id]) + value, expect_rlen=0)

    def set_i2c_clkdiv(self, clkdiv: int) -> None:
        """Set the I2C quarter-bit-period clock divider (system clock cycles).

        Applies to the next I2C transaction; there's no separate "apply" step.
        """
        if not 0 <= clkdiv <= 0xFFFF:
            raise ValueError("clkdiv must fit in 16 bits")
        self._set_config(c.PARAM_I2C_CLKDIV, clkdiv.to_bytes(2, "big"))

    def set_spi_clkdiv(self, clkdiv: int) -> None:
        """Set the SPI half-SCLK-period clock divider (system clock cycles).

        Applies to the next SPI transaction; there's no separate "apply" step.
        """
        if not 0 <= clkdiv <= 0xFFFF:
            raise ValueError("clkdiv must fit in 16 bits")
        self._set_config(c.PARAM_SPI_CLKDIV, clkdiv.to_bytes(2, "big"))

    def set_spi_mode(self, mode: int) -> None:
        """Set SPI mode 0-3 (``mode = (CPOL << 1) | CPHA``)."""
        if not 0 <= mode <= 3:
            raise ValueError("SPI mode must be 0-3")
        self._set_config(c.PARAM_SPI_MODE, bytes([mode]))

    def set_cs_polarity(self, active_high_mask: int) -> None:
        """Set per-CS-line polarity: bit N set = CS line N is active-high
        (default active-low)."""
        if not 0 <= active_high_mask <= 0xFF:
            raise ValueError("active_high_mask must fit in a byte")
        self._set_config(c.PARAM_CS_POLARITY, bytes([active_high_mask]))

    # ------------------------------------------------------------------
    # 0x31 - status
    # ------------------------------------------------------------------

    def get_status(self) -> int:
        """Return the raw STATUS byte of the *previous* command."""
        rdata = self._transact(c.OP_GET_STATUS, expect_rlen=1)
        return rdata[0]

    # ------------------------------------------------------------------
    # 0x40 - self test
    # ------------------------------------------------------------------

    def self_test(self) -> SelfTestResult:
        """Run the device's built-in power-on self test.

        Probes the I2C bus with a bare START/STOP (no address byte, so
        nothing can NACK) and checks it settles idle-high, then shifts a
        known byte out on SPI CS0 to see if it loops back on MISO.

        Unlike the other calls, this never raises `NackError` on a
        failed check - the device's NACK (a stuck I2C bus) is folded
        into the returned result's `i2c_bus_idle`/`passed` fields so
        callers can inspect a failed self test without a try/except.
        """
        try:
            rdata = self._transact(c.OP_SELF_TEST, expect_rlen=2)
        except NackError as exc:
            rdata = exc.rdata
        flags, spi_echo = rdata[0], rdata[1]
        return SelfTestResult(
            i2c_bus_idle=bool(flags & c.SELF_TEST_I2C_BUS_IDLE),
            spi_loopback_ok=bool(flags & c.SELF_TEST_SPI_LOOPBACK),
            spi_echo_byte=spi_echo,
        )
