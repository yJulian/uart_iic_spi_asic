"""cocotb tests for uart_rx and uart_tx (rtl/uart_rx.v, rtl/uart_tx.v)."""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

CLK_FREQ_HZ = 1_600_000
BAUD = 100_000
BIT_CYCLES = CLK_FREQ_HZ // BAUD  # 16 clk cycles per UART bit at these sim params


_clock_task = None


async def start_clock(dut):
    # cocotb runs every @cocotb.test() in this module within the same
    # simulation session. Kill any clock driver left over from a previous
    # test before starting a fresh one, so exactly one is ever driving
    # dut.clk (whether or not cocotb already tore the old one down).
    global _clock_task
    if _clock_task is not None:
        _clock_task.cancel()
    _clock_task = cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


async def send_byte_bits(dut, rxd_signal, byte_val, bad_stop_bit=False):
    """Bit-bang one UART byte onto rxd_signal at the simulated baud rate."""
    rxd_signal.value = 0  # start bit
    await ClockCycles(dut.clk, BIT_CYCLES)
    for i in range(8):
        rxd_signal.value = (byte_val >> i) & 1
        await ClockCycles(dut.clk, BIT_CYCLES)
    rxd_signal.value = 0 if bad_stop_bit else 1
    await ClockCycles(dut.clk, BIT_CYCLES)


@cocotb.test()
async def test_uart_rx_basic(dut):
    """uart_rx correctly decodes a sequence of random bytes."""
    dut.rxd.value = 1
    await start_clock(dut)

    test_bytes = [0x00, 0xFF, 0xA5, 0x5A] + [random.randint(0, 255) for _ in range(8)]

    for b in test_bytes:
        cocotb.start_soon(send_byte_bits(dut, dut.rxd, b))
        await RisingEdge(dut.valid)
        got = int(dut.data.value)
        assert got == b, f"expected 0x{b:02X}, got 0x{got:02X}"
        await ClockCycles(dut.clk, BIT_CYCLES)  # settle before next byte


@cocotb.test()
async def test_uart_rx_frame_error(dut):
    """A missing stop bit raises frame_error and does not raise valid."""
    dut.rxd.value = 1
    await start_clock(dut)

    cocotb.start_soon(send_byte_bits(dut, dut.rxd, 0x3C, bad_stop_bit=True))
    await RisingEdge(dut.frame_error)
    assert dut.valid.value == 0


@cocotb.test()
async def test_uart_tx_basic(dut):
    """uart_tx shifts out start/8-data(LSB first)/stop for each byte."""
    dut.start.value = 0
    dut.data.value = 0
    await start_clock(dut)

    test_bytes = [0x00, 0xFF, 0x81, 0x3C] + [random.randint(0, 255) for _ in range(8)]

    for b in test_bytes:
        dut.data.value = b
        dut.start.value = 1
        await ClockCycles(dut.clk, 1)
        dut.start.value = 0

        # txd begins transmission on the next baud tick boundary, which is
        # a bounded but variable delay after `start` (see uart_tx.v) -
        # wait for the actual falling edge rather than assuming fixed timing.
        await FallingEdge(dut.txd)
        assert dut.busy.value == 1

        # We're now at the start of the start bit; sample the middle of
        # each subsequent bit period.
        await ClockCycles(dut.clk, BIT_CYCLES // 2)
        assert dut.txd.value == 0, "start bit not seen"

        got = 0
        for i in range(8):
            await ClockCycles(dut.clk, BIT_CYCLES)
            got |= (int(dut.txd.value) & 1) << i
        await ClockCycles(dut.clk, BIT_CYCLES)
        assert dut.txd.value == 1, "stop bit not seen"
        assert got == b, f"expected 0x{b:02X}, got 0x{got:02X}"

        await ClockCycles(dut.clk, 2)
        assert dut.busy.value == 0


def _build_and_run(hdl_toplevel, test_module, testcase):
    from pathlib import Path

    from cocotb_tools.runner import get_runner

    rtl_dir = Path(__file__).resolve().parents[2] / "rtl"
    runner = get_runner("icarus")
    runner.build(
        verilog_sources=[rtl_dir / "baud_gen.v", rtl_dir / f"{hdl_toplevel}.v"],
        hdl_toplevel=hdl_toplevel,
        parameters={"CLK_FREQ_HZ": CLK_FREQ_HZ, "BAUD": BAUD},
        build_dir=Path(__file__).resolve().parent / "sim_build" / hdl_toplevel,
        always=True,
        timescale=("1ns", "1ps"),
    )
    runner.test(hdl_toplevel=hdl_toplevel, test_module=test_module, testcase=testcase)


def test_uart_rx_runner():
    _build_and_run(
        "uart_rx", "test_uart", ["test_uart_rx_basic", "test_uart_rx_frame_error"]
    )


def test_uart_tx_runner():
    _build_and_run("uart_tx", "test_uart", ["test_uart_tx_basic"])


if __name__ == "__main__":
    test_uart_rx_runner()
    test_uart_tx_runner()
