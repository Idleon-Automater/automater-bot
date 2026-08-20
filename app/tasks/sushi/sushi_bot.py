#!/usr/bin/env python3
"""
Sushi Station executor: arrange the staircase, then feed it.

    python sushi_bot.py --plan        # print the plan, touch nothing
    python sushi_bot.py --arrange     # do the swaps
    python sushi_bot.py --arrange --start 62

WHAT IT DOES
------------
Reads the board from the save, plans a strictly-descending staircase inside a
chain-eligible region, and performs the swaps by dragging.  One merge at the
head of that staircase then promotes every slot in it -- see SUSHI_NOTES.md for
the mechanics, all transcribed from the game bundle.

VERIFICATION IS NOT OPTIONAL HERE
---------------------------------
Two things make blind execution a bad idea:

  * merges are genuinely stochastic (fireplace bonus, cook bonus), so a plan
    simulated forward drifts from reality by design, not by measurement error
  * the save lags ~175 s, so a mid-run read tells you about the past

So the batch is verified at the END against a fresh board, and any mismatch is
reported rather than silently built upon.  Per-swap verification would be
better but is not affordable through the save; reading the board off the screen
would give it instantly, and that is the obvious next upgrade.
"""

import argparse
import random
import glob
import os
import sys
import time

import gamewindow
import sushi_watch
import sushisim as M
import sushivision
from clicker import Clicker, drag, press_hold


def board_and_mask():
    """
    Board from the SCREEN, chain mask from the SAVE.

    Each source is used for what it is good at.  The board moves constantly and
    the save is ~175 s behind, which is what put three sushi in the wrong place
    on an earlier run: the plan was built against a board that had already
    changed.  The chain mask (Sushi[1]) does not move, so its staleness costs
    nothing and it is not readable from pixels at all.
    """
    d = sushi_watch.read_sushi()
    if not d or not d.get(0):
        # Silent before: a crashed game made this fail, the cycle broke out
        # without a word, and the whole run printed "===== cycle 1 =====" and
        # nothing else.  Any exit needs to say why.
        print("[Sushi] cannot read the save (no Sushi data).  Is the game "
              "running?  It may have crashed - restart it and try again.")
        return None, None
    mask = list(d.get(1, []))
    mask += [0] * (M.BOARD_SLOTS - len(mask))   # past the mask -> eligible

    before_dumps = set(glob.glob(os.path.join(sushivision.UNKNOWN_DIR, "*.png")))
    frame, scale = sushivision.grab_station()
    if frame is None:
        print("[Sushi] the sushi station is not on screen "
              "(is the game focused and on the station?)")
        return None, None
    lib = sushivision.load_tiers()
    if not lib:
        print("[Sushi] no tier templates in sushi_unknown/ - cannot read the "
              "board from screen")
        return None, None
    board = [(sushivision.read_cell_tier(frame, s, lib, scale, dump=True) or 0) - 1
             for s in range(M.BOARD_SLOTS)]

    # Reject a frame captured mid-animation.
    #
    # Observed live: two consecutive reads showed all 76 occupied cells go
    # empty and then come straight back.  A planner handed that frame sees an
    # empty board and plans from nothing.  A drop of more than half the sushi
    # between reads is a bad capture, not a game event -- merges remove one at
    # a time.
    occ = sum(1 for v in board if v >= 0)
    last = board_and_mask._last_occ
    if last and occ < last * 0.5:
        time.sleep(0.4)
        frame, scale = sushivision.grab_station()
        if frame is not None:
            board = [(sushivision.read_cell_tier(frame, s, lib, scale) or 0) - 1
                     for s in range(M.BOARD_SLOTS)]
            occ = sum(1 for v in board if v >= 0)
    board_and_mask._last_occ = occ
    # Every occupied cell is proof that cell exists.  This is the only way the
    # owned region is ever learned -- an empty owned cell is indistinguishable
    # from one that was never bought -- so it is recorded at the one place
    # every board reading passes through.
    grew = M.note_board(board)
    if grew and board_and_mask._told_owned:
        print(f"[Sushi] grid is bigger than it was: {len(grew)} new slot(s), "
              f"{M.owned_count()} known in total")
    board_and_mask._told_owned = True
    # Learn digit shapes from every successful read.  The digit reader is not
    # driving anything yet -- this only fills its library, so that when it does
    # take over, new tiers need no labelling at all.  Cheap, and it cannot
    # affect the board that was just read.
    try:
        got = sushivision.harvest_digits(frame, board, scale)
        if got:
            digs = sorted(sushivision.load_digit_masks())
            print(f"[Sushi] learned {got} new digit variant(s); "
                  f"digits known: {''.join(digs)}")
    except Exception:
        pass

    # Count only what THIS read failed on.  Counting the whole folder reported
    # "118 cell(s) could not be read" on a board of 61 -- the folder had simply
    # accumulated across every previous run, and the number looked alarming
    # while nothing was actually wrong.
    unread = len(set(glob.glob(os.path.join(sushivision.UNKNOWN_DIR, "*.png")))
                 - before_dumps)
    # Only announce a CHANGE.  Printing on every read buried a 200-line run in
    # the same warning repeated eighty times.
    if unread and unread != board_and_mask._last_unread:
        board_and_mask._last_unread = unread
        # An unread cell is planned around as if empty, so it silently shrinks
        # the staircase and any drag aimed at it does nothing.  Say so.
        print(f"[Sushi] {unread} cell(s) could not be read - see "
              f"sushi_unknown/.  They count as EMPTY, so the plan will be "
              f"shorter than it should be.")
    return board, mask


board_and_mask._last_unread = 0
board_and_mask._last_occ = 0
# The first reading of a run teaches us most of the grid at once, and saying
# "48 new slots" then is noise.  Only growth after that is worth a line.
board_and_mask._told_owned = False


def eligible_runs(mask):
    """Contiguous chain-eligible stretches, longest first."""
    runs, start = [], None
    for i in range(len(mask) + 1):
        ok = i < len(mask) and mask[i] > -1
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            runs.append((start, i - 1, i - start))
            start = None
    return sorted(runs, key=lambda r: -r[2])


def choose_start(board, mask, need):
    """
    Slot to put the staircase HEAD on: topmost-leftmost workable position.

    Three constraints, and the third was learned the hard way:

      * the head must be OCCUPIED -- it is the slot you merge onto
      * the slots after it must be chain-eligible (Sushi[1] > -1)
      * they must also be OCCUPIED, because dragging onto an empty cell does
        not swap; the sushi stays put.  A plan that ran off the end of the
        filled region wasted five drags and left the board unaccountable.

    Earliest position wins, so the staircase is built from the top-left rather
    than wherever the longest eligible run happens to be.
    """
    best = None
    for head in range(len(board)):
        if board[head] < 0:
            continue
        run = 0
        while (head + 1 + run < len(board)
               and board[head + 1 + run] >= 0
               and mask[head + 1 + run] > -1):
            run += 1
        if run < 2:
            continue
        if best is None or run > best[1]:
            best = (head, run)
        if run >= need:                    # long enough; take the earliest
            return head
    return best[0] if best else None


def plan(start=None, mode="sort"):
    """
    Build a plan.  `mode` is "sort" (the whole board) or "staircase".

    Default is the full sort.  plan_full_sort was written for it and then not
    wired up, so --arrange kept running the 15-slot staircase and only touched
    the first row and a half.
    """
    board, mask = board_and_mask()
    if board is None:
        return None

    if mode == "sort":
        swaps, after = M.plan_full_sort(board)
        start = None
    else:
        want = M.target_staircase(board)
        if start is None:
            start = choose_start(board, mask, len(want))
            if start is None:
                print("[Sushi] no chain-eligible run long enough")
                return None
        swaps, after = M.plan_arrangement(board, start)
    uniq = max(v for v in board if v >= 0) + 6
    best = M.rank_moves(after, 60, uniq, 1, mask)[0]
    return {"board": board, "mask": mask, "start": start, "swaps": swaps,
            "after": after, "best": best,
            "before_best": M.rank_moves(board, 60, uniq, 1, mask)[0]}


def show(p):
    print("current board:\n" + M.render(p["board"]))
    where = (f"staircase head at slot {p['start']}"
             if p["start"] is not None else "full-board sort")
    print(f"\n{where}  ({len(p['swaps'])} swaps)")
    print("predicted board:\n" + M.render(p["after"]))
    n0, s0, t0 = p["before_best"]
    n1, s1, t1 = p["best"]
    print(f"\nbest chain now:   {n0:2d}  (slot {s0}, tier {t0})")
    print(f"best chain after: {n1:2d}  (slot {s1}, tier {t1})")


def execute(p, cam_rect, clicker, pause=None):
    """Perform the swaps.  Returns the number actually issued."""
    done = 0
    for i, (src, dst) in enumerate(p["swaps"], 1):
        x0, y0 = M.slot_to_xy(src)
        x1, y1 = M.slot_to_xy(dst)
        sx0 = cam_rect["left"] + int(round(x0 * cam_rect["scale"]))
        sy0 = cam_rect["top"] + int(round(y0 * cam_rect["scale"]))
        sx1 = cam_rect["left"] + int(round(x1 * cam_rect["scale"]))
        sy1 = cam_rect["top"] + int(round(y1 * cam_rect["scale"]))
        print(f"  [{i:2d}/{len(p['swaps'])}] slot {src:3d} -> {dst:3d}   "
              f"({sx0},{sy0}) -> ({sx1},{sy1})")
        drag(clicker, sx0, sy0, sx1, sy1)
        done += 1
        time.sleep(pause if pause is not None else random.uniform(0.16, 0.38))
    return done


def verify(p, settle=1.0):
    """
    Compare a fresh SCREEN read against the prediction.

    This used to wait up to four minutes for the save to flush, which made
    verification too expensive to do between batches -- so errors compounded
    silently.  Reading the screen makes it a one-second check.
    """
    print("\n[Sushi] verifying from the screen.")
    time.sleep(settle)
    for _attempt in range(3):
        board, _ = board_and_mask()
        if board:
            same = sum(1 for a, b in zip(board, p["after"]) if a == b)
            print(f"\nactual board:\n{M.render(board)}")
            print(f"\nmatches prediction in {same}/{len(board)} slots")
            if board == p["after"]:
                print("[Sushi] exact match - the swaps landed as planned.")
            else:
                diff = [(i, p['after'][i], board[i])
                        for i in range(len(board)) if p['after'][i] != board[i]]
                print(f"[Sushi] {len(diff)} slots differ, first few "
                      f"(slot, predicted, actual): {diff[:8]}")
                print("        NOT continuing from a board I cannot account for.")
            return
        time.sleep(1.0)
    print("[Sushi] could not read the board to verify - is the station on "
          "screen and focused?")


def read_fuel():
    """Fuel bar fill, 0.0-1.0, or None."""
    frame, scale = sushivision.grab_station()
    if frame is None:
        return None
    return sushivision.fuel_fraction(frame, scale)


def cook(rect, clicker, times=1, pause=None):
    """
    Press the cook button `times`, which spawns a sushi at the selected tier.

    Presses are fast but not metronomic: a short randomised gap, and the
    pointer stays on the button between them (Clicker.move skips a move it does
    not need, now that drag() keeps the cached position honest).
    """
    x, y = M.COOK_BUTTON
    sx = rect["left"] + int(round(x * rect["scale"]))
    sy = rect["top"] + int(round(y * rect["scale"]))
    for i in range(times):
        clicker.click_at(sx, sy, time.perf_counter() + 0.06)
        time.sleep(pause if pause is not None
                   else random.uniform(0.05, 0.14))
    return times


def cook_hold(rect, clicker, seconds=None):
    """
    Hold the cook button down; it auto-repeats and fills the grid.

    Simpler and far less input than one click per free cell, and it needs no
    count at all -- the game stops producing when the board is full or the fuel
    runs out, so the hold is self-limiting.
    """
    seconds = seconds if seconds is not None else random.uniform(4.0, 6.0)
    x, y = M.COOK_BUTTON
    sx = rect["left"] + int(round(x * rect["scale"]))
    sy = rect["top"] + int(round(y * rect["scale"]))
    press_hold(clicker, sx, sy, seconds)
    return seconds


def merge_loop(rect, clicker, rounds=40, pause=None):
    """
    Merge duplicate pairs, ONE AT A TIME, re-reading the board between each.

    Batching from a single read does not work.  Every merge changes the board --
    the destination gains a tier and the cascade promotes slots after it -- so
    the second and later pairs in a batch refer to tiers that no longer exist.
    Seen live: two tier-28 merges ran correctly, then a 29 pair was dragged onto
    a cell that the first merges had already turned into a 30.

    Simulating the batch forward would not fix it either: the game rolls a bonus
    second promotion on every merge, so the predicted board and the real one
    diverge unpredictably.  Re-reading is the only honest option, and at roughly
    a second per read it is cheap enough.
    """
    done = 0
    last_sig = None
    stuck = 0
    while done < rounds:
        board, _mask = board_and_mask()
        if board is None:
            break
        pairs = M.find_pair_merges(board)
        if not pairs:
            print("[Sushi] no run of 3+ adjacent left")
            break
        src, dst, tier = pairs[0]

        # If the same merge comes up again the board did not change, i.e. the
        # drag did not register.  Repeating it forever achieves nothing -- one
        # run issued `65 -> 68` three times in a row before moving on.
        sig = (src, dst, tier)
        if sig == last_sig:
            stuck += 1
            if stuck >= 2:
                print(f"[Sushi] merge {src} -> {dst} had no effect twice - "
                      f"stopping rather than repeating it.")
                break
        else:
            stuck = 0
        last_sig = sig
        x0, y0 = M.slot_to_xy(src)
        x1, y1 = M.slot_to_xy(dst)
        print(f"  [{done + 1:2d}/{rounds}] tier {tier:2d}: slot {src:3d} -> "
              f"{dst:3d}   ({len(pairs)} pairs available)")
        drag(clicker,
             rect["left"] + int(round(x0 * rect["scale"])),
             rect["top"] + int(round(y0 * rect["scale"])),
             rect["left"] + int(round(x1 * rect["scale"])),
             rect["top"] + int(round(y1 * rect["scale"])))
        done += 1
        time.sleep(pause if pause is not None else random.uniform(0.16, 0.38))

        # NO per-merge compaction here.
        #
        # It was tried and it makes things worse.  plan_compaction fills a hole
        # with the LAST sushi on the board, so after merging a run of 28s it
        # dragged a 28 from the far end into slot 0 -- splitting the very run
        # the next merge needed and leaving zero merges available.
        #
        # It is also unnecessary: the hole a merge leaves is always at the START
        # of the run it consumed, so the rest of that run stays contiguous.
        # Holes accumulate harmlessly and the sort+compact at the top of each
        # cycle clears them.
    return done


def _on_station():
    """True while the sushi station is still the visible screen."""
    frame, _scale = sushivision.grab_station()
    return frame is not None


def cycle_preview():
    """
    Print what --cycle would do, without touching anything.

    --plan is not a substitute: it previews the staircase/sort planner, which is
    a different code path from the cycle.  It does confirm the module loads and
    the board reads, but it will not show the merges.
    """
    board, _mask = board_and_mask()
    if board is None:
        return 1
    print("current board:")
    print(M.render(board))

    drags, after = M.plan_sort_and_compact(board)
    print()
    print(f"1. sort + compact : {len(drags)} drag(s)")
    print(M.render(after))

    pairs = M.find_pair_merges(after)
    print()
    print(f"2. merges         : {len(pairs)} run(s) of 3+ adjacent")
    for src, dst, tier in pairs[:10]:
        print(f"      tier {tier:2d}: drag {src:3d} -> {dst:3d}")
    if len(pairs) > 10:
        print(f"      ... and {len(pairs) - 10} more")
    print("   (recomputed after every merge, so this is only the first pass)")

    occ = sum(1 for v in board if v >= 0)
    print()
    print(f"3. cook           : until the board stops growing "
          f"(now {occ} occupied)")
    return 0


def top_up(rect, clicker, cooks=60, fuel_floor=0.02):
    """
    Spend whatever fuel is left filling the board, as a final step.

    Fuel regeneration scales with how many cells are OCCUPIED, so ending a run
    with a nearly empty board throttles everything that comes after it.  Merging
    trades occupancy for tier -- necessary, but it means the loop naturally ends
    on its emptiest board, which is the worst state to leave idle.

    So the last thing done is cook, not merge: convert leftover fuel into
    occupied cells and leave the station regenerating at its best rate.
    """
    fuel = read_fuel()
    if fuel is None:
        return 0
    print(f"[Cycle] final top-up, fuel {fuel * 100:.0f}%")
    board, _ = board_and_mask()
    occ = sum(1 for v in board if v >= 0) if board else 0
    done = 0
    for c in range(cooks):
        if not _on_station():
            print("[Cycle] not on the station - stopping top-up.")
            break
        cook(rect, clicker, times=1)
        done += 1

        # A cook that moves NEITHER the fuel bar NOR the board did not happen.
        #
        # Cost rises with tier -- a tier-27 press costs 66.7M -- so cooking
        # becomes unaffordable while the bar still shows fuel, and a
        # bar-fraction threshold cannot see that.  "Nothing changed" is the
        # honest test, and it needs no reading of the cost at all.
        new_fuel = read_fuel()
        board, _ = board_and_mask()
        new_occ = sum(1 for v in board if v >= 0) if board else occ
        moved = (new_occ > occ
                 or (new_fuel is not None and fuel is not None
                     and new_fuel < fuel - 0.01))
        if not moved:
            print(f"[Cycle] cook had no effect (fuel "
                  f"{(new_fuel or 0) * 100:.0f}%, board {new_occ}) - "
                  f"cannot afford another at this tier.")
            break
        fuel, occ = new_fuel, new_occ
        if done % 5 == 0:
            print(f"[Cycle] topped up {done}, board {occ} occupied, "
                  f"fuel {(fuel or 0) * 100:.0f}%")
    return done


def cycle(rect, clicker, rounds=3, merges=60, cooks=30,
          fuel_floor=0.25, should_stop=None):
    """
    merge -> compact -> cook, repeated.

    Order matters.  Merging punches holes (each merge empties its source), holes
    break cascades, and cooking refills from the base tier -- so compacting
    between merging and cooking is what keeps the block contiguous and the
    chains long.
    """
    # rounds=0 means run until nothing productive is left.  The natural stop is
    # a round that neither merges nor grows the board: with a low base tier and
    # fuel to spare that may never happen, which is the point.
    def stopping():
        return bool(should_stop and should_stop())

    r = 0
    idle_rounds = 0
    while rounds == 0 or r < rounds:
        if stopping():
            print("[Cycle] stopping.")
            return 0
        r += 1
        print()
        print(f"===== cycle {r}{'' if rounds == 0 else '/' + str(rounds)} =====")
        did_something = False

        # Sort and merge ALTERNATELY until neither does anything.
        #
        # One sort per cycle is not enough.  Merging a run of 28s leaves a 29
        # where the 28s were, which breaks descending order -- a real board
        # showed seven `28 then 29` pairs after a merge pass -- and every break
        # hides the runs of three that later merges depend on.  So re-sort as
        # soon as merging stalls, and merge again; only when a full pass yields
        # nothing is the board genuinely exhausted.
        for inner in range(1, 9):
            if stopping():
                print("[Cycle] stopping.")
                return 0
            board, _ = board_and_mask()
            if board is None:
                print("[Cycle] cannot read the board - stopping.")
                break
            drags, _after = M.plan_sort_and_compact(board)
            if drags:
                if not _on_station():
                    print("[Cycle] not on the station - aborting.")
                    return 1
                print(f"[Cycle] pass {inner}: sort + compact, "
                      f"{len(drags)} drag(s)")
                # Verify the FIRST drag actually moved something before
                # issuing the other fifty.  A whole run can otherwise complete
                # with the planner working perfectly and not one drag landing,
                # which looks identical to "nothing to do" from the outside.
                for i, (src, dst) in enumerate(drags):
                    # Between drags, not just between cycles: one cycle is up
                    # to eight passes of fifty drags, so a two-minute limit
                    # checked only at the top would run for many minutes.
                    if stopping():
                        print("[Cycle] stopping.")
                        return 0
                    x0, y0 = M.slot_to_xy(src)
                    x1, y1 = M.slot_to_xy(dst)
                    drag(clicker,
                         rect["left"] + int(round(x0 * rect["scale"])),
                         rect["top"] + int(round(y0 * rect["scale"])),
                         rect["left"] + int(round(x1 * rect["scale"])),
                         rect["top"] + int(round(y1 * rect["scale"])))
                    time.sleep(random.uniform(0.08, 0.20))
                    if i == 0:
                        chk, _ = board_and_mask()
                        if chk is not None and chk == board:
                            print(f"[Cycle] first drag ({src} -> {dst}) changed "
                                  f"NOTHING on the board.  Drags are not "
                                  f"registering - stopping rather than issuing "
                                  f"{len(drags) - 1} more.")
                            return 1
                        print(f"[Cycle] first drag landed - continuing.")

            n = merge_loop(rect, clicker, rounds=merges)
            print(f"[Cycle] pass {inner}: {n} merge(s)")
            did_something = did_something or bool(drags) or n > 0
            if n == 0 and not drags:
                break

        board, _ = board_and_mask()
        if board is None:
            break
        before = sum(1 for v in board if v >= 0)

        # Just press it a lot.
        #
        # The clever version -- press once, read the board, stop if it did not
        # grow -- threw away entire cook phases on a single flat reading taken
        # right after a merge pass, while the tank was full and 68 of 89 cells
        # were free.  Cycle 1 of the same run pressed 30 times and every one
        # landed, so the button was never the problem; the early exit was.
        #
        # A burst is self-correcting: presses that cannot afford fuel or find a
        # free cell simply do nothing, and an extra click costs nothing.
        # Randomised so the count is not identical every cycle.
        # Measured against the whole array, not against the slots known to be
        # owned.  The two uses of "capacity" want opposite errors: compaction
        # must never aim at a cell that might not exist, but this gate only
        # decides whether to bother holding a button that stops by itself.
        # Gating it on the known-owned count would also make new slots
        # undiscoverable -- the board would read as full, cooking would be
        # skipped, and the sushi that would have revealed the new cells would
        # never be made.  Cooking is how the grid grows into what was bought.
        room = max(0, M.BOARD_SLOTS - before)
        if room == 0:
            print(f"[Cycle] board at capacity ({before}/{M.BOARD_SLOTS})"
                  f" - skipping cook")
        else:
            # HOLD the button rather than counting presses.  It auto-repeats
            # and fills the grid, and stops on its own when the board is full
            # or fuel runs out -- so no count is needed and nothing is wasted.
            if not _on_station():
                print("[Cycle] no longer on the sushi station - stopping.")
                return 1
            held = cook_hold(rect, clicker)
            print(f"[Cycle] held cook {held:.1f}s "
                  f"({before} filled, {M.owned_count()} slot(s) known)")
            board, _ = board_and_mask()
            now = sum(1 for v in board if v >= 0) if board else before
            print(f"[Cycle] cooked: board {before} -> {now}")
            if now > before:
                did_something = True

        # Stop only when a whole round achieved nothing; one quiet round can
        # just mean the board needs re-sorting before the next merges appear.
        idle_rounds = 0 if did_something else idle_rounds + 1
        if idle_rounds >= 2:
            print("[Cycle] two rounds with no progress - finishing up.")
            top_up(rect, clicker)
            break
    return 0


def main():
    ap = argparse.ArgumentParser(description="Sushi Station executor")
    ap.add_argument("--plan", action="store_true", help="plan only, touch nothing")
    ap.add_argument("--arrange", action="store_true", help="perform the swaps")
    ap.add_argument("--start", type=int, default=None, help="staircase head slot")
    # default None, not 0: `--cycle 0` means "run indefinitely", and 0 is
    # falsy, so `if args.cycle:` silently fell through to the plan path and the
    # loop never started.
    ap.add_argument("--cycle", type=int, default=None, metavar="N",
                    help="run N rounds of sort+compact -> merge -> cook.  Use 0 to run indefinitely, until a round achieves nothing.")
    ap.add_argument("--cook", type=int, default=0, metavar="N",
                    help="press the cook button N times before anything else")
    ap.add_argument("--merge", action="store_true",
                    help="merge duplicate pairs, lowest tier first, re-reading the board between every merge")
    ap.add_argument("--rounds", type=int, default=40,
                    help="how many merges to attempt")
    ap.add_argument("--staircase", action="store_true",
                    help="build only the strictly-descending staircase instead "
                         "of sorting the whole board")
    ap.add_argument("--no-lock", action="store_true")
    args = ap.parse_args()

    if args.cycle is not None and args.plan:
        return cycle_preview()

    if args.cycle is not None:
        rect = gamewindow.acquire(lock=not args.no_lock, x=0, y=0)
        return cycle(rect, Clicker(), rounds=args.cycle)

    if args.merge or args.cook:
        rect = gamewindow.acquire(lock=not args.no_lock, x=0, y=0)
        if args.cook:
            n = cook(rect, Clicker(), times=args.cook)
            print(f"[Sushi] pressed cook {n} time(s).")
        if args.merge:
            n = merge_loop(rect, Clicker(), rounds=args.rounds)
            print(f"[Sushi] {n} merge(s) performed.")
        return 0

    p = plan(args.start, "staircase" if args.staircase or args.start
             is not None else "sort")
    if p is None:
        print("[Sushi] no board - is the game running and on the station?")
        return 1
    show(p)

    if not args.arrange:
        print("\n(plan only; pass --arrange to perform the swaps)")
        return 0

    rect = gamewindow.acquire(lock=not args.no_lock, x=0, y=0)
    print(f"\n[Window] canvas at ({rect['left']},{rect['top']}) "
          f"{rect['width']}x{rect['height']} scale={rect['scale']:.4f}\n")
    n = execute(p, rect, Clicker())
    print(f"\n[Sushi] {n} swaps issued.")
    verify(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
