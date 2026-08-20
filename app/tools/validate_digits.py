#!/usr/bin/env python3
"""
Check the digit reader against the save, on whatever is on the board now.

The save is the ground truth here: Sushi[0] holds the real tier of every
occupied slot, so nothing in this check depends on the thing being checked.
The one guard that matters is that the save and the screen are describing the
same moment -- the save flushes every ~175 s, so a board that has merged since
the last flush would fail this for reasons that are nobody's fault.  Matching
occupancy is the test for that, and a mismatch means "try again in a minute",
not "the reader is broken".

Run with the station open.  Reports per tier, and separately for one- and
two-digit numbers, because the single-digit path is the one no developed
board can exercise.
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tasks", "sushi"))

import core.input as _input                       # noqa: E402
import core.minigame as _minigame                 # noqa: E402
import core.window as _window                     # noqa: E402

sys.modules.setdefault("gamewindow", _window)
sys.modules.setdefault("clicker", _input)
sys.modules.setdefault("minigame", _minigame)

import sushi_watch as W                           # noqa: E402
import sushisim as M                              # noqa: E402
import sushivision as V                           # noqa: E402


def main():
    frame, scale = V.grab_station()
    if frame is None:
        print("the sushi station is not on screen - focus the game and open it")
        return 1
    data = W.read_sushi()
    if not data or not data.get(0):
        print("cannot read the save")
        return 1
    save = data[0]

    digits = V.load_digit_masks()
    lib = V.load_tiers()
    print(f"digit library: "
          f"{ {d: sum(len(v) for v in bs.values()) for d, bs in sorted(digits.items())} }")
    print(f"tier templates: {min(lib)}-{max(lib)}" if lib else "no tier templates")

    occ_save = {s for s in range(M.BOARD_SLOTS) if save[s] >= 0}
    rows = []
    for s in sorted(occ_save):
        truth = save[s] + 1
        d = V.read_tier_digits(frame, s, digits, scale)
        t = V.read_cell_tier(frame, s, lib, scale)
        rows.append((s, truth, d, t))

    print(f"\n{'slot':<6}{'truth':<7}{'digits':<8}{'templates':<11}verdict")
    for s, truth, d, t in rows:
        verdict = "OK" if d == truth else ("declined" if d is None else "WRONG")
        print(f"{s:<6}{truth:<7}{str(d):<8}{str(t):<11}{verdict}")

    def tally(sel, label):
        sub = [r for r in rows if sel(r[1])]
        if not sub:
            print(f"\n{label}: none on the board")
            return
        ok = sum(1 for _, tr, d, _ in sub if d == tr)
        wrong = sum(1 for _, tr, d, _ in sub if d is not None and d != tr)
        dec = sum(1 for _, _, d, _ in sub if d is None)
        print(f"\n{label}: {ok}/{len(sub)} correct, {wrong} WRONG, {dec} declined")
        if wrong:
            print("  wrong:", [(s, tr, d) for s, tr, d, _ in sub
                               if d is not None and d != tr])
        if dec:
            print("  declined:", [(s, tr) for s, tr, d, _ in sub if d is None])

    tally(lambda t: t < 10, "SINGLE DIGIT (1-9)")
    tally(lambda t: t >= 10, "two digit (10+)")
    tally(lambda t: True, "all")

    # And the reading that actually matters: the whole board, digits first.
    board = V.read_board(frame, scale, lib, digits=digits)
    bad = [(s, save[s] + 1, board[s] + 1) for s in range(M.BOARD_SLOTS)
           if save[s] != board[s]]
    print(f"\nread_board vs save: {M.BOARD_SLOTS - len(bad)}/{M.BOARD_SLOTS} "
          f"slots identical")
    if bad:
        print("  differences (slot, save, read):", bad[:12])

    # Digits alone, with no tier templates -- what a player who has never
    # labelled anything actually gets.
    alone = V.read_board(frame, scale, {}, digits=digits)
    bad2 = [s for s in range(M.BOARD_SLOTS) if save[s] != alone[s]]
    print(f"digits alone, no templates: "
          f"{M.BOARD_SLOTS - len(bad2)}/{M.BOARD_SLOTS} slots identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
