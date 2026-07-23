// Baud rate tick generator shared by uart_rx and uart_tx.
//
// tick_x16 pulses at 16x the target baud rate (used by uart_rx for
// mid-bit oversampling), tick_x1 pulses once every 16 tick_x16 pulses,
// i.e. at the baud rate itself (used by uart_tx).
//
// Default CLK_FREQ_HZ/BAUD (11.0592 MHz / 115200) divides evenly, so the
// generated baud rate has zero ppm error. Other combinations are rounded
// down to the nearest integer divider and will have some baud error -
// keep it under the ~2-3% a UART receiver can tolerate.
module baud_gen #(
    parameter integer CLK_FREQ_HZ = 11_059_200,
    parameter integer BAUD        = 115_200
) (
    input  wire clk,
    input  wire rst_n,
    output reg  tick_x16,
    output reg  tick_x1
);

    localparam integer DIV_X16 = (CLK_FREQ_HZ / (BAUD * 16) < 1)
                                  ? 1 : (CLK_FREQ_HZ / (BAUD * 16));
    localparam integer CNT_W   = $clog2(DIV_X16 + 1);

    reg [CNT_W-1:0] cnt;
    reg [3:0]       x16_cnt;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt      <= {CNT_W{1'b0}};
            x16_cnt  <= 4'd0;
            tick_x16 <= 1'b0;
            tick_x1  <= 1'b0;
        end else begin
            tick_x16 <= 1'b0;
            tick_x1  <= 1'b0;
            if (cnt == DIV_X16 - 1) begin
                cnt      <= {CNT_W{1'b0}};
                tick_x16 <= 1'b1;
                if (x16_cnt == 4'd15) begin
                    x16_cnt <= 4'd0;
                    tick_x1 <= 1'b1;
                end else begin
                    x16_cnt <= x16_cnt + 4'd1;
                end
            end else begin
                cnt <= cnt + 1'b1;
            end
        end
    end

endmodule
