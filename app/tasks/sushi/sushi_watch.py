#!/usr/bin/env python3
"""
Log every change to the Sushi board, straight from the game's save.

The board lives in Chromium LocalStorage as a flat int array under `y5:Sushi`,
where the stored value is one BELOW the tier the UI shows and -1 means empty.
Verified against a screenshot: save 40/40/38/37 == displayed 41/41/39/38.

Why this instead of reading the screen: the merge cascade reshuffles the whole
grid and animates while it does, so pixels show intermediate states that are not
board states at all.  The save is the board, before and after, with nothing to
interpret.  Run this, do a few merges, and the diffs say exactly what the game
did -- which is the one thing the recording could not settle.

    python sushi_watch.py                # follow until Ctrl+C
    python sushi_watch.py --raw          # also dump the full array each change

Read-only.  It copies the leveldb files rather than opening the database,
because the game holds a lock on it while running.
"""

import argparse
import glob
import os
import re
import shutil
import sys
import tempfile
import time

# The sushi chart ends at tier 59, so storage values run 0..59 (display = +1).
# A little headroom in case the cap moves; anything past this is not a tier.
MAX_TIER = 70

# The board is Sushi[0], and the save writes Sushi[0], [1], [2] ... one after
# another, so a flat scrape runs straight past the board into upgrade levels
# and currency.  That is why the tail of the array held values like 3/3/1/1 and
# a lone 47, and why index positions never lined up with the screen.
# N.js loops the board as `for (n = 0; n < 120; n++)` and indexes columns as
# `slot % 15`, so the board is at most 120 slots, 15 wide.
BOARD_SLOTS = 120
GRID_COLS = 15

SAVE_DIR = os.path.join(os.environ.get("APPDATA", ""),
                        "legends-of-idleon", "Local Storage", "leveldb")


def read_board(save_dir=SAVE_DIR, want_src=False):
    """Current Sushi array as a list of ints (stored values, not display tiers)."""
    tmp = tempfile.mkdtemp(prefix="sushi_")
    try:
        files = []
        for pat in ("*.ldb", "*.log"):
            files += glob.glob(os.path.join(save_dir, pat))
        if not files:
            return (None, (None, 0)) if want_src else None
        # Newest last: later writes win, which is what makes a stale copy of the
        # same key in an older file harmless.
        files.sort(key=os.path.getmtime)
        best = None
        best_src = (None, 0)
        for f in files:
            dst = os.path.join(tmp, os.path.basename(f))
            try:
                shutil.copy2(f, dst)
                blob = open(dst, "rb").read()
            except OSError:
                continue
            # Stop at the first value the game cannot produce.
            #
            # Two boundary guesses failed before this one: a fixed 1200-char
            # window ran into the following key, and cutting at the next `y<n>:`
            # marker was no better -- both returned "tiers" of 754 and 694 for a
            # game whose sushi chart ends at 59.  Rather than keep guessing at
            # the delimiter, trust the domain: tier is 0..MAX_TIER-1 in storage,
            # -1 is empty, and anything else means the array ended.
            for m in re.finditer(rb"y5:Sushi(.{0,1600})", blob, re.S):
                nums = re.findall(r"i(-?\d+)", m.group(1).decode("ascii", "ignore"))
                board = []
                for x in nums:
                    v = int(x)
                    if v < -1 or v >= MAX_TIER:
                        break
                    board.append(v)
                # NEWEST wins, not longest.  Keying on length was a bug: the
                # array is truncated at the first non-tier value, so its parsed
                # length varies with content, and a longer STALE board from an
                # older file beat the fresh shorter one -- the watcher then sat
                # on an unchanging board through a session of real merges and
                # reported nothing.
                board = board[:BOARD_SLOTS]
                if len(board) > 20:
                    best, best_src = board, (os.path.basename(f),
                                             os.path.getmtime(f))
        return (best, best_src) if want_src else best
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def summarise(board):
    """Tier histogram, ignoring empties.  Displayed tier is stored + 1."""
    out = {}
    for v in board:
        if v >= 0:
            out[v + 1] = out.get(v + 1, 0) + 1
    return out


def describe(prev, cur):
    a, b = summarise(prev), summarise(cur)
    gone, made = [], []
    for t in sorted(set(a) | set(b), reverse=True):
        d = b.get(t, 0) - a.get(t, 0)
        if d > 0:
            made.append(f"+{d}x T{t}")
        elif d < 0:
            gone.append(f"{d}x T{t}")
    filled_a = sum(1 for v in prev if v >= 0)
    filled_b = sum(1 for v in cur if v >= 0)
    return (f"{' '.join(gone) or 'nothing lost':<28} -> "
            f"{' '.join(made) or 'nothing gained':<28} "
            f"(filled {filled_a} -> {filled_b})")


def main():
    ap = argparse.ArgumentParser(description="Watch the Sushi board in the save")
    ap.add_argument("--raw", action="store_true", help="dump the whole array too")
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args()

    if not os.path.isdir(SAVE_DIR):
        print(f"[Sushi] save dir not found: {SAVE_DIR}")
        return 1

    prev = read_board()
    if prev is None:
        print("[Sushi] could not find y5:Sushi - is the game running?")
        return 1
    print(f"[Sushi] watching. {sum(1 for v in prev if v >= 0)} sushi on the board.")
    print("[Sushi] do some merges; Ctrl+C to stop.\n")
    print(f"  start: {summarise(prev)}\n")

    n = 0
    last_src = (None, 0)
    try:
        while True:
            time.sleep(args.interval)
            cur, src = read_board(want_src=True)
            if cur is None:
                continue
            if src != last_src:
                # Shows whether the GAME is writing at all.  If this never moves
                # while you play, the save simply has not been flushed yet and
                # no amount of polling will help -- that is a different problem
                # from reading a stale copy.
                print(f"    (save written: {src[0]} at "
                      f"{time.strftime('%H:%M:%S', time.localtime(src[1]))})")
                last_src = src
            if cur == prev:
                continue
            n += 1
            print(f"[change {n}] {describe(prev, cur)}")
            if args.raw:
                print(f"    before: {prev}")
                print(f"    after : {cur}")
            prev = cur
    except KeyboardInterrupt:
        print(f"\n[Sushi] {n} change(s) recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ── Proper structured read ────────────────────────────────────────────────────
#
# The save encodes Sushi as nested arrays: `a` opens one, `h` closes it, `i<n>`
# is an int and `d<x>` a float.  Confirmed against the real save -- the first
# sub-array runs exactly 120 entries, matching `for (n = 0; n < 120; n++)` in
# N.js, which is far better evidence than the "stop at the first implausible
# value" heuristic read_board() uses.
#
# That heuristic worked but for the wrong reason: it stopped at the first value
# above MAX_TIER, which happened to land near the board's end.  It reported 184
# entries where the board is 120, so everything after slot 119 was upgrade
# levels being read as sushi.

def read_sushi(save_dir=SAVE_DIR):
    """
    All Sushi sub-arrays, as {0: [...], 1: [...], ...}, or None.

    Sushi[0] is the board (tier-1 per slot, -1 empty), Sushi[1] the chain
    eligibility mask, Sushi[2] upgrade levels, Sushi[4] fuel/currency/cook tier.
    """
    tmp = tempfile.mkdtemp(prefix="sushi_")
    try:
        files = []
        for pat in ("*.ldb", "*.log"):
            files += glob.glob(os.path.join(save_dir, pat))
        files.sort(key=os.path.getmtime)
        best = None
        for f in files:
            dst = os.path.join(tmp, os.path.basename(f))
            try:
                shutil.copy2(f, dst)
                blob = open(dst, "rb").read()
            except OSError:
                continue
            for m in re.finditer(rb"y5:Sushi(.{0,6000})", blob, re.S):
                toks = re.findall(r"(a+|h+|i-?\d+|d[\d.eE+-]+)",
                                  m.group(1).decode("ascii", "ignore"))
                if len(toks) < 150:
                    continue
                subs, cur, idx = {}, None, 0
                for t in toks:
                    if t[0] == "a":
                        if cur is not None:
                            subs[idx] = cur
                            idx += 1
                        cur = []
                    elif t[0] == "h":
                        if cur is not None:
                            subs[idx] = cur
                            idx += 1
                            cur = None
                        if t == "hh":
                            break
                    elif cur is not None:
                        cur.append(int(t[1:]) if t[0] == "i" else float(t[1:]))
                if subs.get(0) and len(subs[0]) >= 100:
                    best = subs
        return best
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
