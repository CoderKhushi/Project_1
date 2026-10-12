# Flash translation layer (FTL) simulator

A small Python model of what an SSD controller does between the host and the NAND, built to answer one question:

> **How much extra writing does the controller do behind the scenes, and what makes it better or worse?**

## Why this matters

NAND flash cannot overwrite data in place. A page is written once, and space comes back only by erasing a whole block, which wears the block out. So the controller writes updates to fresh pages, tracks where everything lives (the logical-to-physical map), and later copies the still-valid pages out of old blocks so it can erase them (garbage collection, GC). Those extra copies are **write amplification (WA)** = NAND page writes / host page writes. Higher WA means a slower and shorter-lived drive.

## What is modelled

- Page-level logical-to-physical (L2P) map, 64 pages per block, 512 blocks (32,768 physical pages).
- Over-provisioning (OP): spare physical space, `physical = logical * (1 + OP)`.
- Greedy GC: reclaim the block with the fewest valid pages.
- Wear levelling (optional): new data goes to the least-worn free block, and when the erase-count gap exceeds a threshold, GC reclaims the least-worn full block so cold data moves off barely-used cells.
- Multiple write streams, so hot and cold data can be placed in different blocks.
- `check_invariants()` verifies that the maps agree, valid counts are right, and no block is lost. The tests call it after every scenario.

## Files

| File | Purpose |
|---|---|
| `ftl.py` | The simulator |
| `test_ftl.py` | Correctness tests (`python test_ftl.py`) |
| `run_experiments.py` | Runs the experiments, writes charts and `results.json` (a few minutes) |
| `chart1_write_amplification.png` | WA vs over-provisioning |
| `chart2_tlc_vs_qlc_lifetime.png` | Estimated lifetime, TLC vs QLC |
| `chart3_wear_levelling.png` | Erase-count distribution, wear levelling off vs on |

Method for every measurement: fill the drive sequentially, write for 8 drive-capacities of random traffic to reach steady state, reset counters, then measure over 8 more (12 for experiment 3). Results are averaged over 2 random seeds.

## Results

### 1. Spare space is the biggest lever

Uniform random writes, measured WA:

| OP | 7% | 12% | 20% | 28% | 40% |
|---|---|---|---|---|---|
| Measured WA | 7.69 | 4.78 | 3.15 | 2.46 | 1.94 |
| Textbook approximation (1+OP)/(2·OP) | 7.64 | 4.67 | 3.00 | 2.29 | 1.75 |

The simulator tracks the rough analytic shape. It is slightly higher at large OP, which I attribute to the blocks held back as GC reserve and open write blocks, though I did not isolate that. The formula is an approximation, not an exact law.

### 2. Skewed traffic: what the controller does with it matters

Workload: 90% of writes hit 10% of the pages ("hot"), the rest are "cold".

| OP | Uniform | Hot/cold, no placement | Hot/cold, temperature-aware |
|---|---|---|---|
| 7% | 7.69 | 8.24 | 7.02 |
| 20% | 3.15 | 3.64 | 3.00 |
| 40% | 1.94 | 2.37 | 1.86 |

- A plain greedy FTL gets **no benefit** from skewed traffic. It is actually 7-22% worse than uniform here, because hot and cold pages share blocks, so blocks are never mostly invalid.
- If the controller knows which data is hot and keeps it in separate blocks, WA drops by about 15% at 7% OP (7.02 vs 8.24). This "oracle" is an **upper bound**: it is told the temperature. A real controller has to predict it, which is an open design problem.

### 3. Wear levelling evens out wear, at a price (hot/cold, 7% OP)

| | WA | Most-erased block vs average |
|---|---|---|
| Off | 8.23 | 1.67x |
| On | 8.76 | 1.11x |

Wear is far more even, so no block dies early, but the extra copying of cold data costs about 6% more WA. Since the drive fails when its first blocks wear out, this trade-off is usually worth it.

### 4. Lifetime, TLC vs QLC (illustrative)

Full-drive writes before wear-out = `cycles * (1 + OP) / WA`, assuming perfect wear levelling. Cycle limits are **illustrative assumptions** (TLC 3,000, QLC 1,000), not datasheet values.

| OP | 7% | 20% | 40% |
|---|---|---|---|
| TLC | 417 | 1,142 | 2,169 |
| QLC | 139 | 381 | 723 |

Takeaway: QLC stores more per cell but has fewer erase cycles, so the controller has to work harder (more OP, lower WA, stronger ECC) to give a usable lifetime.

## Limitations (read before quoting numbers)

- A 512-block toy drive, not a real SSD. Real drives have many planes and channels, much larger blocks, and a map held partly in DRAM.
- No ECC, read disturb, retention, bad blocks, TRIM, or host caching.
- GC is greedy only; real controllers use cost-benefit and background policies.
- Only two simple workloads, no real traces. The cold region takes a long time to reach steady state, so cold-data effects may be slightly understated.
- The oracle placement is an upper bound, not a realistic design.
- Endurance figures are illustrative.
- Only 2 seeds. The spread between them was under 0.02 WA, but that is not a full confidence analysis.

## Run it

```
pip install matplotlib
python test_ftl.py
python run_experiments.py
```

## Natural next steps

- Replace the oracle with a simple hot/cold predictor (e.g. write-count based) and measure how much of the 15% it recovers.
- Add cost-benefit GC and compare it with greedy.
- Replay a real I/O trace instead of synthetic workloads.
