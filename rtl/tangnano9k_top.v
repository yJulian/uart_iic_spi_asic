// Sipeed Tang Nano 9K (GW1NR-LV9QN88PC6/I5) top level. Pure pin mapping
// around uart_iic_spi_bridge, no added protocol logic - same philosophy as
// the TinyTapeout wrapper (rtl/tt_um_uart_iic_spi_bridge.v).
//
// Pinout (see README.md / docs/architecture.md for the full table and
// flow/tangnano9k/tangnano9k.cst for the physical constraints):
//   clk         = onboard 27 MHz oscillator (pin 52)
//   rst_n       = onboard button, active-low (pin 4)
//   uart_rx/tx  = onboard USB-UART bridge (pins 18/17)
//   spi_*       = header pins 25-29
//   i2c_scl/sda = header pins 30/19, real tri-state open-drain
//   led[5:0]    = onboard LEDs, active-low
module tangnano9k_top (
    input  wire clk,
    input  wire rst_n,

    input  wire uart_rx,
    output wire uart_tx,

    output wire       spi_sclk,
    output wire       spi_mosi,
    input  wire       spi_miso,
    output wire [1:0] spi_cs_n,

    inout  wire i2c_scl,
    inout  wire i2c_sda,

    output wire [5:0] led
);

    wire core_busy, core_error;

    wire i2c_scl_out, i2c_scl_oe;
    wire i2c_sda_out, i2c_sda_oe;

    // Open-drain emulation: the core only ever asserts oe to pull the line
    // low (see i2c_master.v), so releasing (oe=0) lets the pin float and
    // the pad's internal pull-up (see the .cst) bring it back high.
    assign i2c_scl = i2c_scl_oe ? 1'b0 : 1'bz;
    assign i2c_sda = i2c_sda_oe ? 1'b0 : 1'bz;

    // Free-running heartbeat so led[0] visibly blinks even with no UART
    // traffic, confirming the clock/reset are alive.
    reg [23:0] heartbeat_cnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) heartbeat_cnt <= 24'd0;
        else        heartbeat_cnt <= heartbeat_cnt + 24'd1;
    end

    uart_iic_spi_bridge #(
        .CLK_FREQ_HZ(27_000_000),
        .BAUD       (115_200)
    ) u_core (
        .clk  (clk),
        .rst_n(rst_n),

        .uart_rx(uart_rx),
        .uart_tx(uart_tx),

        .spi_sclk(spi_sclk),
        .spi_mosi(spi_mosi),
        .spi_miso(spi_miso),
        .spi_cs_n(spi_cs_n),

        .i2c_scl_out(i2c_scl_out),
        .i2c_scl_oe (i2c_scl_oe),
        .i2c_scl_in (i2c_scl),
        .i2c_sda_out(i2c_sda_out),
        .i2c_sda_oe (i2c_sda_oe),
        .i2c_sda_in (i2c_sda),

        .busy (core_busy),
        .error(core_error)
    );

    // LEDs are active-low (common-anode): drive low to light. led[5:3]
    // are unused - held explicitly off rather than left dangling.
    assign led[0] = ~heartbeat_cnt[23];
    assign led[1] = ~core_busy;
    assign led[2] = ~core_error;
    assign led[5:3] = 3'b111;

endmodule
