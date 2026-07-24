"""Unit tests for the bridge driver against a fake in-process transport.

No real hardware or serial port is needed: `FakeDeviceSerial` implements
the same framing the RTL's `cmd_engine` speaks and lets each test define
how it responds per opcode.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from uart_iic_spi_bridge import (
    ChecksumRejected,
    LengthRejected,
    NackError,
    OpcodeRejected,
    ResponseChecksumError,
    UartIicSpiBridge,
)
from uart_iic_spi_bridge import constants as c


def _checksum(data: bytes) -> int:
    cksum = 0
    for b in data:
        cksum ^= b
    return cksum


class FakeDeviceSerial:
    """Stands in for `serial.Serial`, backed by a responder callback.

    `responder(opcode, payload) -> (status, rdata)` decides how the fake
    device answers each command frame.
    """

    def __init__(self, responder, corrupt_checksum=False):
        self._responder = responder
        self._corrupt_checksum = corrupt_checksum
        self._rx_buf = bytearray()
        self.written_frames = []

    def write(self, data: bytes) -> int:
        data = bytes(data)
        opcode, length = data[0], data[1]
        payload = data[2 : 2 + length]
        self.written_frames.append(data)

        status, rdata = self._responder(opcode, payload)
        header = bytes([status, len(rdata)]) + rdata
        checksum = _checksum(header)
        if self._corrupt_checksum:
            checksum ^= 0xFF
        self._rx_buf += header + bytes([checksum])
        return len(data)

    def read(self, n: int) -> bytes:
        n = min(n, len(self._rx_buf))
        out = bytes(self._rx_buf[:n])
        del self._rx_buf[:n]
        return out

    def close(self) -> None:
        pass


def make_bridge(responder, **kwargs):
    ser = FakeDeviceSerial(responder, **kwargs)
    return UartIicSpiBridge.from_serial(ser), ser


def test_ping():
    def responder(opcode, payload):
        assert opcode == c.OP_PING
        assert payload == b""
        return c.STATUS_ACK, b""

    bridge, _ = make_bridge(responder)
    bridge.ping()  # must not raise


def test_get_id():
    def responder(opcode, payload):
        return c.STATUS_ACK, bytes([0x01, 0xA5])

    bridge, _ = make_bridge(responder)
    assert bridge.get_id() == (0x01, 0xA5)


def test_i2c_write_read_matches_protocol_doc_example():
    # docs/PROTOCOL.md's worked example: reading two bytes from an I2C
    # EEPROM at 0x50 via a write-pointer-then-read.
    eeprom = {0x00: 0xDE, 0x01: 0xAD}

    def responder(opcode, payload):
        assert opcode == c.OP_I2C_WRITE_READ
        addr, nwrite = payload[0], payload[1]
        wdata = payload[2 : 2 + nwrite]
        nread = payload[2 + nwrite]
        assert addr == 0x50
        ptr = wdata[0]
        data = bytes(eeprom[ptr + i] for i in range(nread))
        return c.STATUS_ACK, data

    bridge, ser = make_bridge(responder)
    result = bridge.i2c_write_read(0x50, bytes([0x00]), 2)
    assert result == bytes([0xDE, 0xAD])

    sent = ser.written_frames[0]
    expected_body = bytes([0x12, 0x04, 0x50, 0x01, 0x00, 0x02])
    assert sent[:-1] == expected_body
    assert sent[-1] == _checksum(expected_body) == 0x45


def test_i2c_write_nack_raises():
    def responder(opcode, payload):
        return c.STATUS_NACK, b""

    bridge, _ = make_bridge(responder)
    with pytest.raises(NackError):
        bridge.i2c_write(0x50, b"\x01")


def test_i2c_read_returns_bytes():
    def responder(opcode, payload):
        addr, nbytes = payload
        return c.STATUS_ACK, bytes(range(nbytes))

    bridge, _ = make_bridge(responder)
    assert bridge.i2c_read(0x20, 3) == bytes([0, 1, 2])


def test_spi_xfer_roundtrip():
    def responder(opcode, payload):
        assert opcode == c.OP_SPI_XFER
        cs = payload[0]
        assert cs == 1
        return c.STATUS_ACK, bytes(b ^ 0xFF for b in payload[1:])

    bridge, _ = make_bridge(responder)
    result = bridge.spi_xfer(1, bytes([0x00, 0x0F]))
    assert result == bytes([0xFF, 0xF0])


def test_set_spi_mode_rejects_invalid_locally():
    bridge, ser = make_bridge(lambda op, pl: (c.STATUS_ACK, b""))
    with pytest.raises(ValueError):
        bridge.set_spi_mode(4)
    assert ser.written_frames == []  # never touched the wire


def test_set_config_i2c_clkdiv_encodes_big_endian():
    def responder(opcode, payload):
        assert opcode == c.OP_SET_CONFIG
        assert payload == bytes([c.PARAM_I2C_CLKDIV, 0x01, 0x2C])
        return c.STATUS_ACK, b""

    bridge, _ = make_bridge(responder)
    bridge.set_i2c_clkdiv(0x012C)


def test_device_error_opcode_rejected():
    def responder(opcode, payload):
        return c.STATUS_ERR_OPCODE, b""

    bridge, _ = make_bridge(responder)
    with pytest.raises(OpcodeRejected):
        bridge.ping()


def test_device_error_len_rejected():
    def responder(opcode, payload):
        return c.STATUS_ERR_LEN, b""

    bridge, _ = make_bridge(responder)
    with pytest.raises(LengthRejected):
        bridge.i2c_read(0x10, 1)


def test_device_error_checksum_rejected():
    def responder(opcode, payload):
        return c.STATUS_ERR_CHECKSUM, b""

    bridge, _ = make_bridge(responder)
    with pytest.raises(ChecksumRejected):
        bridge.ping()


def test_response_checksum_mismatch_detected():
    bridge, _ = make_bridge(lambda op, pl: (c.STATUS_ACK, b""), corrupt_checksum=True)
    with pytest.raises(ResponseChecksumError):
        bridge.ping()


def test_get_status():
    def responder(opcode, payload):
        assert opcode == c.OP_GET_STATUS
        return c.STATUS_ACK, bytes([c.STATUS_NACK])

    bridge, _ = make_bridge(responder)
    assert bridge.get_status() == c.STATUS_NACK


def test_self_test_passes_when_bus_idle():
    def responder(opcode, payload):
        assert opcode == c.OP_SELF_TEST
        assert payload == b""
        flags = c.SELF_TEST_I2C_BUS_IDLE
        return c.STATUS_ACK, bytes([flags, 0xFF])

    bridge, _ = make_bridge(responder)
    result = bridge.self_test()
    assert result.i2c_bus_idle is True
    assert result.spi_loopback_ok is False
    assert result.spi_echo_byte == 0xFF
    assert result.passed is True


def test_self_test_reports_stuck_bus_without_raising():
    def responder(opcode, payload):
        return c.STATUS_NACK, bytes([0x00, 0xFF])

    bridge, _ = make_bridge(responder)
    result = bridge.self_test()  # must not raise
    assert result.i2c_bus_idle is False
    assert result.passed is False


def test_self_test_detects_spi_loopback():
    def responder(opcode, payload):
        flags = c.SELF_TEST_I2C_BUS_IDLE | c.SELF_TEST_SPI_LOOPBACK
        return c.STATUS_ACK, bytes([flags, c.SELF_TEST_PATTERN])

    bridge, _ = make_bridge(responder)
    result = bridge.self_test()
    assert result.spi_loopback_ok is True
    assert result.spi_echo_byte == c.SELF_TEST_PATTERN


def test_i2c_address_out_of_range_rejected_locally():
    bridge, ser = make_bridge(lambda op, pl: (c.STATUS_ACK, b""))
    with pytest.raises(ValueError):
        bridge.i2c_write(0x80, b"\x00")
    assert ser.written_frames == []


def test_payload_exceeding_max_payload_rejected_locally():
    bridge, ser = make_bridge(lambda op, pl: (c.STATUS_ACK, b""), )
    with pytest.raises(ValueError):
        bridge.i2c_write(0x50, bytes(20))
    assert ser.written_frames == []
