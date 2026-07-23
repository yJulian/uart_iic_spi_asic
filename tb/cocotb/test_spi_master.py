"""cocotb tests for rtl/spi_master.v against a hand-rolled SPI slave model."""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from spi_slave_model import SpiSlaveModel

CLK_DIV = 4
NUM_CS = 2


_clock_task = None


async def reset(dut):
    # cocotb runs every @cocotb.test() in this module within the same
    # simulation session. Kill any clock driver left over from a previous
    # test before starting a fresh one, so exactly one is ever driving
    # dut.clk (whether or not cocotb already tore the old one down).
    global _clock_task
    if _clock_task is not None:
        _clock_task.cancel()
    _clock_task = cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.clk_div.value = CLK_DIV
    dut.mode.value = 0
    dut.cs_active_high.value = 0
    dut.start.value = 0
    dut.cs_sel.value = 0
    dut.hold_cs.value = 0
    dut.tx_data.value = 0
    dut.miso.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


async def xfer_byte(dut, data, hold_cs=False, cs_sel=0):
    dut.tx_data.value = data
    dut.hold_cs.value = int(hold_cs)
    dut.cs_sel.value = cs_sel
    dut.start.value = 1
    await ClockCycles(dut.clk, 1)
    dut.start.value = 0
    await RisingEdge(dut.done)
    return int(dut.rx_data.value)


async def run_mode(dut, cpol, cpha):
    await reset(dut)
    dut.mode.value = (cpol << 1) | cpha

    tx_bytes = [0x5A, 0xC3, 0x00, 0xFF]
    slave = SpiSlaveModel(dut, cpol=cpol, cpha=cpha, tx_bytes=[0xA5, 0x3C, 0xFF, 0x00])
    slave.start()

    got = []
    for i, b in enumerate(tx_bytes):
        r = await xfer_byte(dut, b, hold_cs=(i != len(tx_bytes) - 1))
        got.append(r)

    await ClockCycles(dut.clk, 4)

    assert dut.cs_n.value.to_unsigned() & 1 == 1, "CS not deasserted after last byte"
    assert slave.rx_bytes == tx_bytes, f"mode({cpol},{cpha}): slave saw {slave.rx_bytes}"
    assert got == [0xA5, 0x3C, 0xFF, 0x00], f"mode({cpol},{cpha}): master got {got}"


@cocotb.test()
async def test_spi_mode0(dut):
    await run_mode(dut, cpol=0, cpha=0)


@cocotb.test()
async def test_spi_mode1(dut):
    await run_mode(dut, cpol=0, cpha=1)


@cocotb.test()
async def test_spi_mode2(dut):
    await run_mode(dut, cpol=1, cpha=0)


@cocotb.test()
async def test_spi_mode3(dut):
    await run_mode(dut, cpol=1, cpha=1)


@cocotb.test()
async def test_spi_cs_active_high(dut):
    """cs_active_high[0]=1 makes CS idle-low, asserted-high."""
    await reset(dut)
    dut.cs_active_high.value = 1

    slave = SpiSlaveModel(dut, cpol=0, cpha=0, tx_bytes=[0x11], cs_active_level=1)
    slave.start()
    r = await xfer_byte(dut, 0x22, hold_cs=False)
    assert r == 0x11
    assert dut.cs_n.value.to_unsigned() & 1 == 0, "CS should return to idle-low"
    assert slave.rx_bytes == [0x22]


def test_spi_master_runner():
    from pathlib import Path

    from cocotb_tools.runner import get_runner

    rtl_dir = Path(__file__).resolve().parents[2] / "rtl"
    runner = get_runner("icarus")
    runner.build(
        verilog_sources=[rtl_dir / "spi_master.v"],
        hdl_toplevel="spi_master",
        parameters={"NUM_CS": NUM_CS},
        build_dir=Path(__file__).resolve().parent / "sim_build" / "spi_master",
        always=True,
        timescale=("1ns", "1ps"),
    )
    runner.test(hdl_toplevel="spi_master", test_module="test_spi_master")


if __name__ == "__main__":
    test_spi_master_runner()
