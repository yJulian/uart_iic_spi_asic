# Yosys -> nextpnr-himbaechel -> gowin_pack flow for the Tang Nano 9K
# (GW1NR-LV9QN88PC6/I5) target. Requires oss-cad-suite's bin/ on PATH.
#
# Invoked via the root Makefile's fpga-* targets; can also be run directly
# from this directory with `make`.

TOP        := tangnano9k_top
DEVICE     := GW1NR-LV9QN88PC6/I5
FAMILY     := GW1N-9C
CST        := tangnano9k.cst
BUILD      := build

RTL := \
	../../rtl/baud_gen.v \
	../../rtl/uart_rx.v \
	../../rtl/uart_tx.v \
	../../rtl/i2c_master.v \
	../../rtl/spi_master.v \
	../../rtl/cmd_engine.v \
	../../rtl/uart_iic_spi_bridge.v \
	../../rtl/tangnano9k_top.v

.PHONY: all synth pnr pack clean

all: pack

$(BUILD):
	mkdir -p $(BUILD)

synth: $(BUILD)
	yosys -p "read_verilog -sv $(RTL); synth_gowin -top $(TOP) -json $(BUILD)/$(TOP).json"

pnr: synth
	nextpnr-himbaechel \
		--json $(BUILD)/$(TOP).json \
		--write $(BUILD)/$(TOP)_pnr.json \
		--device "$(DEVICE)" \
		--vopt family=$(FAMILY) \
		--vopt cst=$(CST)

pack: pnr
	gowin_pack -d $(FAMILY) -o $(BUILD)/$(TOP).fs $(BUILD)/$(TOP)_pnr.json

clean:
	rm -rf $(BUILD)
