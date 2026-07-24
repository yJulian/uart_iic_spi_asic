"""Integration test: drives uart_iic_spi_bridge (the full top-level) with
real UART byte streams and checks the protocol responses, with an I2C
slave model and an SPI slave model attached to the respective buses.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Edge, FallingEdge

from i2c_slave_model import I2cSlaveModel
from spi_slave_model import SpiSlaveModel

CLK_FREQ_HZ = 1_600_000
BAUD = 100_000
BIT_CYCLES = CLK_FREQ_HZ // BAUD

# ---- protocol constants (see docs/PROTOCOL.md) ----
OPC_PING = 0x01
OPC_GET_ID = 0x02
OPC_I2C_WRITE = 0x10
OPC_I2C_READ = 0x11
OPC_I2C_WRITE_READ = 0x12
OPC_SPI_XFER = 0x20
OPC_SET_CONFIG = 0x30
OPC_GET_STATUS = 0x31
OPC_SELF_TEST = 0x40

SELF_TEST_PATTERN = 0x5A
SELF_TEST_I2C_BUS_IDLE = 0x01
SELF_TEST_SPI_LOOPBACK = 0x02

STAT_ACK = 0x06
STAT_NACK = 0x15
STAT_ERR_CHECKSUM = 0x16
STAT_ERR_LEN = 0x17
STAT_ERR_OPCODE = 0x18

PARAM_I2C_CLKDIV = 0x01
PARAM_SPI_CLKDIV = 0x02
PARAM_SPI_MODE = 0x03
PARAM_CS_POLARITY = 0x04


def checksum(bs):
    c = 0
    for b in bs:
        c ^= b
    return c


def build_frame(opcode, payload=b"", bad_checksum=False):
    body = bytes([opcode, len(payload)]) + bytes(payload)
    cksum = checksum(body)
    if bad_checksum:
        cksum ^= 0xFF
    return body + bytes([cksum])


async def uart_send_byte(dut, byte_val):
    dut.uart_rx.value = 0
    await ClockCycles(dut.clk, BIT_CYCLES)
    for i in range(8):
        dut.uart_rx.value = (byte_val >> i) & 1
        await ClockCycles(dut.clk, BIT_CYCLES)
    dut.uart_rx.value = 1
    await ClockCycles(dut.clk, BIT_CYCLES)


async def uart_send_frame(dut, opcode, payload=b"", bad_checksum=False):
    for b in build_frame(opcode, payload, bad_checksum):
        await uart_send_byte(dut, b)


async def uart_recv_byte(dut):
    await FallingEdge(dut.uart_tx)
    await ClockCycles(dut.clk, BIT_CYCLES // 2)
    assert dut.uart_tx.value == 0
    got = 0
    for i in range(8):
        await ClockCycles(dut.clk, BIT_CYCLES)
        got |= (int(dut.uart_tx.value) & 1) << i
    await ClockCycles(dut.clk, BIT_CYCLES)
    assert dut.uart_tx.value == 1, "missing stop bit"
    return got


async def uart_recv_response(dut):
    status = await uart_recv_byte(dut)
    rlen = await uart_recv_byte(dut)
    data = bytearray()
    for _ in range(rlen):
        data.append(await uart_recv_byte(dut))
    cksum = await uart_recv_byte(dut)
    assert cksum == checksum(bytes([status, rlen]) + bytes(data)), "bad response checksum"
    return status, bytes(data)


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
    dut.uart_rx.value = 1
    dut.spi_miso.value = 1
    dut.i2c_scl_in.value = 1
    dut.i2c_sda_in.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


@cocotb.test()
async def test_ping_and_get_id(dut):
    await start_clock(dut)

    await uart_send_frame(dut, OPC_PING)
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK and data == b""

    await uart_send_frame(dut, OPC_GET_ID)
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK
    assert data == bytes([0x01, 0xA5])


@cocotb.test()
async def test_bad_checksum(dut):
    await start_clock(dut)
    await uart_send_frame(dut, OPC_PING, bad_checksum=True)
    status, data = await uart_recv_response(dut)
    assert status == STAT_ERR_CHECKSUM and data == b""


@cocotb.test()
async def test_unknown_opcode(dut):
    await start_clock(dut)
    await uart_send_frame(dut, 0x7F)
    status, data = await uart_recv_response(dut)
    assert status == STAT_ERR_OPCODE and data == b""


@cocotb.test()
async def test_get_status_reflects_previous(dut):
    await start_clock(dut)
    await uart_send_frame(dut, OPC_PING, bad_checksum=True)
    status, _ = await uart_recv_response(dut)
    assert status == STAT_ERR_CHECKSUM

    await uart_send_frame(dut, OPC_GET_STATUS)
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK
    assert data == bytes([STAT_ERR_CHECKSUM])


@cocotb.test()
async def test_len_error(dut):
    await start_clock(dut)
    # I2C_READ requires exactly 2 payload bytes.
    await uart_send_frame(dut, OPC_I2C_READ, bytes([0x50]))
    status, data = await uart_recv_response(dut)
    assert status == STAT_ERR_LEN and data == b""


@cocotb.test()
async def test_i2c_write_then_read(dut):
    await start_clock(dut)

    await uart_send_frame(
        dut, OPC_SET_CONFIG, bytes([PARAM_I2C_CLKDIV, 0x00, 0x04])
    )
    status, _ = await uart_recv_response(dut)
    assert status == STAT_ACK

    slave = I2cSlaveModel(dut.u_i2c_master, address=0x50, read_bytes=[0xDE, 0xAD])
    slave.start()

    await uart_send_frame(dut, OPC_I2C_WRITE, bytes([0x50, 0x11, 0x22]))
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK and data == b""
    assert slave.written_bytes == [0x11, 0x22]

    await uart_send_frame(dut, OPC_I2C_READ, bytes([0x50, 0x02]))
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK
    assert data == bytes([0xDE, 0xAD])


@cocotb.test()
async def test_i2c_address_nack(dut):
    await start_clock(dut)
    await uart_send_frame(
        dut, OPC_SET_CONFIG, bytes([PARAM_I2C_CLKDIV, 0x00, 0x04])
    )
    await uart_recv_response(dut)

    slave = I2cSlaveModel(dut.u_i2c_master, address=0x50, ack_address=False)
    slave.start()

    await uart_send_frame(dut, OPC_I2C_WRITE, bytes([0x50, 0xFF]))
    status, data = await uart_recv_response(dut)
    assert status == STAT_NACK and data == b""


@cocotb.test()
async def test_i2c_write_read_repeated_start(dut):
    await start_clock(dut)
    await uart_send_frame(
        dut, OPC_SET_CONFIG, bytes([PARAM_I2C_CLKDIV, 0x00, 0x04])
    )
    await uart_recv_response(dut)

    slave = I2cSlaveModel(dut.u_i2c_master, address=0x60, read_bytes=[0x01, 0x02, 0x03])
    slave.start()

    payload = bytes([0x60, 0x02, 0xAA, 0xBB, 0x03])  # addr, nwrite, wdata.., nread
    await uart_send_frame(dut, OPC_I2C_WRITE_READ, payload)
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK
    assert data == bytes([0x01, 0x02, 0x03])
    assert slave.written_bytes == [0xAA, 0xBB]


@cocotb.test()
async def test_spi_xfer(dut):
    await start_clock(dut)

    await uart_send_frame(dut, OPC_SET_CONFIG, bytes([PARAM_SPI_CLKDIV, 0x00, 0x04]))
    await uart_recv_response(dut)
    await uart_send_frame(dut, OPC_SET_CONFIG, bytes([PARAM_SPI_MODE, 0x00]))
    await uart_recv_response(dut)

    slave = SpiSlaveModel(dut.u_spi_master, cpol=0, cpha=0, tx_bytes=[0x11, 0x22, 0x33])
    slave.start()

    await uart_send_frame(dut, OPC_SPI_XFER, bytes([0x00, 0xAA, 0xBB, 0xCC]))
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK
    assert data == bytes([0x11, 0x22, 0x33])
    assert slave.rx_bytes == [0xAA, 0xBB, 0xCC]


@cocotb.test()
async def test_self_test_bus_ok_no_loopback(dut):
    await start_clock(dut)
    # spi_miso is held at its default 1 (start_clock) - no loopback jumper.

    await uart_send_frame(dut, OPC_SELF_TEST)
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK
    assert data[0] & SELF_TEST_I2C_BUS_IDLE
    assert not (data[0] & SELF_TEST_SPI_LOOPBACK)
    assert data[1] == 0xFF  # miso held high throughout the probe


@cocotb.test()
async def test_self_test_stuck_bus_reports_nack(dut):
    await start_clock(dut)
    dut.i2c_sda_in.value = 0  # simulate a stuck-low SDA (missing pull-up)

    await uart_send_frame(dut, OPC_SELF_TEST)
    status, data = await uart_recv_response(dut)
    assert status == STAT_NACK
    assert not (data[0] & SELF_TEST_I2C_BUS_IDLE)


@cocotb.test()
async def test_self_test_spi_loopback_detected(dut):
    await start_clock(dut)

    async def mirror_mosi_to_miso():
        dut.spi_miso.value = dut.spi_mosi.value  # seed: Edge only fires on a change
        while True:
            await Edge(dut.spi_mosi)
            dut.spi_miso.value = dut.spi_mosi.value

    cocotb.start_soon(mirror_mosi_to_miso())

    await uart_send_frame(dut, OPC_SELF_TEST)
    status, data = await uart_recv_response(dut)
    assert status == STAT_ACK
    assert data[0] & SELF_TEST_SPI_LOOPBACK
    assert data[1] == SELF_TEST_PATTERN


def test_cmd_engine_runner():
    from pathlib import Path

    from cocotb_tools.runner import get_runner

    rtl_dir = Path(__file__).resolve().parents[2] / "rtl"
    runner = get_runner("icarus")
    runner.build(
        verilog_sources=[
            rtl_dir / "baud_gen.v",
            rtl_dir / "uart_rx.v",
            rtl_dir / "uart_tx.v",
            rtl_dir / "i2c_master.v",
            rtl_dir / "spi_master.v",
            rtl_dir / "cmd_engine.v",
            rtl_dir / "uart_iic_spi_bridge.v",
        ],
        hdl_toplevel="uart_iic_spi_bridge",
        parameters={"CLK_FREQ_HZ": CLK_FREQ_HZ, "BAUD": BAUD},
        build_dir=Path(__file__).resolve().parent / "sim_build" / "uart_iic_spi_bridge",
        always=True,
        timescale=("1ns", "1ps"),
    )
    runner.test(hdl_toplevel="uart_iic_spi_bridge", test_module="test_cmd_engine")


if __name__ == "__main__":
    test_cmd_engine_runner()
