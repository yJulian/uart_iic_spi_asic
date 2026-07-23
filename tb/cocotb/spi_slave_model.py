"""A minimal event-driven SPI slave model for testing rtl/spi_master.v.

Unlike I2C, SPI has no open-drain arbitration - the slave only needs to
drive `miso` and sample `mosi` on the edges dictated by (CPOL, CPHA),
using the same "leading/trailing edge" rules the master itself follows.
"""

from collections import deque

import cocotb
from cocotb.triggers import FallingEdge


class SpiSlaveModel:
    def __init__(self, dut, cpol, cpha, tx_bytes=None, cs_active_level=0):
        self.dut = dut
        self.cpol = cpol
        self.cpha = cpha
        self.cs_active_level = cs_active_level
        self.tx_queue = deque(tx_bytes or [])
        self.rx_bytes = []

        self._prev_sclk = 0
        self._prev_cs_active = False
        self._bit_idx = 0
        self._shift_in = 0
        self._shift_out = 0
        self._skip_next_trailing = False
        self.miso_val = 1

    def start(self):
        cocotb.start_soon(self._run())

    def _cs_active(self):
        # cs_n[0] (bit 0 of the vector) is used for every test in this project.
        return (int(self.dut.cs_n.value) & 1) == self.cs_active_level

    async def _run(self):
        dut = self.dut
        dut.miso.value = 1
        while True:
            await FallingEdge(dut.clk)
            cs_active = self._cs_active()
            sclk = int(dut.sclk.value)

            if cs_active and not self._prev_cs_active:
                # CS just asserted: sclk is settling to its idle level
                # (cpol) as part of activation, not a real bit-transfer
                # edge - establish the edge-tracking baseline here rather
                # than assuming what the DUT's reset value was.
                self._bit_idx = 0
                self._shift_in = 0
                self._skip_next_trailing = False
                self._load_next_tx_byte()
                if not self.cpha:
                    dut.miso.value = (self._shift_out >> 7) & 1
                self._prev_sclk = sclk

            elif cs_active:
                leading = (self._prev_sclk == self.cpol) and (sclk != self.cpol)
                trailing = (self._prev_sclk != self.cpol) and (sclk == self.cpol)

                if not self.cpha:
                    if leading:
                        bit = int(dut.mosi.value)
                        self._shift_in = ((self._shift_in << 1) | bit) & 0xFF
                        self._bit_idx += 1
                        if self._bit_idx == 8:
                            self.rx_bytes.append(self._shift_in)
                            self._load_next_tx_byte()
                            self._bit_idx = 0
                            # Pre-load the next byte's MSB now: for CPHA=0
                            # it must be valid *before* the next byte's
                            # first leading edge, and no real sclk edge
                            # occurs during the inter-byte CS-held gap.
                            dut.miso.value = (self._shift_out >> 7) & 1
                            self._skip_next_trailing = True
                    elif trailing:
                        if self._skip_next_trailing:
                            self._skip_next_trailing = False
                        else:
                            self._shift_out = (self._shift_out << 1) & 0xFF
                            dut.miso.value = (self._shift_out >> 7) & 1
                else:
                    if leading:
                        dut.miso.value = (self._shift_out >> 7) & 1
                        self._shift_out = (self._shift_out << 1) & 0xFF
                    elif trailing:
                        bit = int(dut.mosi.value)
                        self._shift_in = ((self._shift_in << 1) | bit) & 0xFF
                        self._bit_idx += 1
                        if self._bit_idx == 8:
                            self.rx_bytes.append(self._shift_in)
                            self._load_next_tx_byte()
                            self._bit_idx = 0

                self._prev_sclk = sclk

            self._prev_cs_active = cs_active

    def _load_next_tx_byte(self):
        self._shift_out = self.tx_queue.popleft() if self.tx_queue else 0x00
