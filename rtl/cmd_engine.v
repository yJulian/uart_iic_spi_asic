// Frame parser / dispatcher / response builder. Implements the UART
// command protocol described in docs/PROTOCOL.md: receives a framed
// command, drives the I2C or SPI master through the requested
// transaction, and sends back a framed response.
module cmd_engine #(
    parameter integer MAX_PAYLOAD = 16,
    parameter integer NUM_SPI_CS  = 2
) (
    input  wire clk,
    input  wire rst_n,

    // UART RX
    input  wire [7:0] urx_data,
    input  wire        urx_valid,

    // UART TX
    output reg  [7:0] utx_data,
    output reg          utx_start,
    input  wire          utx_busy,

    // I2C master control (see rtl/i2c_master.v for the primitive semantics)
    output reg           i2c_cmd_start,
    output reg           i2c_cmd_stop,
    output reg           i2c_cmd_wr,
    output reg           i2c_cmd_rd,
    output reg   [7:0]  i2c_wr_data,
    output reg           i2c_rd_ack_en,
    input  wire  [7:0]  i2c_rd_data,
    input  wire          i2c_done,
    input  wire          i2c_ack_error,
    output reg  [15:0]  i2c_clk_div,

    // SPI master control
    output reg                              spi_start,
    output reg [$clog2(NUM_SPI_CS)-1:0]     spi_cs_sel,
    output reg                              spi_hold_cs,
    output reg [7:0]                        spi_tx_data,
    input  wire [7:0]                       spi_rx_data,
    input  wire                             spi_done,
    output reg [15:0]                       spi_clk_div,
    output reg [1:0]                        spi_mode,
    output reg [NUM_SPI_CS-1:0]             spi_cs_polarity,

    output wire busy,
    output wire error
);

    // ------------------------------------------------------------------
    // Protocol constants
    // ------------------------------------------------------------------
    localparam [7:0]
        OPC_PING           = 8'h01,
        OPC_GET_ID         = 8'h02,
        OPC_I2C_WRITE      = 8'h10,
        OPC_I2C_READ       = 8'h11,
        OPC_I2C_WRITE_READ = 8'h12,
        OPC_SPI_XFER       = 8'h20,
        OPC_SET_CONFIG     = 8'h30,
        OPC_GET_STATUS     = 8'h31;

    localparam [7:0]
        STAT_ACK           = 8'h06,
        STAT_NACK          = 8'h15,
        STAT_ERR_CHECKSUM  = 8'h16,
        STAT_ERR_LEN       = 8'h17,
        STAT_ERR_OPCODE    = 8'h18;

    localparam [7:0]
        PARAM_I2C_CLKDIV   = 8'h01,
        PARAM_SPI_CLKDIV   = 8'h02,
        PARAM_SPI_MODE     = 8'h03,
        PARAM_CS_POLARITY  = 8'h04;

    localparam [7:0] DEVICE_VERSION = 8'h01;
    localparam [7:0] DEVICE_ID      = 8'hA5;

    // ------------------------------------------------------------------
    // State encoding
    // ------------------------------------------------------------------
    localparam [4:0]
        ST_RX_OPCODE        = 5'd0,
        ST_RX_LEN            = 5'd1,
        ST_RX_PAYLOAD        = 5'd2,
        ST_RX_CKSUM          = 5'd3,
        ST_VALIDATE          = 5'd4,
        ST_I2C_ISSUE         = 5'd5,
        ST_I2C_WAIT          = 5'd6,
        ST_EX_I2C_START_DONE = 5'd7,
        ST_EX_I2C_ADDR_DONE  = 5'd8,
        ST_EX_I2C_WDATA_DONE = 5'd9,
        ST_EX_I2C_RDATA_DONE = 5'd10,
        ST_EX_I2C_STOP_DONE  = 5'd11,
        ST_EX_SPI_ISSUE      = 5'd12,
        ST_EX_SPI_WAIT       = 5'd13,
        ST_TX_STATUS         = 5'd14,
        ST_TX_RLEN           = 5'd15,
        ST_TX_PAYLOAD        = 5'd16,
        ST_TX_PAYLOAD_INC    = 5'd17,
        ST_TX_CKSUM          = 5'd18,
        ST_SEND_ISSUE        = 5'd19,
        ST_SEND_GAP          = 5'd20,
        ST_SEND_WAIT         = 5'd21;

    localparam [1:0]
        I2C_OP_START = 2'd0,
        I2C_OP_STOP  = 2'd1,
        I2C_OP_WR    = 2'd2,
        I2C_OP_RD    = 2'd3;

    localparam [2:0]
        OPK_NONE           = 3'd0,
        OPK_I2C_WRITE      = 3'd1,
        OPK_I2C_READ       = 3'd2,
        OPK_I2C_WRITE_READ = 3'd3,
        OPK_SPI            = 3'd4;

    // ------------------------------------------------------------------
    // Registers
    // ------------------------------------------------------------------
    reg  [4:0] state;

    reg  [7:0] rx_buf [0:MAX_PAYLOAD-1];
    reg  [7:0] tx_buf [0:MAX_PAYLOAD-1];

    reg  [7:0] opcode, len, cksum_calc, rx_cksum_reg;
    reg  [7:0] resp_status, resp_len, tx_cksum;
    reg  [7:0] byte_idx, data_idx, rd_idx, tx_idx, remaining, saved_nwrite;
    reg  [7:0] prev_status;

    reg  [2:0] op_kind;
    reg        wr_phase;

    reg  [1:0] i2c_op_sel;
    reg  [4:0] i2c_return_state;

    reg  [7:0] send_byte_data;
    reg  [4:0] send_return_state;

    wire addr_rw_bit = (op_kind == OPK_I2C_READ) ? 1'b1 :
                        (op_kind == OPK_I2C_WRITE_READ) ? ~wr_phase : 1'b0;

    wire [7:0] read_count = (op_kind == OPK_I2C_READ) ? rx_buf[1]
                                                        : rx_buf[8'd2 + saved_nwrite];

    assign busy  = (state != ST_RX_OPCODE);
    assign error = (prev_status != STAT_ACK);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state          <= ST_RX_OPCODE;
            opcode         <= 8'd0;
            len            <= 8'd0;
            cksum_calc     <= 8'd0;
            rx_cksum_reg   <= 8'd0;
            resp_status    <= 8'd0;
            resp_len       <= 8'd0;
            tx_cksum       <= 8'd0;
            byte_idx       <= 8'd0;
            data_idx       <= 8'd0;
            rd_idx         <= 8'd0;
            tx_idx         <= 8'd0;
            remaining      <= 8'd0;
            saved_nwrite   <= 8'd0;
            prev_status    <= STAT_ACK;
            op_kind        <= OPK_NONE;
            wr_phase       <= 1'b0;
            i2c_op_sel     <= I2C_OP_START;
            i2c_return_state  <= ST_RX_OPCODE;
            send_byte_data    <= 8'd0;
            send_return_state <= ST_RX_OPCODE;

            utx_data      <= 8'd0;
            utx_start     <= 1'b0;
            i2c_cmd_start <= 1'b0;
            i2c_cmd_stop  <= 1'b0;
            i2c_cmd_wr    <= 1'b0;
            i2c_cmd_rd    <= 1'b0;
            i2c_wr_data   <= 8'd0;
            i2c_rd_ack_en <= 1'b0;
            i2c_clk_div   <= 16'd10;

            spi_start       <= 1'b0;
            spi_cs_sel      <= {$clog2(NUM_SPI_CS){1'b0}};
            spi_hold_cs     <= 1'b0;
            spi_tx_data     <= 8'd0;
            spi_clk_div     <= 16'd10;
            spi_mode        <= 2'b00;
            spi_cs_polarity <= {NUM_SPI_CS{1'b0}};
        end else begin
            // Default: pulses are one-shot unless re-asserted below.
            utx_start     <= 1'b0;
            i2c_cmd_start <= 1'b0;
            i2c_cmd_stop  <= 1'b0;
            i2c_cmd_wr    <= 1'b0;
            i2c_cmd_rd    <= 1'b0;
            spi_start     <= 1'b0;

            case (state)
                // ---------------- frame reception ----------------
                ST_RX_OPCODE: if (urx_valid) begin
                    opcode     <= urx_data;
                    cksum_calc <= urx_data;
                    state      <= ST_RX_LEN;
                end

                ST_RX_LEN: if (urx_valid) begin
                    len        <= urx_data;
                    cksum_calc <= cksum_calc ^ urx_data;
                    byte_idx   <= 8'd0;
                    state      <= (urx_data == 8'd0) ? ST_RX_CKSUM : ST_RX_PAYLOAD;
                end

                ST_RX_PAYLOAD: if (urx_valid) begin
                    if (byte_idx < MAX_PAYLOAD) rx_buf[byte_idx] <= urx_data;
                    cksum_calc <= cksum_calc ^ urx_data;
                    if (byte_idx == len - 8'd1) begin
                        state <= ST_RX_CKSUM;
                    end else begin
                        byte_idx <= byte_idx + 8'd1;
                    end
                end

                ST_RX_CKSUM: if (urx_valid) begin
                    rx_cksum_reg <= urx_data;
                    state        <= ST_VALIDATE;
                end

                // ---------------- validation & dispatch ----------------
                ST_VALIDATE: begin
                    if (rx_cksum_reg != cksum_calc) begin
                        resp_status <= STAT_ERR_CHECKSUM;
                        resp_len    <= 8'd0;
                        state       <= ST_TX_STATUS;
                    end else if (len > MAX_PAYLOAD) begin
                        resp_status <= STAT_ERR_LEN;
                        resp_len    <= 8'd0;
                        state       <= ST_TX_STATUS;
                    end else begin
                        case (opcode)
                            OPC_PING: begin
                                resp_status <= (len == 8'd0) ? STAT_ACK : STAT_ERR_LEN;
                                resp_len    <= 8'd0;
                                state       <= ST_TX_STATUS;
                            end

                            OPC_GET_ID: begin
                                if (len == 8'd0) begin
                                    resp_status <= STAT_ACK;
                                    resp_len    <= 8'd2;
                                    tx_buf[0]   <= DEVICE_VERSION;
                                    tx_buf[1]   <= DEVICE_ID;
                                end else begin
                                    resp_status <= STAT_ERR_LEN;
                                    resp_len    <= 8'd0;
                                end
                                state <= ST_TX_STATUS;
                            end

                            OPC_GET_STATUS: begin
                                if (len == 8'd0) begin
                                    resp_status <= STAT_ACK;
                                    resp_len    <= 8'd1;
                                    tx_buf[0]   <= prev_status;
                                end else begin
                                    resp_status <= STAT_ERR_LEN;
                                    resp_len    <= 8'd0;
                                end
                                state <= ST_TX_STATUS;
                            end

                            OPC_I2C_WRITE: begin
                                if (len == 8'd0) begin
                                    resp_status <= STAT_ERR_LEN;
                                    resp_len    <= 8'd0;
                                    state       <= ST_TX_STATUS;
                                end else begin
                                    op_kind    <= OPK_I2C_WRITE;
                                    data_idx   <= 8'd1;
                                    remaining  <= len - 8'd1;
                                    i2c_op_sel <= I2C_OP_START;
                                    i2c_return_state <= ST_EX_I2C_START_DONE;
                                    state <= ST_I2C_ISSUE;
                                end
                            end

                            OPC_I2C_READ: begin
                                if (len != 8'd2) begin
                                    resp_status <= STAT_ERR_LEN;
                                    resp_len    <= 8'd0;
                                    state       <= ST_TX_STATUS;
                                end else begin
                                    op_kind    <= OPK_I2C_READ;
                                    i2c_op_sel <= I2C_OP_START;
                                    i2c_return_state <= ST_EX_I2C_START_DONE;
                                    state <= ST_I2C_ISSUE;
                                end
                            end

                            OPC_I2C_WRITE_READ: begin
                                if (len < 8'd3 || len != rx_buf[1] + 8'd3) begin
                                    resp_status <= STAT_ERR_LEN;
                                    resp_len    <= 8'd0;
                                    state       <= ST_TX_STATUS;
                                end else begin
                                    op_kind      <= OPK_I2C_WRITE_READ;
                                    wr_phase     <= 1'b1;
                                    saved_nwrite <= rx_buf[1];
                                    data_idx     <= 8'd2;
                                    remaining    <= rx_buf[1];
                                    i2c_op_sel   <= I2C_OP_START;
                                    i2c_return_state <= ST_EX_I2C_START_DONE;
                                    state <= ST_I2C_ISSUE;
                                end
                            end

                            OPC_SPI_XFER: begin
                                if (len == 8'd0) begin
                                    resp_status <= STAT_ERR_LEN;
                                    resp_len    <= 8'd0;
                                    state       <= ST_TX_STATUS;
                                end else begin
                                    op_kind    <= OPK_SPI;
                                    spi_cs_sel <= rx_buf[0][$clog2(NUM_SPI_CS)-1:0];
                                    data_idx   <= 8'd1;
                                    rd_idx     <= 8'd0;
                                    remaining  <= len - 8'd1;
                                    state      <= ST_EX_SPI_ISSUE;
                                end
                            end

                            OPC_SET_CONFIG: begin
                                case (rx_buf[0])
                                    PARAM_I2C_CLKDIV: begin
                                        if (len != 8'd3) begin
                                            resp_status <= STAT_ERR_LEN;
                                        end else begin
                                            i2c_clk_div <= {rx_buf[1], rx_buf[2]};
                                            resp_status <= STAT_ACK;
                                        end
                                    end
                                    PARAM_SPI_CLKDIV: begin
                                        if (len != 8'd3) begin
                                            resp_status <= STAT_ERR_LEN;
                                        end else begin
                                            spi_clk_div <= {rx_buf[1], rx_buf[2]};
                                            resp_status <= STAT_ACK;
                                        end
                                    end
                                    PARAM_SPI_MODE: begin
                                        if (len != 8'd2) begin
                                            resp_status <= STAT_ERR_LEN;
                                        end else begin
                                            spi_mode    <= rx_buf[1][1:0];
                                            resp_status <= STAT_ACK;
                                        end
                                    end
                                    PARAM_CS_POLARITY: begin
                                        if (len != 8'd2) begin
                                            resp_status <= STAT_ERR_LEN;
                                        end else begin
                                            spi_cs_polarity <= rx_buf[1][NUM_SPI_CS-1:0];
                                            resp_status     <= STAT_ACK;
                                        end
                                    end
                                    default: resp_status <= STAT_NACK;
                                endcase
                                resp_len <= 8'd0;
                                state    <= ST_TX_STATUS;
                            end

                            default: begin
                                resp_status <= STAT_ERR_OPCODE;
                                resp_len    <= 8'd0;
                                state       <= ST_TX_STATUS;
                            end
                        endcase
                    end
                end

                // ---------------- I2C primitive call/wait ----------------
                ST_I2C_ISSUE: begin
                    case (i2c_op_sel)
                        I2C_OP_START: i2c_cmd_start <= 1'b1;
                        I2C_OP_STOP:  i2c_cmd_stop  <= 1'b1;
                        I2C_OP_WR:    i2c_cmd_wr    <= 1'b1;
                        I2C_OP_RD:    i2c_cmd_rd    <= 1'b1;
                        default:      ;
                    endcase
                    state <= ST_I2C_WAIT;
                end

                ST_I2C_WAIT: if (i2c_done) state <= i2c_return_state;

                ST_EX_I2C_START_DONE: begin
                    // rx_buf[0][6:0] is the plain 7-bit I2C address per
                    // docs/PROTOCOL.md (bit 7 reserved); the R/W bit is
                    // supplied by the engine, not the host.
                    i2c_wr_data <= {rx_buf[0][6:0], addr_rw_bit};
                    i2c_op_sel  <= I2C_OP_WR;
                    i2c_return_state <= ST_EX_I2C_ADDR_DONE;
                    state <= ST_I2C_ISSUE;
                end

                ST_EX_I2C_ADDR_DONE: begin
                    if (i2c_ack_error) begin
                        resp_status <= STAT_NACK;
                        resp_len    <= 8'd0;
                        i2c_op_sel  <= I2C_OP_STOP;
                        i2c_return_state <= ST_EX_I2C_STOP_DONE;
                        state <= ST_I2C_ISSUE;
                    end else if (op_kind == OPK_I2C_WRITE) begin
                        if (remaining == 8'd0) begin
                            resp_status <= STAT_ACK;
                            resp_len    <= 8'd0;
                            i2c_op_sel  <= I2C_OP_STOP;
                            i2c_return_state <= ST_EX_I2C_STOP_DONE;
                            state <= ST_I2C_ISSUE;
                        end else begin
                            i2c_wr_data <= rx_buf[data_idx];
                            i2c_op_sel  <= I2C_OP_WR;
                            i2c_return_state <= ST_EX_I2C_WDATA_DONE;
                            state <= ST_I2C_ISSUE;
                        end
                    end else if (op_kind == OPK_I2C_WRITE_READ && wr_phase) begin
                        if (remaining == 8'd0) begin
                            wr_phase   <= 1'b0;
                            i2c_op_sel <= I2C_OP_START;
                            i2c_return_state <= ST_EX_I2C_START_DONE;
                            state <= ST_I2C_ISSUE;
                        end else begin
                            i2c_wr_data <= rx_buf[data_idx];
                            i2c_op_sel  <= I2C_OP_WR;
                            i2c_return_state <= ST_EX_I2C_WDATA_DONE;
                            state <= ST_I2C_ISSUE;
                        end
                    end else begin
                        // read phase: plain I2C_READ, or WRITE_READ after repeated start
                        rd_idx    <= 8'd0;
                        remaining <= read_count;
                        if (read_count == 8'd0) begin
                            resp_status <= STAT_ACK;
                            resp_len    <= 8'd0;
                            i2c_op_sel  <= I2C_OP_STOP;
                            i2c_return_state <= ST_EX_I2C_STOP_DONE;
                            state <= ST_I2C_ISSUE;
                        end else begin
                            i2c_rd_ack_en <= (read_count != 8'd1);
                            i2c_op_sel    <= I2C_OP_RD;
                            i2c_return_state <= ST_EX_I2C_RDATA_DONE;
                            state <= ST_I2C_ISSUE;
                        end
                    end
                end

                ST_EX_I2C_WDATA_DONE: begin
                    if (i2c_ack_error) begin
                        resp_status <= STAT_NACK;
                        resp_len    <= 8'd0;
                        i2c_op_sel  <= I2C_OP_STOP;
                        i2c_return_state <= ST_EX_I2C_STOP_DONE;
                        state <= ST_I2C_ISSUE;
                    end else begin
                        data_idx <= data_idx + 8'd1;
                        if (remaining == 8'd1) begin
                            if (op_kind == OPK_I2C_WRITE_READ && wr_phase) begin
                                wr_phase   <= 1'b0;
                                i2c_op_sel <= I2C_OP_START;
                                i2c_return_state <= ST_EX_I2C_START_DONE;
                                state <= ST_I2C_ISSUE;
                            end else begin
                                resp_status <= STAT_ACK;
                                resp_len    <= 8'd0;
                                i2c_op_sel  <= I2C_OP_STOP;
                                i2c_return_state <= ST_EX_I2C_STOP_DONE;
                                state <= ST_I2C_ISSUE;
                            end
                        end else begin
                            remaining   <= remaining - 8'd1;
                            i2c_wr_data <= rx_buf[data_idx + 8'd1];
                            i2c_op_sel  <= I2C_OP_WR;
                            i2c_return_state <= ST_EX_I2C_WDATA_DONE;
                            state <= ST_I2C_ISSUE;
                        end
                    end
                end

                ST_EX_I2C_RDATA_DONE: begin
                    tx_buf[rd_idx] <= i2c_rd_data;
                    rd_idx <= rd_idx + 8'd1;
                    if (remaining == 8'd1) begin
                        resp_status <= STAT_ACK;
                        resp_len    <= rd_idx + 8'd1;
                        i2c_op_sel  <= I2C_OP_STOP;
                        i2c_return_state <= ST_EX_I2C_STOP_DONE;
                        state <= ST_I2C_ISSUE;
                    end else begin
                        remaining     <= remaining - 8'd1;
                        i2c_rd_ack_en <= (remaining != 8'd2);
                        i2c_op_sel    <= I2C_OP_RD;
                        i2c_return_state <= ST_EX_I2C_RDATA_DONE;
                        state <= ST_I2C_ISSUE;
                    end
                end

                ST_EX_I2C_STOP_DONE: state <= ST_TX_STATUS;

                // ---------------- SPI transfer loop ----------------
                ST_EX_SPI_ISSUE: begin
                    spi_tx_data <= rx_buf[data_idx];
                    spi_hold_cs <= (remaining != 8'd1);
                    spi_start   <= 1'b1;
                    state <= ST_EX_SPI_WAIT;
                end

                ST_EX_SPI_WAIT: if (spi_done) begin
                    tx_buf[rd_idx] <= spi_rx_data;
                    rd_idx   <= rd_idx + 8'd1;
                    data_idx <= data_idx + 8'd1;
                    if (remaining == 8'd1) begin
                        resp_status <= STAT_ACK;
                        resp_len    <= rd_idx + 8'd1;
                        state <= ST_TX_STATUS;
                    end else begin
                        remaining <= remaining - 8'd1;
                        state <= ST_EX_SPI_ISSUE;
                    end
                end

                // ---------------- response transmission ----------------
                ST_TX_STATUS: begin
                    prev_status       <= resp_status;
                    send_byte_data    <= resp_status;
                    tx_cksum          <= resp_status;
                    send_return_state <= ST_TX_RLEN;
                    state <= ST_SEND_ISSUE;
                end

                ST_TX_RLEN: begin
                    send_byte_data    <= resp_len;
                    tx_cksum          <= tx_cksum ^ resp_len;
                    tx_idx            <= 8'd0;
                    send_return_state <= (resp_len == 8'd0) ? ST_TX_CKSUM : ST_TX_PAYLOAD;
                    state <= ST_SEND_ISSUE;
                end

                ST_TX_PAYLOAD: begin
                    send_byte_data    <= tx_buf[tx_idx];
                    tx_cksum          <= tx_cksum ^ tx_buf[tx_idx];
                    send_return_state <= (tx_idx == resp_len - 8'd1) ? ST_TX_CKSUM : ST_TX_PAYLOAD_INC;
                    state <= ST_SEND_ISSUE;
                end

                ST_TX_PAYLOAD_INC: begin
                    tx_idx <= tx_idx + 8'd1;
                    state  <= ST_TX_PAYLOAD;
                end

                ST_TX_CKSUM: begin
                    send_byte_data    <= tx_cksum;
                    send_return_state <= ST_RX_OPCODE;
                    state <= ST_SEND_ISSUE;
                end

                ST_SEND_ISSUE: begin
                    utx_data  <= send_byte_data;
                    utx_start <= 1'b1;
                    state <= ST_SEND_GAP;
                end

                ST_SEND_GAP: state <= ST_SEND_WAIT;

                ST_SEND_WAIT: if (!utx_busy) state <= send_return_state;

                default: state <= ST_RX_OPCODE;
            endcase
        end
    end

endmodule
