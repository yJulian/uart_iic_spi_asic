// 8N1 UART transmitter. Transmission of the next byte begins on the
// tick_x1 boundary following the `start` pulse (bounded latency of at
// most one bit period), then shifts out start/data[0..7]/stop.
module uart_tx #(
    parameter integer CLK_FREQ_HZ = 11_059_200,
    parameter integer BAUD        = 115_200
) (
    input  wire clk,
    input  wire rst_n,

    input  wire [7:0] data,
    input  wire        start,
    output reg          txd,
    output reg          busy
);

    wire tick_x1;
    baud_gen #(
        .CLK_FREQ_HZ(CLK_FREQ_HZ),
        .BAUD       (BAUD)
    ) u_baud (
        .clk     (clk),
        .rst_n   (rst_n),
        .tick_x16(),
        .tick_x1 (tick_x1)
    );

    reg [9:0] frame;      // {stop, data[7:0], start}, shifted out LSB-first
    reg [3:0] bits_left;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            txd       <= 1'b1;
            busy      <= 1'b0;
            frame     <= 10'h3FF;
            bits_left <= 4'd0;
        end else if (!busy) begin
            txd <= 1'b1;
            if (start) begin
                busy      <= 1'b1;
                frame     <= {1'b1, data, 1'b0};
                bits_left <= 4'd10;
            end
        end else if (tick_x1) begin
            txd   <= frame[0];
            frame <= {1'b1, frame[9:1]};
            if (bits_left == 4'd1) busy <= 1'b0;
            bits_left <= bits_left - 4'd1;
        end
    end

endmodule
