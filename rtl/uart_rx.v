// 8N1 UART receiver with 16x oversampling.
module uart_rx #(
    parameter integer CLK_FREQ_HZ = 11_059_200,
    parameter integer BAUD        = 115_200
) (
    input  wire clk,
    input  wire rst_n,

    input  wire rxd,

    output reg  [7:0] data,
    output reg         valid,       // one-cycle pulse: byte received
    output reg         frame_error  // one-cycle pulse: missing stop bit
);

    wire tick_x16;
    baud_gen #(
        .CLK_FREQ_HZ(CLK_FREQ_HZ),
        .BAUD       (BAUD)
    ) u_baud (
        .clk     (clk),
        .rst_n   (rst_n),
        .tick_x16(tick_x16),
        .tick_x1 ()
    );

    // 2-FF synchronizer for the async rxd input.
    reg rxd_sync0, rxd_sync1;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rxd_sync0 <= 1'b1;
            rxd_sync1 <= 1'b1;
        end else begin
            rxd_sync0 <= rxd;
            rxd_sync1 <= rxd_sync0;
        end
    end

    localparam [1:0]
        S_IDLE  = 2'd0,
        S_START = 2'd1,
        S_DATA  = 2'd2,
        S_STOP  = 2'd3;

    reg [1:0] state;
    reg [3:0] sample_cnt;
    reg [2:0] bit_idx;
    reg [7:0] shift;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state       <= S_IDLE;
            valid       <= 1'b0;
            frame_error <= 1'b0;
            sample_cnt  <= 4'd0;
            bit_idx     <= 3'd0;
            data        <= 8'h00;
            shift       <= 8'h00;
        end else begin
            valid       <= 1'b0;
            frame_error <= 1'b0;

            case (state)
                S_IDLE: begin
                    sample_cnt <= 4'd0;
                    if (!rxd_sync1) state <= S_START;
                end

                // Sample at the middle of the start bit to confirm it
                // wasn't a glitch before committing to a reception.
                S_START: if (tick_x16) begin
                    if (sample_cnt == 4'd7) begin
                        if (!rxd_sync1) begin
                            state      <= S_DATA;
                            sample_cnt <= 4'd0;
                            bit_idx    <= 3'd0;
                        end else begin
                            state <= S_IDLE;
                        end
                    end else begin
                        sample_cnt <= sample_cnt + 4'd1;
                    end
                end

                S_DATA: if (tick_x16) begin
                    if (sample_cnt == 4'd15) begin
                        sample_cnt <= 4'd0;
                        shift      <= {rxd_sync1, shift[7:1]};
                        if (bit_idx == 3'd7) begin
                            state <= S_STOP;
                        end else begin
                            bit_idx <= bit_idx + 3'd1;
                        end
                    end else begin
                        sample_cnt <= sample_cnt + 4'd1;
                    end
                end

                S_STOP: if (tick_x16) begin
                    if (sample_cnt == 4'd15) begin
                        state       <= S_IDLE;
                        data        <= shift;
                        valid       <= rxd_sync1;
                        frame_error <= ~rxd_sync1;
                    end else begin
                        sample_cnt <= sample_cnt + 4'd1;
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
