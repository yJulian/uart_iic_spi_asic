// Full-duplex SPI controller. Supports all 4 (CPOL,CPHA) modes and up
// to NUM_CS independently-polarized chip selects. `hold_cs` keeps the
// currently-selected CS asserted across consecutive byte transfers so a
// multi-byte burst appears as a single SPI transaction on the bus.
module spi_master #(
    parameter integer NUM_CS = 2
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [15:0] clk_div,          // SCLK half-period, in clk cycles (0 treated as 1)
    input  wire  [1:0] mode,             // {CPOL, CPHA}
    input  wire [NUM_CS-1:0] cs_active_high,

    input  wire        start,            // pulse: begin one byte transfer
    input  wire [$clog2(NUM_CS)-1:0] cs_sel,
    input  wire        hold_cs,          // keep CS asserted after this byte
    input  wire  [7:0] tx_data,

    output reg   [7:0] rx_data,
    output reg          busy,
    output reg          done,            // one-cycle pulse

    output reg           sclk,
    output reg           mosi,
    input  wire          miso,
    output reg [NUM_CS-1:0] cs_n
);

    wire cpol = mode[1];
    wire cpha = mode[0];

    localparam [2:0]
        S_IDLE     = 3'd0,
        S_SETUP    = 3'd1,
        S_XFER     = 3'd2,
        S_TEARDOWN = 3'd3,
        S_DONE     = 3'd4;

    reg [2:0] state;
    reg [4:0] half_idx;   // 0..15: 16 half-periods = 8 bits
    reg [7:0] tx_shift;
    reg       cs_holding;
    reg [$clog2(NUM_CS)-1:0] active_cs;
    reg       release_cs;

    wire [3:0] bit_no = half_idx[4:1];
    wire       edge_b = half_idx[0];

    wire [15:0] div_max = (clk_div == 16'd0) ? 16'd1 : clk_div;
    reg  [15:0] div_cnt;
    wire        tick = (div_cnt >= div_max - 16'd1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_cnt <= 16'd0;
        end else if (state == S_IDLE || state == S_DONE) begin
            div_cnt <= 16'd0;
        end else if (tick) begin
            div_cnt <= 16'd0;
        end else begin
            div_cnt <= div_cnt + 16'd1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            busy       <= 1'b0;
            done       <= 1'b0;
            sclk       <= 1'b0;
            mosi       <= 1'b0;
            cs_n       <= {NUM_CS{1'b1}};
            cs_holding <= 1'b0;
            half_idx   <= 5'd0;
            tx_shift   <= 8'd0;
            rx_data    <= 8'd0;
            active_cs  <= {$clog2(NUM_CS){1'b0}};
            release_cs <= 1'b0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (start) begin
                        busy       <= 1'b1;
                        active_cs  <= cs_sel;
                        tx_shift   <= tx_data;
                        sclk       <= cpol;
                        half_idx   <= 5'd0;
                        release_cs <= ~hold_cs;
                        if (!cs_holding) cs_n[cs_sel] <= cs_active_high[cs_sel];
                        cs_holding <= 1'b1;
                        state      <= S_SETUP;
                    end
                end

                // CS-to-clock setup time; for CPHA=0 the first bit must
                // already be valid on MOSI before the first clock edge.
                S_SETUP: begin
                    if (!cpha) mosi <= tx_shift[7];
                    if (tick) state <= S_XFER;
                end

                S_XFER: if (tick) begin
                    sclk <= ~sclk;
                    if (!edge_b) begin
                        // edge A: idle level -> active level
                        if (!cpha) rx_data <= {rx_data[6:0], miso};
                        else       mosi    <= tx_shift[7 - bit_no];
                    end else begin
                        // edge B: active level -> idle level
                        if (!cpha) begin
                            if (bit_no != 4'd7) mosi <= tx_shift[6 - bit_no];
                        end else begin
                            rx_data <= {rx_data[6:0], miso};
                        end
                    end
                    if (half_idx == 5'd15) state <= S_TEARDOWN;
                    else half_idx <= half_idx + 5'd1;
                end

                S_TEARDOWN: begin
                    if (release_cs) begin
                        cs_n[active_cs] <= ~cs_active_high[active_cs];
                        cs_holding      <= 1'b0;
                    end
                    state <= S_DONE;
                end

                S_DONE: begin
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
