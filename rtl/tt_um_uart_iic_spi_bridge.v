// TinyTapeout wrapper around uart_iic_spi_bridge. Pure signal mapping,
// no added logic - every core port is wired straight to a real
// top-level pin so nothing is left dangling for synthesis to prune.
//
// Pinout (see README.md for the full table):
//   ui_in[0]  = uart_rx        ui_in[1]  = spi_miso   ui_in[7:2] = reserved
//   uo_out[0] = uart_tx        uo_out[1] = spi_sclk   uo_out[2] = spi_mosi
//   uo_out[3] = spi_cs_n[0]    uo_out[4] = spi_cs_n[1]
//   uo_out[5] = busy           uo_out[6] = error       uo_out[7] = heartbeat
//   uio[0]    = i2c_scl (open-drain via uio_out/uio_oe/uio_in)
//   uio[1]    = i2c_sda (open-drain via uio_out/uio_oe/uio_in)
//   uio[7:2]  = unused (uio_oe held low)
module tt_um_uart_iic_spi_bridge (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire        ena,
    input  wire        clk,
    input  wire        rst_n
);

    wire        core_busy, core_error;
    wire [1:0] spi_cs_n;

    wire i2c_scl_out, i2c_scl_oe;
    wire i2c_sda_out, i2c_sda_oe;

    // Free-running heartbeat so a scope on uo_out[7] can confirm the
    // clock is alive even with no UART traffic.
    reg [23:0] heartbeat_cnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) heartbeat_cnt <= 24'd0;
        else        heartbeat_cnt <= heartbeat_cnt + 24'd1;
    end

    uart_iic_spi_bridge u_core (
        .clk   (clk),
        .rst_n (rst_n & ena),

        .uart_rx(ui_in[0]),
        .uart_tx(uo_out[0]),

        .spi_sclk(uo_out[1]),
        .spi_mosi(uo_out[2]),
        .spi_miso(ui_in[1]),
        .spi_cs_n(spi_cs_n),

        .i2c_scl_out(i2c_scl_out),
        .i2c_scl_oe (i2c_scl_oe),
        .i2c_scl_in (uio_in[0]),
        .i2c_sda_out(i2c_sda_out),
        .i2c_sda_oe (i2c_sda_oe),
        .i2c_sda_in (uio_in[1]),

        .busy (core_busy),
        .error(core_error)
    );

    assign uo_out[3]   = spi_cs_n[0];
    assign uo_out[4]   = spi_cs_n[1];
    assign uo_out[5]   = core_busy;
    assign uo_out[6]   = core_error;
    assign uo_out[7]   = heartbeat_cnt[23];

    assign uio_out[0]  = i2c_scl_out;
    assign uio_out[1]  = i2c_sda_out;
    assign uio_out[7:2] = 6'b0;

    assign uio_oe[0]   = i2c_scl_oe;
    assign uio_oe[1]   = i2c_sda_oe;
    assign uio_oe[7:2] = 6'b0;

    // ui_in[7:2] reserved, intentionally unused.
    wire _unused = &{ui_in[7:2], 1'b0};

endmodule
