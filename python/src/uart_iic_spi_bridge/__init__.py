"""Host driver for the UART-to-I2C/SPI bridge ASIC.

See ``docs/PROTOCOL.md`` in the repository root for the wire protocol
this package implements.
"""

from .bridge import SelfTestResult, UartIicSpiBridge
from .exceptions import (
    BridgeError,
    ChecksumRejected,
    DeviceError,
    LengthRejected,
    NackError,
    OpcodeRejected,
    ProtocolError,
    ResponseChecksumError,
)

__version__ = "0.1.0"

__all__ = [
    "UartIicSpiBridge",
    "SelfTestResult",
    "BridgeError",
    "ProtocolError",
    "ResponseChecksumError",
    "DeviceError",
    "NackError",
    "ChecksumRejected",
    "LengthRejected",
    "OpcodeRejected",
]
