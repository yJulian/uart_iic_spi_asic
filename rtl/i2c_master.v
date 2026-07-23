// Simple single-master I2C controller with a byte-level primitive
// interface: START (incl. repeated START), STOP, WRITE-byte and
// READ-byte. The command engine composes these into full transactions.
//
// SCL/SDA are exposed as split out/oe/in triplets, matching how a real
// open-drain pad is controlled: driving "0" means oe=1,out=0; driving
// "1" means releasing the line (oe=0) and letting the external pull-up
// take it high.
//
// Clock stretching is not implemented (the master does not sample scl_in
// before ending a high phase) - a deliberate scope cut for this design;
// see docs/PROTOCOL.md for details.
module i2c_master (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [15:0] clk_div,      // quarter-bit period, in clk cycles (0 treated as 1)

    input  wire        cmd_start,    // pulse: issue START / repeated START
    input  wire        cmd_stop,     // pulse: issue STOP
    input  wire        cmd_wr,       // pulse: write wr_data, then sample ACK/NACK
    input  wire        cmd_rd,       // pulse: read a byte, drive ACK/NACK per rd_ack_en
    input  wire  [7:0] wr_data,
    input  wire        rd_ack_en,    // 1 = ACK after the read byte (more bytes follow)

    output reg   [7:0] rd_data,
    output reg          busy,
    output reg          done,        // one-cycle pulse
    output reg          ack_error,   // valid at `done` following cmd_wr: 1 = slave NACKed

    output reg          scl_out,
    output reg          scl_oe,
    input  wire         scl_in,
    output reg          sda_out,
    output reg          sda_oe,
    input  wire         sda_in
);

    localparam [3:0]
        S_IDLE      = 4'd0,
        S_START1    = 4'd1,   // force SCL low
        S_START2    = 4'd2,   // release SDA high
        S_START3    = 4'd3,   // release SCL high
        S_START4    = 4'd4,   // pull SDA low: START condition
        S_BIT_LOW1  = 4'd5,   // SCL low, drive next bit
        S_BIT_LOW2  = 4'd6,   // SCL low, hold (setup time)
        S_BIT_HIGH1 = 4'd7,   // SCL high, sample point
        S_BIT_HIGH2 = 4'd8,   // SCL high, hold
        S_BIT_NEXT  = 4'd9,   // advance to next bit or finish byte
        S_PARK_LOW  = 4'd10,  // leave SCL low between operations
        S_DONE      = 4'd11,
        S_STOP1     = 4'd12,  // force SCL low, SDA low
        S_STOP2     = 4'd13,  // release SCL high, SDA still low
        S_STOP3     = 4'd14;  // release SDA high: STOP condition

    localparam OP_WRITE = 1'b0, OP_READ = 1'b1;

    reg [3:0] state;
    reg [3:0] bit_cnt;   // 0..7 data bits, 8 = ack/nack bit
    reg [7:0] shift_reg;
    reg       op;

    wire [15:0] div_max = (clk_div == 16'd0) ? 16'd1 : clk_div;
    reg  [15:0] div_cnt;
    wire        tick = (div_cnt >= div_max - 16'd1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_cnt <= 16'd0;
        end else if (state == S_IDLE) begin
            div_cnt <= 16'd0;
        end else if (tick) begin
            div_cnt <= 16'd0;
        end else begin
            div_cnt <= div_cnt + 16'd1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_IDLE;
            bit_cnt   <= 4'd0;
            shift_reg <= 8'd0;
            op        <= OP_WRITE;
            rd_data   <= 8'd0;
            busy      <= 1'b0;
            done      <= 1'b0;
            ack_error <= 1'b0;
            scl_out   <= 1'b0;
            scl_oe    <= 1'b0;
            sda_out   <= 1'b0;
            sda_oe    <= 1'b0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (cmd_start) begin
                        busy  <= 1'b1;
                        state <= S_START1;
                    end else if (cmd_stop) begin
                        busy  <= 1'b1;
                        state <= S_STOP1;
                    end else if (cmd_wr) begin
                        busy      <= 1'b1;
                        op        <= OP_WRITE;
                        shift_reg <= wr_data;
                        bit_cnt   <= 4'd0;
                        state     <= S_BIT_LOW1;
                    end else if (cmd_rd) begin
                        busy    <= 1'b1;
                        op      <= OP_READ;
                        bit_cnt <= 4'd0;
                        state   <= S_BIT_LOW1;
                    end
                end

                // ---- START / repeated START ----
                S_START1: begin
                    scl_oe <= 1'b1; scl_out <= 1'b0;
                    if (tick) state <= S_START2;
                end
                S_START2: begin
                    sda_oe <= 1'b0;
                    if (tick) state <= S_START3;
                end
                S_START3: begin
                    scl_oe <= 1'b0;
                    if (tick) state <= S_START4;
                end
                S_START4: begin
                    sda_oe <= 1'b1; sda_out <= 1'b0;
                    if (tick) state <= S_DONE;
                end

                // ---- generic bit transfer: bits 0..7 = data, bit 8 = ack ----
                S_BIT_LOW1: begin
                    scl_oe <= 1'b1; scl_out <= 1'b0;
                    if (bit_cnt == 4'd8) begin
                        if (op == OP_WRITE) begin
                            sda_oe <= 1'b0; // release, listen for slave ACK
                        end else begin
                            sda_oe  <= rd_ack_en;  // drive low = ACK when more bytes follow
                            sda_out <= 1'b0;
                        end
                    end else begin
                        if (op == OP_WRITE) begin
                            sda_oe  <= ~shift_reg[7];
                            sda_out <= 1'b0;
                        end else begin
                            sda_oe <= 1'b0; // release, reading
                        end
                    end
                    if (tick) state <= S_BIT_LOW2;
                end
                S_BIT_LOW2: if (tick) state <= S_BIT_HIGH1;
                S_BIT_HIGH1: begin
                    scl_oe <= 1'b0;
                    if (tick) begin
                        if (bit_cnt == 4'd8) begin
                            if (op == OP_WRITE) ack_error <= sda_in; // 1 = NACK
                        end else if (op == OP_READ) begin
                            shift_reg <= {shift_reg[6:0], sda_in};
                        end
                        state <= S_BIT_HIGH2;
                    end
                end
                S_BIT_HIGH2: if (tick) state <= S_BIT_NEXT;
                S_BIT_NEXT: begin
                    if (bit_cnt == 4'd8) begin
                        if (op == OP_READ) rd_data <= shift_reg;
                        state <= S_PARK_LOW;
                    end else begin
                        bit_cnt <= bit_cnt + 4'd1;
                        // Only WRITE needs to pre-shift the next output
                        // bit into place; READ's shift_reg was already
                        // updated with the sampled bit in S_BIT_HIGH1.
                        if (op == OP_WRITE) shift_reg <= {shift_reg[6:0], 1'b0};
                        state <= S_BIT_LOW1;
                    end
                end
                S_PARK_LOW: begin
                    scl_oe <= 1'b1; scl_out <= 1'b0;
                    if (tick) state <= S_DONE;
                end

                S_DONE: begin
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                // ---- STOP ----
                S_STOP1: begin
                    scl_oe <= 1'b1; scl_out <= 1'b0;
                    sda_oe <= 1'b1; sda_out <= 1'b0;
                    if (tick) state <= S_STOP2;
                end
                S_STOP2: begin
                    scl_oe <= 1'b0;
                    if (tick) state <= S_STOP3;
                end
                S_STOP3: begin
                    sda_oe <= 1'b0;
                    if (tick) state <= S_DONE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
