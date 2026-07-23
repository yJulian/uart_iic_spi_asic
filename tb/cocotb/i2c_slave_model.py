"""A minimal event-driven I2C slave model for testing rtl/i2c_master.v.

Instead of assuming any particular timing (clk_div), this model samples
the open-drain bus once per system clock cycle (on the falling edge, safe
since the DUT is purely posedge-synchronous) and reacts to level
transitions - it works for any i2c_master clk_div.

Each byte time is 9 bit-periods: data bits 0..7 then an ack/nack bit
(bit_idx == 8). `_on_scl_falling` prepares what the slave drives for the
*current* bit_idx; `_on_scl_falling`'s matching `_on_scl_rising` samples
it and only then advances bit_idx - both halves of a bit period always
see the same bit_idx.
"""

from collections import deque

import cocotb
from cocotb.triggers import FallingEdge


class I2cSlaveModel:
    def __init__(self, dut, address, read_bytes=None, ack_address=True):
        self.dut = dut
        self.address = address
        self.read_queue = deque(read_bytes or [])
        self.written_bytes = []
        self.ack_address = ack_address
        self.nacked = False

        self._prev_scl = 1
        self._prev_sda = 1
        self._slave_drive_low = False

        self._role = "idle"  # idle | addr | wdata | rdata | ignore
        self._bit_idx = 0
        self._shift_in = 0
        self._shift_out = 0

    def start(self):
        cocotb.start_soon(self._run())

    async def _run(self):
        dut = self.dut
        dut.scl_in.value = 1
        dut.sda_in.value = 1
        while True:
            await FallingEdge(dut.clk)

            master_scl_low = bool(dut.scl_oe.value) and (int(dut.scl_out.value) == 0)
            scl = 0 if master_scl_low else 1
            scl_falling_now = (self._prev_scl == 1 and scl == 0)
            scl_rising_now = (self._prev_scl == 0 and scl == 1)

            # Falling edge: the slave must decide what it drives for the
            # *new* bit period before the bus value below is computed -
            # otherwise the driven bit would lag by one bit period.
            if scl_falling_now:
                self._on_scl_falling()

            master_sda_low = bool(dut.sda_oe.value) and (int(dut.sda_out.value) == 0)
            sda = 0 if (master_sda_low or self._slave_drive_low) else 1

            if self._prev_scl == 1 and scl == 1:
                if self._prev_sda == 1 and sda == 0:
                    self._on_start()
                elif self._prev_sda == 0 and sda == 1:
                    self._on_stop()

            if scl_rising_now:
                self._on_scl_rising(sda)

            self._prev_scl, self._prev_sda = scl, sda
            dut.scl_in.value = 1  # this model never stretches the clock
            dut.sda_in.value = sda

    def _on_start(self):
        self._role = "addr"
        self._bit_idx = 0
        self._shift_in = 0
        self._slave_drive_low = False

    def _on_stop(self):
        self._role = "idle"
        self._slave_drive_low = False

    # ---- prepare what to drive for the *current* bit_idx ----
    def _on_scl_falling(self):
        if self._role in ("addr", "wdata"):
            if self._bit_idx < 8:
                self._slave_drive_low = False  # receiving, release SDA
            else:
                addr7 = (self._shift_in >> 1) & 0x7F
                rw = self._shift_in & 1
                if self._role == "addr":
                    match = (addr7 == self.address) and self.ack_address
                    self._slave_drive_low = match
                    self._pending_next_role = ("rdata" if rw else "wdata") if match else "ignore"
                else:
                    self.written_bytes.append(self._shift_in)
                    self._slave_drive_low = True  # ACK the written byte
                    self._pending_next_role = "wdata"
        elif self._role == "rdata":
            if self._bit_idx < 8:
                bit = (self._shift_out >> (7 - self._bit_idx)) & 1
                self._slave_drive_low = (bit == 0)
            else:
                self._slave_drive_low = False  # release, listen for master's ack/nack

    # ---- sample/consume the bit that was just driven, then advance ----
    def _on_scl_rising(self, sda_bit):
        if self._role in ("addr", "wdata"):
            if self._bit_idx < 8:
                self._shift_in = ((self._shift_in << 1) | sda_bit) & 0xFF
                self._bit_idx += 1
            else:
                self._role = getattr(self, "_pending_next_role", "ignore")
                self._bit_idx = 0
                self._shift_in = 0
                if self._role == "rdata":
                    self._load_next_read_byte()
        elif self._role == "rdata":
            if self._bit_idx < 8:
                self._bit_idx += 1
            else:
                self.nacked = bool(sda_bit)
                self._bit_idx = 0
                if self.nacked:
                    self._role = "ignore"
                else:
                    self._load_next_read_byte()

    def _load_next_read_byte(self):
        self._shift_out = self.read_queue.popleft() if self.read_queue else 0xFF
