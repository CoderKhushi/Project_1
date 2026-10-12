# Verilog lab: from logic gates to a tiny processor, verified end to end

A library of the digital circuits taught in a first Digital Electronics course, written in Verilog, then pushed through a
verification pipeline of the kind real chip teams use: simulate, check against an independent model, synthesise to gates,
re-test the gates, and finally break the design on purpose to prove the tests can fail.

The code and checks were produced with Claude (Anthropic's AI assistant) and supervised by the project owner, who set the
scope, reviewed the output and can run every stage with one command. The point of the project is the workflow as much as the circuits.

## What is in it

| Group | Blocks (all in `rtl/`) |
|---|---|
| Arithmetic | half adder, full adder, ripple-carry adder, carry-lookahead adder, adder-subtractor with overflow flag, 8-bit ALU (add, sub, and, or, xor, set-less-than, shifts) |
| Selection and decoding | 2:1 and 4:1 multiplexers, 1:4 demultiplexer, 3:8 decoder, priority encoder, magnitude comparator, binary-to-Gray and Gray-to-binary converters |
| Storage | D latch, D flip-flop (asynchronous and synchronous reset), register with enable, shift register |
| Counting and control | up/down counter with load, modulo-10 counter, Johnson counter, clock divider, finite state machine that detects the bit pattern 1011 |
| Putting it together | A toy processor: register + ALU + program counter steered by a two-state controller, running a 9-instruction program from ROM |

## Run it

```
sudo apt install iverilog           # simulator
pip install yowasp-yosys matplotlib # synthesis tool and charts
make all        # simulate, check, synthesise, gate-level re-test, charts   (about 3 minutes)
make mutate     # inject 19 bugs and confirm the tests catch them            (about 2 minutes)
```

## How it is verified (five independent layers)

1. **Self-checking testbenches.** Exhaustive wherever the input space allows: every a, b and carry-in for the 8-bit adders
   (131,072 vectors), every a, b for the comparator, every a, b and operation for the ALU (524,288 vectors).
   1.65 million checks, 0 errors.
2. **An independent Python reference model.** The testbenches log every vector to CSV; `scripts/check_results.py`
   recomputes each expected result in ordinary Python (for example, the sequence detector is checked by string matching on the
   input history, not by a second state machine). Sequential tests use seeded random stimulus with resets injected in the middle.
3. **Synthesis.** Yosys turns each block into a netlist of simple gates (NOT, AND, NAND, OR, NOR, XOR, XNOR, MUX) and D flip-flops.
4. **Gate-level re-test.** The same testbenches run unchanged on the synthesised netlist and are re-checked against Python, which
   confirms that synthesis preserved the behaviour. Result: identical, 0 mismatches.
5. **Mutation testing.** 19 plausible bugs (a dropped carry-in, a counter that counts down by 2, a branch on the wrong condition, ...)
   are injected one at a time. **All 19 were caught.** Nine were caught only by the Python model, not by the checks written inside
   the testbenches, which is the reason for having an independent model.

Results are in `results/` (JSON) and the charts below.

## Results

**Processor** (`chart_cpu_waveform.png`): the toy processor prints 5, 4, 3, 2, 1, then 2 and halts after 42 cycles, exactly as the
Python instruction-set model predicts, in both the RTL and the gate-level netlist.

**Adders after synthesis** (`chart_adders_synthesis.png`, unit-delay model: every gate counts as one cell and one delay):

| Width | Ripple-carry: gates / depth | Carry-lookahead: gates / depth |
|---|---|---|
| 8 bit | 65 / 17 | 42 / 17 |
| 16 bit | 129 / 33 | 117 / 19 |
| 32 bit | 257 / 65 | 233 / 36 |

Other synthesised sizes: 8-bit ALU 238 gates (depth 20); modulo-10 counter 17; sequence detector 14; processor 167.

What the numbers say, and what they do not:

- Ripple-carry delay grows in direct proportion to width (the carry crosses every stage). Carry-lookahead depth grows much more slowly,
  so the advantage is large at 16 and 32 bits.
- At 8 bits the two came out equal in depth, and the lookahead version is smaller. The synthesis tool rewrites logic, so the circuit you
  describe is not always the circuit you get. I also tried a second synthesis recipe (skipping Yosys's built-in optimisation pass); there the 32-bit lookahead depth rose to 62,
  barely better than ripple-carry (65). The advantage depends on how the tool maps the logic, which is why real teams constrain synthesis with timing targets.
- This is a unit-delay model. It ignores wire delay, gate sizes and real cell libraries, so it compares designs against each other
  but does not predict a real chip's speed or area.

## Honest limits

- Toy scale: 8-bit blocks and a 4-bit processor. No pipeline, no memory beyond a ROM, no interrupts.
- Simulation only. Nothing was run on an FPGA or taped out.
- The GitHub Actions workflow (`.github/workflows/verify.yml`) is written and the same commands pass locally, but it has not been run on GitHub
  until the repository is pushed.
- The D latch is tested in simulation only (latches are avoided in synthesised flows).
- `$random` and Python's random generator are seeded, so every run is repeatable.

## Folder guide

```
rtl/        Verilog source: comb.v, seq.v, cpu.v, wrappers.v (fixed-width versions used for test and synthesis)
tb/         Testbenches: tb_comb.v, tb_seq.v, tb_cpu.v, tb_basic.v
scripts/    gen_stimulus.py, check_results.py (independent model), synth.py, mutation_test.py, make_charts.py
results/    verify_rtl.json, verify_gl.json, synth_report.json, mutation_report.json
```
