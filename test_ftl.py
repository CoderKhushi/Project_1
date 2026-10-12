"""Quick correctness tests. Run:  python test_ftl.py"""
import random

from ftl import FTL, hot_cold_workload, run, uniform_workload


def test_sequential_has_no_amplification():
    ftl = FTL(over_provisioning=0.10)
    # overwriting the whole drive sequentially, again and again, invalidates whole
    # blocks at once, so GC copies (almost) nothing and WA should be ~1
    for lpn in range(ftl.L):
        ftl.write(lpn)
    ftl.reset_counters()
    for _ in range(5):
        for lpn in range(ftl.L):
            ftl.write(lpn)
    ftl.check_invariants()
    assert ftl.write_amplification < 1.1, ftl.write_amplification


def test_random_writes_amplify_and_stay_consistent():
    rng = random.Random(1)
    ftl = FTL(over_provisioning=0.07)
    wa = run(ftl, uniform_workload(ftl.L, rng), warmup_passes=3, measure_passes=3)
    ftl.check_invariants()
    assert wa > 1.5, wa


def test_more_spare_space_means_less_amplification():
    results = []
    for op in (0.07, 0.28):
        rng = random.Random(2)
        ftl = FTL(over_provisioning=op)
        results.append(run(ftl, uniform_workload(ftl.L, rng), 3, 3))
    assert results[0] > results[1], results


def test_knowing_temperature_reduces_amplification():
    # same workload, steady state; the second drive writes hot and cold data to
    # separate blocks, which should cut garbage-collection copying a lot
    # (512 blocks: on a tiny drive the GC reserve eats most of the spare space)
    results = []
    for oracle in (False, True):
        rng = random.Random(3)
        ftl0 = FTL(blocks=512, over_provisioning=0.07)
        hot_n = int(ftl0.L * 0.1)
        fn = (lambda lpn: "hot" if lpn < hot_n else "cold") if oracle else None
        ftl = FTL(blocks=512, over_provisioning=0.07, stream_fn=fn)
        results.append(run(ftl, hot_cold_workload(ftl.L, rng), 6, 4))
        ftl.check_invariants()
    assert results[1] < results[0], results


def test_wear_leveling_reduces_imbalance():
    ratios = []
    for wl in (False, True):
        rng = random.Random(4)
        ftl = FTL(over_provisioning=0.07, wear_leveling=wl)
        run(ftl, hot_cold_workload(ftl.L, rng), 5, 10)
        ftl.check_invariants()
        mean = sum(ftl.erase_count) / ftl.B
        ratios.append(max(ftl.erase_count) / mean)
    assert ratios[1] < ratios[0], ratios


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print("all tests passed")
