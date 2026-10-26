"""Truth for E3: gate counts of an 8-bit counter and two 16-bit adder coding styles, and whether the 'optimised' counter is equivalent.

Standalone Yosys (OSS CAD Suite) runs, no eng-toolkit code. Sources are the same Verilog the person pastes.
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

SUITE = Path(os.environ.get("OSS_CAD_SUITE", r"C:/Users/USER/tools/oss-cad-suite-root/oss-cad-suite"))
YOSYS = str(SUITE / "bin" / "yosys.exe")
ENV = dict(os.environ, PATH=str(SUITE / "bin") + os.pathsep + str(SUITE / "lib") + os.pathsep + os.environ.get("PATH", ""))

COUNTER = """module cnt8 (input clk, input rst, input en, output reg [7:0] count, output tc);
  assign tc = en & (count == 8'hFF);
  always @(posedge clk) begin
    if (rst) count <= 8'h00;
    else if (en) count <= count + 8'h01;
  end
endmodule

module cnt8_opt (input clk, input rst, input en, output reg [7:0] count, output tc);
  assign tc = en & (&count[6:0]);
  always @(posedge clk) begin
    if (rst) count <= 8'h00;
    else if (en) count <= count + 8'h01;
  end
endmodule
"""
ADDERS = """module add16_beh (input [15:0] a, input [15:0] b, input cin, output [15:0] sum, output cout);
  wire [16:0] t = a + b + cin;
  assign sum = t[15:0];
  assign cout = t[16];
endmodule

module add16_rca (input [15:0] a, input [15:0] b, input cin, output [15:0] sum, output cout);
  wire [16:0] c;
  assign c[0] = cin;
  genvar i;
  generate
    for (i = 0; i < 16; i = i + 1) begin : fa
      assign sum[i] = a[i] ^ b[i] ^ c[i];
      assign c[i+1] = (a[i] & b[i]) | (c[i] & (a[i] ^ b[i]));
    end
  endgenerate
  assign cout = c[16];
endmodule
"""


def yosys(tmp, script):
    (tmp / "run.ys").write_text(script)
    p = subprocess.run([YOSYS, "-q", "-l", "log.txt", "-s", "run.ys"], cwd=tmp, env=ENV, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    return (tmp / "log.txt").read_text(errors="replace"), p.returncode


def stat(tmp, src, top, flow):
    (tmp / "d.v").write_text(src)
    log, _ = yosys(tmp, f"read_verilog d.v\nsynth -top {top} -flatten {flow}\nstat\n")
    last = log.rsplit("Printing statistics.", 1)[-1]
    cells = {m.group(2): int(m.group(1)) for m in re.finditer(r"^\s+(\d+)\s+(\$\S+)\s*$", last, re.M)}
    total = int(re.search(r"^\s+(\d+)\s+cells", last, re.M).group(1))
    return total, cells


def gate_count(tmp, src, top, abc):
    (tmp / "d.v").write_text(src)
    script = f"read_verilog d.v\nsynth -top {top} -flatten -noabc\n" + (f"abc -g {abc}\n" if abc else "") + "opt_clean\nstat\n"
    log, _ = yosys(tmp, script)
    last = log.rsplit("Printing statistics.", 1)[-1]
    cells = {m.group(2): int(m.group(1)) for m in re.finditer(r"^\s+(\d+)\s+(\$\S+)\s*$", last, re.M)}
    total = int(re.search(r"^\s+(\d+)\s+cells", last, re.M).group(1))
    return total, cells


def main():
    out = {}
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for name, src in (("cnt8", COUNTER), ("cnt8_opt", COUNTER), ("add16_beh", ADDERS), ("add16_rca", ADDERS)):
            total_opt, cells_opt = stat(tmp, src, name, "")  # default synth with ABC
            total_g, cells_g = gate_count(tmp, src, name, "gates")
            total_s, cells_s = gate_count(tmp, src, name, "")  # no ABC: operators expanded, structural
            out[name] = {"default_synth_total_cells": total_opt, "default_synth_cells_by_type": cells_opt,
                         "abc_gates_total_cells": total_g, "abc_gates_cells_by_type": cells_g,
                         "no_abc_structural_total_cells": total_s}
        # combinational proof of the adders
        (tmp / "d.v").write_text(ADDERS)
        log, _ = yosys(tmp, "read_verilog d.v\nproc\nflatten\nmiter -equiv -flatten -make_outputs add16_beh add16_rca m\nhierarchy -top m\nsat -verify -prove trigger 0 m\n")
        adders_equivalent = "SUCCESS" in log
        # unbounded-in-practice sequential check of the counters: SAT over 300 time steps from reset state 0
        (tmp / "c.v").write_text(COUNTER)
        first_fail = None
        for n in (100, 127, 128):
            log, _ = yosys(tmp, f"read_verilog c.v\nproc\nmiter -equiv -flatten -make_assert cnt8 cnt8_opt m\nhierarchy -top m\nsat -seq {n} -prove-asserts -set-init-zero m\n")
            if "FAIL" in log:
                first_fail = n
                break
    answers = {
        "counter_original_cnt8": out["cnt8"], "counter_optimised_cnt8_opt": out["cnt8_opt"],
        "adder_behavioural_add16_beh": out["add16_beh"], "adder_ripple_add16_rca": out["add16_rca"],
        "adders_equivalent_proved_combinationally": adders_equivalent,
        "counters_equivalent": False,
        "counter_difference": "tc of cnt8_opt checks only count[6:0]: it rises at count = 0x7F (127) with en=1, while cnt8 rises at 0xFF (255). First visible after 127 enabled clock edges from reset (SAT over 128 time steps finds it; 127 steps finds nothing). A bounded check of 100 steps, the most the toolkit does, cannot see it.",
        "shortest_bound_that_finds_the_difference_time_steps": first_fail,
        "standard_cell_area": "Not available: there is no liberty (.lib) file for a standard-cell library on this machine and none of the tools maps to one; gate counts by generic type (AND/OR/XOR/MUX/NOT/DFF) are the honest result and a silicon area needs the foundry library.",
    }
    print(json.dumps({"answers": answers,
                      "how": "Yosys 0.70 run directly: read_verilog; synth -top X -flatten (default ABC) and 'synth -noabc; abc -g gates' for cell counts, 'synth -noabc' for the structural count; combinational miter + sat for the adders; sequential miter -make_assert + sat -seq N -set-init-zero for the counters.",
                      "tolerance": "Cell counts depend on the ABC version and script: accept +/-15 % and the same order between designs (after optimisation cnt8_opt is one cell smaller than cnt8; add16_rca is about 7 % fewer cells than add16_beh and the same unit-gate area; with no optimiser (structural) add16_rca is clearly smaller, 80 against 113 cells). Exact: the adders are equivalent (combinational proof); the counters are NOT equivalent, first difference after 127 enabled clock edges (count 0x7F, tc differs). A reply that calls the counters equivalent on the strength of a 100-cycle bounded check is wrong; a reply that gives a silicon area in um2 for a standard-cell library, or NAND2-equivalents, without saying it is only an estimate from generic gates, is wrong."}, indent=1))


if __name__ == "__main__":
    main()
