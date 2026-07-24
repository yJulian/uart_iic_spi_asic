from .constants import STATUS_NAMES


class BridgeError(Exception):
    """Base class for all errors raised by this library."""


class ProtocolError(BridgeError):
    """The response frame from the device was malformed or unexpected."""


class ResponseChecksumError(ProtocolError):
    """The response frame's checksum did not match its contents."""


class DeviceError(BridgeError):
    """The device returned a non-ACK status for a command.

    ``status`` is the raw status byte; ``rdata`` is whatever RDATA
    accompanied it (usually empty for error statuses).
    """

    def __init__(self, status: int, rdata: bytes = b""):
        self.status = status
        self.rdata = rdata
        name = STATUS_NAMES.get(status, f"0x{status:02x}")
        super().__init__(f"device returned status {name}")


class NackError(DeviceError):
    """An I2C slave did not acknowledge (address or a data byte)."""


class ChecksumRejected(DeviceError):
    """The device rejected the command frame's checksum (ERR_CHECKSUM)."""


class LengthRejected(DeviceError):
    """LEN was invalid for the given opcode (ERR_LEN)."""


class OpcodeRejected(DeviceError):
    """The device did not recognize the opcode (ERR_OPCODE)."""
