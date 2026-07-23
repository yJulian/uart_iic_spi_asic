"""cocotb tests for rtl/i2c_master.v against a hand-rolled I2C slave model."""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

from i2c_slave_model import I2cSlaveModel

CLK_DIV = 4  # quarter-bit period, in clk cycles - small for fast simulation


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
    dut.cmd_start.value = 0
    dut.cmd_stop.value = 0
    dut.cmd_wr.value = 0
    dut.cmd_rd.value = 0
    dut.wr_data.value = 0
    dut.rd_ack_en.value = 0
    dut.scl_in.value = 1
    dut.sda_in.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


async def pulse(dut, signal, data_signal=None, data=None, ack_en_signal=None, ack_en=None):
    if data_signal is not None:
        data_signal.value = data
    if ack_en_signal is not None:
        ack_en_signal.value = ack_en
    signal.value = 1
    await ClockCycles(dut.clk, 1)
    signal.value = 0
    await RisingEdge(dut.done)


@cocotb.test()
async def test_i2c_write_ack(dut):
    """Master writes address + N data bytes; slave ACKs everything."""
    await reset(dut)
    slave = I2cSlaveModel(dut, address=0x50)
    slave.start()

    await pulse(dut, dut.cmd_start)
    await pulse(dut, dut.cmd_wr, dut.wr_data, (0x50 << 1) | 0)
    assert dut.ack_error.value == 0

    payload = [0x11, 0x22, 0x33]
    for b in payload:
        await pulse(dut, dut.cmd_wr, dut.wr_data, b)
        assert dut.ack_error.value == 0

    await pulse(dut, dut.cmd_stop)
    await ClockCycles(dut.clk, 10)

    assert slave.written_bytes == payload


@cocotb.test()
async def test_i2c_write_address_nack(dut):
    """No slave at the given address -> ack_error after the address byte."""
    await reset(dut)
    slave = I2cSlaveModel(dut, address=0x50, ack_address=False)
    slave.start()

    await pulse(dut, dut.cmd_start)
    await pulse(dut, dut.cmd_wr, dut.wr_data, (0x50 << 1) | 0)
    assert dut.ack_error.value == 1

    await pulse(dut, dut.cmd_stop)


@cocotb.test()
async def test_i2c_read(dut):
    """Master reads 3 bytes, ACKing the first two and NACKing the last."""
    await reset(dut)
    preload = [0xAA, 0xBB, 0xCC]
    slave = I2cSlaveModel(dut, address=0x50, read_bytes=list(preload))
    slave.start()

    await pulse(dut, dut.cmd_start)
    await pulse(dut, dut.cmd_wr, dut.wr_data, (0x50 << 1) | 1)
    assert dut.ack_error.value == 0

    got = []
    for i in range(len(preload)):
        more = i != len(preload) - 1
        await pulse(dut, dut.cmd_rd, ack_en_signal=dut.rd_ack_en, ack_en=int(more))
        got.append(int(dut.rd_data.value))

    await pulse(dut, dut.cmd_stop)

    assert got == preload
    assert slave.nacked is True


@cocotb.test()
async def test_i2c_write_read_repeated_start(dut):
    """Write 2 bytes, repeated START, then read 2 bytes - one transaction."""
    await reset(dut)
    preload = [0x01, 0x02]
    slave = I2cSlaveModel(dut, address=0x50, read_bytes=list(preload))
    slave.start()

    await pulse(dut, dut.cmd_start)
    await pulse(dut, dut.cmd_wr, dut.wr_data, (0x50 << 1) | 0)
    for b in (0x10, 0x20):
        await pulse(dut, dut.cmd_wr, dut.wr_data, b)
        assert dut.ack_error.value == 0

    # repeated START
    await pulse(dut, dut.cmd_start)
    await pulse(dut, dut.cmd_wr, dut.wr_data, (0x50 << 1) | 1)
    assert dut.ack_error.value == 0

    got = []
    for i in range(len(preload)):
        more = i != len(preload) - 1
        await pulse(dut, dut.cmd_rd, ack_en_signal=dut.rd_ack_en, ack_en=int(more))
        got.append(int(dut.rd_data.value))

    await pulse(dut, dut.cmd_stop)

    assert slave.written_bytes == [0x10, 0x20]
    assert got == preload


def test_i2c_master_runner():
    from pathlib import Path

    from cocotb_tools.runner import get_runner

    rtl_dir = Path(__file__).resolve().parents[2] / "rtl"
    runner = get_runner("icarus")
    runner.build(
        verilog_sources=[rtl_dir / "i2c_master.v"],
        hdl_toplevel="i2c_master",
        build_dir=Path(__file__).resolve().parent / "sim_build" / "i2c_master",
        always=True,
        timescale=("1ns", "1ps"),
    )
    runner.test(hdl_toplevel="i2c_master", test_module="test_i2c_master")


if __name__ == "__main__":
    test_i2c_master_runner()
