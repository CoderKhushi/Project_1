"""Run the three experiments and save charts + results.json.

    python run_experiments.py

Takes several minutes (pure Python). Lower BLOCKS / SEEDS below for a faster, noisier run.
"""
import json
import random
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ftl import FTL, hot_cold_workload, run, uniform_workload

BLOCKS = 512
PAGES_PER_BLOCK = 64
SEEDS = [1, 2]
OP_VALUES = [0.07, 0.12, 0.20, 0.28, 0.40]

# Illustrative program/erase cycle limits per block. Real values depend on the NAND
# generation and the controller's error correction; replace with datasheet numbers.
ENDURANCE = {"TLC (3 bits/cell)": 3000, "QLC (4 bits/cell)": 1000}

GREY, BLUE, ORANGE = "#6b7280", "#1d4ed8", "#d97706"


def make(op, wear_leveling=False, temperature_aware=False):
    """temperature_aware=True: the FTL magically knows the hottest 10% of logical pages
    and keeps them in their own blocks (an upper bound on what prediction can do)."""
    probe = FTL(blocks=BLOCKS, pages_per_block=PAGES_PER_BLOCK, over_provisioning=op)
    hot_n = int(probe.L * 0.1)
    fn = (lambda lpn: "hot" if lpn < hot_n else "cold") if temperature_aware else None
    return FTL(blocks=BLOCKS, pages_per_block=PAGES_PER_BLOCK, over_provisioning=op,
               wear_leveling=wear_leveling, stream_fn=fn)


SERIES = {
    "uniform random writes": ("uniform", False),
    "hot/cold writes (90% to 10% of pages), no placement": ("hot_cold", False),
    "hot/cold writes, temperature-aware placement": ("hot_cold", True),
}


def mean_wa(op, workload_name, aware):
    values = []
    for seed in SEEDS:
        rng = random.Random(seed)
        ftl = make(op, temperature_aware=aware)
        wl = uniform_workload(ftl.L, rng) if workload_name == "uniform" else hot_cold_workload(ftl.L, rng)
        values.append(run(ftl, wl, warmup_passes=8, measure_passes=8))
    return statistics.mean(values), (max(values) - min(values)) / 2


def experiment_1():
    out = {name: [] for name in SERIES}
    for op in OP_VALUES:
        for name, (workload, aware) in SERIES.items():
            wa, spread = mean_wa(op, workload, aware)
            out[name].append({"op": op, "wa": wa, "spread": spread})
            print(f"  OP {op:>4.0%}  {name:<55} WA {wa:5.2f}  (+/- {spread:.2f})", flush=True)
    return out


def lifetime_drive_writes(wa, op, cycles):
    """How many full-drive writes of host data fit before blocks reach their erase limit,
    assuming perfect wear levelling: physical_pages * cycles / (WA * logical_pages)."""
    return cycles * (1 + op) / wa


def experiment_3():
    rows = {}
    for wl in (False, True):
        was, ratios, counts = [], [], None
        for seed in SEEDS:
            rng = random.Random(seed)
            ftl = make(0.07, wear_leveling=wl)
            was.append(run(ftl, hot_cold_workload(ftl.L, rng), warmup_passes=8, measure_passes=12))
            mean = sum(ftl.erase_count) / ftl.B
            ratios.append(max(ftl.erase_count) / mean)
            counts = list(ftl.erase_count)  # keep the last seed for the histogram
        rows[wl] = {"wa": statistics.mean(was), "imbalance": statistics.mean(ratios), "erase_counts": counts}
        label = "on " if wl else "off"
        print(f"  wear levelling {label}: WA {rows[wl]['wa']:.2f}, max/mean erase {rows[wl]['imbalance']:.2f}", flush=True)
    return rows


def main():
    print("Experiment 1: write amplification vs over-provisioning")
    e1 = experiment_1()

    print("Experiment 2: estimated lifetime, TLC vs QLC (uniform random writes)")
    uni = e1["uniform random writes"]
    e2 = {}
    for name, cycles in ENDURANCE.items():
        e2[name] = [lifetime_drive_writes(p["wa"], p["op"], cycles) for p in uni]
        print(f"  {name}: " + ", ".join(f"OP {p['op']:.0%} -> {v:,.0f}" for p, v in zip(uni, e2[name])))

    print("Experiment 3: wear levelling under a hot/cold workload (OP 7%)")
    e3 = experiment_3()

    # ---- charts ----
    ops = [p["op"] * 100 for p in uni]
    fig, ax = plt.subplots(figsize=(6.4, 4))
    for (name, pts), color in zip(e1.items(), (BLUE, ORANGE, "#059669")):
        ax.errorbar(ops, [p["wa"] for p in pts], yerr=[p["spread"] for p in pts],
                    marker="o", color=color, label=name, capsize=3)
    ax.set_xlabel("Over-provisioning (% extra physical space)")
    ax.set_ylabel("Write amplification (NAND writes / host writes)")
    ax.set_title("More spare space means less copying during garbage collection")
    ax.set_ylim(bottom=1)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig("chart1_write_amplification.png", dpi=160)

    fig, ax = plt.subplots(figsize=(6.4, 4))
    for (name, vals), color in zip(e2.items(), (BLUE, ORANGE)):
        ax.plot(ops, vals, marker="o", color=color, label=name)
    ax.set_yscale("log")
    ax.set_xlabel("Over-provisioning (% extra physical space)")
    ax.set_ylabel("Full-drive writes before wear-out (log scale)")
    ax.set_title("QLC gains capacity but gives up lifetime (illustrative limits)")
    ax.grid(alpha=0.3, which="both")
    ax.legend()
    fig.tight_layout()
    fig.savefig("chart2_tlc_vs_qlc_lifetime.png", dpi=160)

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), sharey=True)
    for ax, wl in zip(axes, (False, True)):
        r = e3[wl]
        ax.hist(r["erase_counts"], bins=25, color=GREY if not wl else BLUE)
        ax.set_title(f"Wear levelling {'on' if wl else 'off'}\n"
                     f"WA {r['wa']:.2f}, worst block {r['imbalance']:.2f}x the average")
        ax.set_xlabel("Erase count per block")
    axes[0].set_ylabel("Number of blocks")
    fig.tight_layout()
    fig.savefig("chart3_wear_levelling.png", dpi=160)

    results = {
        "config": {"blocks": BLOCKS, "pages_per_block": PAGES_PER_BLOCK, "seeds": SEEDS},
        "write_amplification": e1,
        "lifetime_full_drive_writes": {"op": OP_VALUES, **e2},
        "wear_levelling": {("on" if k else "off"): {"wa": v["wa"], "max_over_mean_erase": v["imbalance"]}
                           for k, v in e3.items()},
    }
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("saved results.json and three charts")


if __name__ == "__main__":
    main()
