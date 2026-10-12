"""A small flash translation layer (FTL) simulator.

NAND flash has three physical rules that force every SSD to carry a controller:
  1. Data is written one page at a time.
  2. A page can only be written once until its whole block is erased.
  3. Erasing works on a whole block (many pages) and wears the block out.

So the controller cannot overwrite in place. It writes new data to a fresh page,
marks the old page invalid, and later reclaims space by copying the still-valid
pages out of a block (garbage collection) and erasing it. Those extra copies are
"write amplification": WA = NAND page writes / host page writes.

This file models exactly that, with a page-level logical-to-physical map,
greedy garbage collection, optional wear levelling, and an optional separate
write stream for garbage-collected (likely cold) data.
"""

from __future__ import annotations

import random

HOST, GC = "host", "gc"


class FTL:
    def __init__(
        self,
        blocks: int = 128,
        pages_per_block: int = 64,
        over_provisioning: float = 0.07,
        wear_leveling: bool = False,
        wear_gap: int = 20,
        separate_gc_stream: bool = False,
        stream_fn=None,
        gc_reserve: int = 4,
    ):
        """
        over_provisioning: extra physical space as a fraction of logical space,
            so physical = logical * (1 + OP). Spare space is what makes GC cheap.
        wear_leveling: if True, (a) new data goes to the least-worn free block and
            (b) when the gap between the most- and least-erased blocks exceeds
            wear_gap, GC reclaims the least-worn full block (moving cold data).
        separate_gc_stream: if True, pages copied by GC go to their own open block,
            apart from fresh host writes. Data that survives GC is probably cold, so
            this keeps cold and hot data in different blocks.
        stream_fn: optional function lpn -> stream name. Models a controller that knows
            (or predicts) which data is hot or cold and writes each kind into its own
            blocks, both for host writes and for GC copies. This is an "oracle" in the
            experiments: real controllers must infer temperature.
        gc_reserve: GC runs whenever fewer than this many free blocks remain.
        """
        if over_provisioning <= 0:
            raise ValueError("over_provisioning must be > 0 or GC cannot progress")
        self.B = blocks
        self.P = pages_per_block
        total_pages = blocks * pages_per_block
        self.total_pages = total_pages
        self.L = int(total_pages / (1 + over_provisioning))  # logical pages
        self.wear_leveling = wear_leveling
        self.wear_gap = wear_gap
        self.separate_gc_stream = separate_gc_stream
        self.stream_fn = stream_fn
        self.gc_reserve = gc_reserve

        self.l2p = [-1] * self.L            # logical page -> physical page
        self.p2l = [-1] * total_pages       # physical page -> logical page (-1 = free/invalid)
        self.valid = [0] * blocks           # valid pages in each block
        self.erase_count = [0] * blocks
        self.free_blocks = list(range(blocks))
        self.full_blocks: set[int] = set()
        # open ("active") block and write pointer per stream
        self.active: dict[str, int | None] = {}
        self.write_pointer: dict[str, int] = {}

        self.host_writes = 0
        self.nand_writes = 0   # includes GC copies
        self.gc_runs = 0

    # ---- helpers -----------------------------------------------------------
    def _stream(self, lpn: int, in_gc: bool) -> str:
        if self.stream_fn is not None:
            return self.stream_fn(lpn)
        return GC if (in_gc and self.separate_gc_stream) else HOST

    def _take_free_block(self) -> int:
        if self.wear_leveling:
            # dynamic wear levelling: use the least-worn free block
            blk = min(self.free_blocks, key=lambda b: self.erase_count[b])
            self.free_blocks.remove(blk)
            return blk
        return self.free_blocks.pop(0)

    def _ensure_active(self, stream: str, in_gc: bool) -> None:
        if self.active.get(stream) is not None:
            return
        if not in_gc:
            while len(self.free_blocks) < self.gc_reserve:
                self._garbage_collect()
            # GC may have opened this very stream's block while copying pages;
            # keep it instead of opening another (and leaking the first).
            if self.active.get(stream) is not None:
                return
        self.active[stream] = self._take_free_block()
        self.write_pointer[stream] = 0

    def _program(self, lpn: int, in_gc: bool) -> None:
        stream = self._stream(lpn, in_gc)
        self._ensure_active(stream, in_gc)
        blk = self.active[stream]
        ppn = blk * self.P + self.write_pointer[stream]
        self.p2l[ppn] = lpn
        self.l2p[lpn] = ppn
        self.valid[blk] += 1
        self.write_pointer[stream] += 1
        self.nand_writes += 1
        if self.write_pointer[stream] == self.P:
            self.full_blocks.add(blk)
            self.active[stream] = None

    def _pick_victim(self) -> int:
        candidates = self.full_blocks
        if not candidates:
            raise RuntimeError("no full block to collect; drive is out of space")
        if self.wear_leveling:
            worn = max(self.erase_count)
            least = min(self.erase_count)
            if worn - least > self.wear_gap:
                # static wear levelling: free up the least-worn full block, which
                # usually holds cold data that has been blocking its cells from wearing
                return min(candidates, key=lambda b: self.erase_count[b])
        # greedy: fewest valid pages = least copying
        return min(candidates, key=lambda b: self.valid[b])

    def _garbage_collect(self) -> None:
        victim = self._pick_victim()
        self.gc_runs += 1
        self.full_blocks.discard(victim)
        base = victim * self.P
        for ppn in range(base, base + self.P):
            lpn = self.p2l[ppn]
            if lpn >= 0:
                self.p2l[ppn] = -1
                self.valid[victim] -= 1
                self._program(lpn, in_gc=True)
        assert self.valid[victim] == 0
        self.erase_count[victim] += 1
        self.free_blocks.append(victim)

    # ---- public API --------------------------------------------------------
    def write(self, lpn: int) -> None:
        """Host writes one logical page."""
        if not 0 <= lpn < self.L:
            raise IndexError("logical page out of range")
        self.host_writes += 1
        old = self.l2p[lpn]
        if old >= 0:
            self.p2l[old] = -1
            self.valid[old // self.P] -= 1
            self.l2p[lpn] = -1
        self._program(lpn, in_gc=False)

    def reset_counters(self) -> None:
        self.host_writes = 0
        self.nand_writes = 0
        self.gc_runs = 0

    @property
    def write_amplification(self) -> float:
        return self.nand_writes / self.host_writes if self.host_writes else float("nan")

    # ---- self-check --------------------------------------------------------
    def check_invariants(self) -> None:
        """Raise AssertionError if the mapping tables disagree with each other."""
        for lpn, ppn in enumerate(self.l2p):
            if ppn >= 0:
                assert self.p2l[ppn] == lpn, f"l2p/p2l mismatch at lpn {lpn}"
        counted = [0] * self.B
        for ppn, lpn in enumerate(self.p2l):
            if lpn >= 0:
                assert self.l2p[lpn] == ppn, f"p2l/l2p mismatch at ppn {ppn}"
                counted[ppn // self.P] += 1
        assert counted == self.valid, "per-block valid counts are wrong"
        free_set = set(self.free_blocks)
        assert not (free_set & self.full_blocks), "block both free and full"
        # every block must be in exactly one state: free, full, or an open block
        open_blocks = {b for b in self.active.values() if b is not None}
        assert not (open_blocks & (free_set | self.full_blocks)), "open block also free/full"
        in_use = len(free_set) + len(self.full_blocks) + len(open_blocks)
        assert in_use == self.B, f"{self.B - in_use} block(s) leaked"
        for b in free_set:
            assert self.valid[b] == 0, "free block holds valid data"


# ---- workloads -------------------------------------------------------------
def uniform_workload(n_logical: int, rng: random.Random):
    """Every logical page is equally likely to be overwritten."""
    while True:
        yield rng.randrange(n_logical)


def hot_cold_workload(n_logical: int, rng: random.Random, hot_fraction=0.1, hot_traffic=0.9):
    """hot_traffic of writes go to hot_fraction of the pages (e.g. 90% to 10%)."""
    hot_n = max(1, int(n_logical * hot_fraction))
    while True:
        if rng.random() < hot_traffic:
            yield rng.randrange(hot_n)
        else:
            yield hot_n + rng.randrange(n_logical - hot_n)


def run(ftl: FTL, workload, warmup_passes: float = 5, measure_passes: float = 10):
    """Fill the drive once, warm up to steady state, then measure.

    A 'pass' is one drive's worth of host writes (ftl.L pages).
    Returns write amplification measured over the measurement window.
    """
    for lpn in range(ftl.L):            # sequential fill (precondition)
        ftl.write(lpn)
    for _ in range(int(warmup_passes * ftl.L)):
        ftl.write(next(workload))
    ftl.reset_counters()
    for _ in range(int(measure_passes * ftl.L)):
        ftl.write(next(workload))
    return ftl.write_amplification
