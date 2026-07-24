# Convenience targets for the UART-to-I2C/SPI bridge ASIC.
#
# Simulation targets need the cocotb venv (`make sim-setup` once, inside
# the LibreLane nix-shell). Flow targets need `librelane` on PATH (also
# provided by the LibreLane nix-shell) and PDK_ROOT set/available via ciel.

VENV       := .venv
PY         := $(VENV)/bin/python3
PDK_ROOT   ?= $(HOME)/.ciel

# FPGA (Tang Nano 9K) flow needs oss-cad-suite's bin/ on PATH (yosys,
# nextpnr-himbaechel, gowin_pack) and openFPGALoader for fpga-load/fpga-flash.
FPGA_FLOW  := flow/tangnano9k
FPGA_BOARD := tangnano9k
FPGA_BIT   := $(FPGA_FLOW)/build/tangnano9k_top.fs

.PHONY: sim-setup sim lint synth-check synth-check-standalone synth-check-tinytapeout flow-standalone flow-tinytapeout clean fpga-build fpga-load fpga-flash fpga-clean

sim-setup:
	scripts/setup_cocotb_venv.sh

sim: sim-setup
	$(PY) tb/cocotb/test_uart.py
	$(PY) tb/cocotb/test_i2c_master.py
	$(PY) tb/cocotb/test_spi_master.py
	$(PY) tb/cocotb/test_cmd_engine.py

lint:
	iverilog -g2012 -Wall -o /dev/null -s uart_iic_spi_bridge rtl/*.v
	iverilog -g2012 -Wall -o /dev/null -s tt_um_uart_iic_spi_bridge rtl/*.v
	iverilog -g2012 -Wall -o /dev/null -s tangnano9k_top rtl/*.v

synth-check: synth-check-standalone synth-check-tinytapeout

synth-check-standalone:
	PDK_ROOT=$(PDK_ROOT) librelane --pdk-root $(PDK_ROOT) --only Yosys.Synthesis flow/standalone/config.yaml

synth-check-tinytapeout:
	PDK_ROOT=$(PDK_ROOT) librelane --pdk-root $(PDK_ROOT) --only Yosys.Synthesis flow/tinytapeout/config.yaml

flow-standalone:
	PDK_ROOT=$(PDK_ROOT) librelane --pdk-root $(PDK_ROOT) flow/standalone/config.yaml

flow-tinytapeout:
	PDK_ROOT=$(PDK_ROOT) librelane --pdk-root $(PDK_ROOT) flow/tinytapeout/config.yaml

fpga-build:
	$(MAKE) -C $(FPGA_FLOW) pack

fpga-load: fpga-build
	openFPGALoader -b $(FPGA_BOARD) $(FPGA_BIT)

fpga-flash: fpga-build
	openFPGALoader -b $(FPGA_BOARD) -f $(FPGA_BIT)

fpga-clean:
	$(MAKE) -C $(FPGA_FLOW) clean

clean: fpga-clean
	rm -rf tb/cocotb/sim_build tb/cocotb/__pycache__ flow/standalone/runs flow/tinytapeout/runs
