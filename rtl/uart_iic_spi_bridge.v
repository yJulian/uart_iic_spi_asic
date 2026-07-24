// Top-level: UART-controlled I2C/SPI bridge (native/standalone pinout).
//
// A host sends framed commands over `uart_rx`/`uart_tx` (see
// docs/PROTOCOL.md); cmd_engine parses them and drives either the I2C
// or the SPI master accordingly.
//
// I2C SCL/SDA are exposed as split out/oe/in triplets rather than
// `inout`, since real pad tri-state control belongs outside this
// digital core (see docs/architecture.md).
module uart_iic_spi_bridge #(
    parameter integer CLK_FREQ_HZ = 11_059_200,
    parameter integer BAUD        = 115_200,
    parameter integer MAX_PAYLOAD = 16,
    parameter integer NUM_SPI_CS  = 2
) (
    input  wire clk,
    input  wire rst_n,

    input  wire uart_rx,
    output wire uart_tx,

    output wire                spi_sclk,
    output wire                spi_mosi,
    input  wire                spi_miso,
    output wire [NUM_SPI_CS-1:0] spi_cs_n,

    output wire i2c_scl_out,
    output wire i2c_scl_oe,
    input  wire i2c_scl_in,
    output wire i2c_sda_out,
    output wire i2c_sda_oe,
    input  wire i2c_sda_in,

    output wire busy,
    output wire error
);

    wire [7:0] urx_data;
    wire        urx_valid;

    wire [7:0] utx_data;
    wire        utx_start;
    wire        utx_busy;

    uart_rx #(
        .CLK_FREQ_HZ(CLK_FREQ_HZ),
        .BAUD       (BAUD)
    ) u_uart_rx (
        .clk        (clk),
        .rst_n      (rst_n),
        .rxd        (uart_rx),
        .data       (urx_data),
        .valid      (urx_valid),
        .frame_error()
    );

    uart_tx #(
        .CLK_FREQ_HZ(CLK_FREQ_HZ),
        .BAUD       (BAUD)
    ) u_uart_tx (
        .clk  (clk),
        .rst_n(rst_n),
        .data (utx_data),
        .start(utx_start),
        .txd  (uart_tx),
        .busy (utx_busy)
    );

    wire        i2c_cmd_start, i2c_cmd_stop, i2c_cmd_wr, i2c_cmd_rd;
    wire [7:0] i2c_wr_data;
    wire        i2c_rd_ack_en;
    wire [7:0] i2c_rd_data;
    wire        i2c_done, i2c_ack_error;
    wire [15:0] i2c_clk_div;

    i2c_master u_i2c_master (
        .clk      (clk),
        .rst_n    (rst_n),
        .clk_div  (i2c_clk_div),
        .cmd_start(i2c_cmd_start),
        .cmd_stop (i2c_cmd_stop),
        .cmd_wr   (i2c_cmd_wr),
        .cmd_rd   (i2c_cmd_rd),
        .wr_data  (i2c_wr_data),
        .rd_ack_en(i2c_rd_ack_en),
        .rd_data  (i2c_rd_data),
        .busy     (),
        .done     (i2c_done),
        .ack_error(i2c_ack_error),
        .scl_out  (i2c_scl_out),
        .scl_oe   (i2c_scl_oe),
        .scl_in   (i2c_scl_in),
        .sda_out  (i2c_sda_out),
        .sda_oe   (i2c_sda_oe),
        .sda_in   (i2c_sda_in)
    );

    wire                          spi_start_w;
    wire [$clog2(NUM_SPI_CS)-1:0] spi_cs_sel_w;
    wire                          spi_hold_cs_w;
    wire [7:0]                    spi_tx_data_w;
    wire [7:0]                    spi_rx_data_w;
    wire                          spi_done_w;
    wire [15:0]                   spi_clk_div_w;
    wire [1:0]                    spi_mode_w;
    wire [NUM_SPI_CS-1:0]         spi_cs_polarity_w;

    spi_master #(
        .NUM_CS(NUM_SPI_CS)
    ) u_spi_master (
        .clk           (clk),
        .rst_n         (rst_n),
        .clk_div       (spi_clk_div_w),
        .mode          (spi_mode_w),
        .cs_active_high(spi_cs_polarity_w),
        .start         (spi_start_w),
        .cs_sel        (spi_cs_sel_w),
        .hold_cs       (spi_hold_cs_w),
        .tx_data       (spi_tx_data_w),
        .rx_data       (spi_rx_data_w),
        .busy          (),
        .done          (spi_done_w),
        .sclk          (spi_sclk),
        .mosi          (spi_mosi),
        .miso          (spi_miso),
        .cs_n          (spi_cs_n)
    );

    cmd_engine #(
        .MAX_PAYLOAD(MAX_PAYLOAD),
        .NUM_SPI_CS (NUM_SPI_CS)
    ) u_cmd_engine (
        .clk  (clk),
        .rst_n(rst_n),

        .urx_data (urx_data),
        .urx_valid(urx_valid),

        .utx_data (utx_data),
        .utx_start(utx_start),
        .utx_busy (utx_busy),

        .i2c_cmd_start(i2c_cmd_start),
        .i2c_cmd_stop (i2c_cmd_stop),
        .i2c_cmd_wr   (i2c_cmd_wr),
        .i2c_cmd_rd   (i2c_cmd_rd),
        .i2c_wr_data  (i2c_wr_data),
        .i2c_rd_ack_en(i2c_rd_ack_en),
        .i2c_rd_data  (i2c_rd_data),
        .i2c_done     (i2c_done),
        .i2c_ack_error(i2c_ack_error),
        .i2c_clk_div  (i2c_clk_div),

        .i2c_scl_in(i2c_scl_in),
        .i2c_sda_in(i2c_sda_in),

        .spi_start      (spi_start_w),
        .spi_cs_sel     (spi_cs_sel_w),
        .spi_hold_cs    (spi_hold_cs_w),
        .spi_tx_data    (spi_tx_data_w),
        .spi_rx_data    (spi_rx_data_w),
        .spi_done       (spi_done_w),
        .spi_clk_div    (spi_clk_div_w),
        .spi_mode       (spi_mode_w),
        .spi_cs_polarity(spi_cs_polarity_w),

        .busy (busy),
        .error(error)
    );

endmodule
