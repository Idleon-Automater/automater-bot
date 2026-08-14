#!/usr/bin/env python3
"""
Sushi Station board model, transcribed from `N.js`.

Every rule here is cited to the bundle; see SUSHI_NOTES.md for the source
snippets and offsets.  Nothing in this file was inferred from watching the
screen -- that route produced three wrong guesses about the board layout before
the source settled it.

    board   list of 120 ints, tier-1 per slot, -1 empty, 15 columns
    merge   slot += 1, capped at MAX_TIER, then a CHANCE of a second +1
    chain   after a merge, the maximal strictly-descending occupied run
            starting at slot+1 (max 25) each get +1 as well

THE MODEL IS NOT DETERMINISTIC.  Two rolls make it stochastic:

  * the fireplace bonus, a second promotion on a merge
  * the cook bonus tier

so a plan simulated forward WILL drift from reality.  `expected_gain` exists to
rank candidate moves; anything that acts on a plan must re-read the board and
verify rather than assume.  That discipline is what stopped the darts bot from
compounding its own errors, and it matters more here because the drift is
built into the game rather than into our measurement.
"""

GRID_COLS = 15
# The game's array is 120 long, but only some of those cells are OWNED and
# usable.  Unowned cells look empty and silently swallow every drag aimed at
# them -- five compaction drags into slots 72-78 did nothing for exactly this
# reason, and cooking "fails" when the owned region is full even though the
# array has room.
#
# Grows with upgrades, so it is a setting rather than a constant.  89 as of the
# last check; raise it when you buy more slots.
BOARD_SLOTS = 120             # array size, not capacity
OWNED_SLOTS = 89              # cells actually usable
CHAIN_MAX = 25


def chain_from(board, slot, eligible=None):
    """
    Slots that a merge at `slot` would cascade into.

    Walks forward by raw index, so it crosses row boundaries -- the "wrapping"
    described in play is just contiguous indexing over a 15-wide grid.

    `eligible` is Sushi[1]: a slot only chains when its entry is > -1.  When it
    is not supplied every occupied slot is treated as eligible, which
    OVERESTIMATES the chain.  Pass the real mask before trusting a plan.
    """
    # `board` must already have the head incremented.  The game does
    # `Sushi[0][slot] += 1` and THEN walks the chain, so the first comparison
    # uses the PROMOTED value.  Comparing against the pre-merge value made
    # `30 30 30 29` stop dead (30 < 30 is false) and report one promotion where
    # the game gives three: the head becomes 31, so the next 30 is now strictly
    # less and chains, and the 29 after it chains off that.
    out = []
    for i in range(CHAIN_MAX):
        nxt = slot + 1 + i
        if nxt >= len(board):
            break
        prev_tier = board[slot + i]
        if not (0 <= board[nxt] < prev_tier):
            break
        if eligible is not None and not (eligible[nxt] > -1):
            break
        out.append(nxt)
    return out


def can_chain(board, slot, unique_sushi, chain_upgrade_lv):
    """
    Whether a merge at `slot` cascades at all.

    Three gates from the source, and the middle one is easy to miss: a merge
    only chains while its tier sits at least 5 BELOW the best-ever tier, so
    chaining stops helping exactly where the board is most valuable.
    """
    if chain_upgrade_lv < 1:
        return False
    return board[slot] < unique_sushi - 5


def apply_merge(board, slot, max_tier, unique_sushi, chain_upgrade_lv,
                eligible=None, fireplace=False):
    """
    Apply one merge and its cascade.  Returns a NEW board.

    `fireplace` applies the bonus second promotion deterministically; the real
    game rolls for it per merge.  Left as a flag rather than a random draw so
    callers can bound the outcome (best case / worst case) instead of sampling
    a single future that will not happen.
    """
    b = list(board)

    def bump(i):
        if b[i] >= 0:
            b[i] = min(max_tier, b[i] + 1)
            if fireplace:
                b[i] = min(max_tier, b[i] + 1)

    chained = can_chain(b, slot, unique_sushi, chain_upgrade_lv)
    bump(slot)                     # head first: the chain tests against it
    if chained:
        for i in chain_from(b, slot, eligible):
            bump(i)
    return b


def rank_moves(board, max_tier, unique_sushi, chain_upgrade_lv, eligible=None):
    """
    Every slot that could be merged, best first, scored by chain length.

    Score is simply how many slots gain a tier, which is the honest first-order
    payoff.  It does NOT weight by tier value -- promoting a run of tier-40s is
    worth far more than a run of tier-4s -- so this is a starting point for a
    planner, not the planner itself.
    """
    out = []
    for s, t in enumerate(board):
        if t < 0:
            continue
        n = 1 + (len(chain_from(board, s, eligible))
                 if can_chain(board, s, unique_sushi, chain_upgrade_lv) else 0)
        out.append((n, s, t + 1))          # +1 -> displayed tier
    out.sort(reverse=True)
    return out


def render(board):
    """The board as the screen shows it: 15 wide, displayed tiers."""
    rows = []
    for r in range(0, len(board), GRID_COLS):
        rows.append(" ".join(".." if v < 0 else f"{v + 1:2d}"
                             for v in board[r:r + GRID_COLS]))
    return "\n".join(rows)


if __name__ == "__main__":
    import sushi_watch
    b = sushi_watch.read_board()
    if b is None:
        raise SystemExit("no board - is the game running?")
    print(render(b), "\n")
    # UniqueSushi and the chain upgrade are not read yet; assume chaining is on
    # and the best-ever tier is above everything present, so chains are allowed.
    uniq = max([v for v in b if v >= 0], default=0) + 6
    print("best merges (chain length, slot, displayed tier):")
    for n, s, t in rank_moves(b, 60, uniq, 1)[:8]:
        print(f"   chain {n:2d}  slot {s:3d} (row {s // GRID_COLS}, "
              f"col {s % GRID_COLS})  tier {t}")


# ── Arranging the staircase ───────────────────────────────────────────────────
#
# Dragging swaps two slots, so any target arrangement is reachable by a sequence
# of swaps.  The target is NOT a plain descending sort.
#
# The chain test is STRICTLY less (`board[next] < board[prev]`), so gaps are
# harmless -- 42 40 35 30 chains fine -- but duplicates stop it dead.  Measured
# on a real board of 88 sushi at tier >= 28: a plain descending sort chains 1
# slot, because it opens `43 43`.  One of each tier, strictly descending, chains
# 16.  The 71 duplicates are not waste; they are the fuel dropped on the head.
#
# And the structure is self-sustaining: dropping a head-tier sushi on
# `20 | 19 18 17 16 15` yields `20 20 19 18 17 16` -- the same shape one tier up.

# Stored tier below which sushi are ignored.  Displayed = stored + 1.
#
# This used to be hardcoded, which silently excluded the base tier the moment
# the cook tier was lowered: with the button set to 27 every freshly cooked
# sushi was invisible to both the sorter and the merger.
#
# `base_tier()` derives it from the board instead -- the lowest tier present IS
# whatever is being cooked, because that is what cooking adds -- so it follows
# the button without needing to read it.
# No fixed floor.  This was 26 (stored; displayed 27) and silently excluded
# every sushi below it -- so when the cook tier was lowered to 25 the bot
# reported "no run of 3+ adjacent left" against a board that was almost
# entirely 25s and 26s.  base_tier() derives the floor from the board, which
# follows the cook tier wherever it goes.
MIN_USEFUL_TIER = 0


def base_tier(board, floor=MIN_USEFUL_TIER):
    """Lowest tier actually on the board, never below `floor`."""
    present = [v for v in board if v >= 0]
    return max(floor, min(present)) if present else floor


def target_staircase(board, min_tier=None):
    """The strictly-descending tier sequence to build, highest first."""
    if min_tier is None:
        min_tier = base_tier(board)
    return sorted({v for v in board if v >= min_tier}, reverse=True)


def plan_arrangement(board, start_slot, min_tier=None):
    """
    Swaps that build the staircase at `start_slot`.  Returns (swaps, board).

    Selection sort by swaps: for each target position take a slot that already
    holds the wanted tier, preferring one already in place so it costs nothing.
    Sources are taken from OUTSIDE the target region first, so the staircase is
    not cannibalised while it is being built.

    The target region is TRUNCATED at the first empty slot.  Dragging onto an
    empty cell does not swap -- the sushi simply stays where it was.  Learned
    live: a 17-swap plan whose last five targets were empty cells left five
    source sushi untouched and five staircase positions blank, and the verify
    step then refused the board because it could not account for 14 slots.
    """
    b = list(board)
    want = target_staircase(b, min_tier)

    room = 0
    while (room < len(want)
           and start_slot + room < len(b)
           and b[start_slot + room] >= 0):
        room += 1
    want = want[:room]
    if not want:
        return [], b
    region = range(start_slot, start_slot + len(want))

    swaps = []
    for pos, tier in zip(region, want):
        if b[pos] == tier:
            continue
        src = next((s for s in range(len(b))
                    if b[s] == tier and s not in region), None)
        if src is None:
            src = next((s for s in range(len(b))
                        if b[s] == tier and s > pos), None)
        if src is None:
            continue                       # tier already consumed; skip
        b[pos], b[src] = b[src], b[pos]
        swaps.append((src, pos))
    return swaps, b


# ── Slot to screen ────────────────────────────────────────────────────────────
#
# Derived from recording 1786187091_full by detecting sushi sprites and fitting
# the spacing of their centres, not by measuring one screenshot by hand.
#
# The fit gives 15 columns x 8 rows = 120 slots, which matches `slot % 15` and
# `for (n = 0; n < 120; n++)` in N.js.  Geometry recovered from pixels agreeing
# with geometry recovered from the bundle is the strongest check available here.
#
# CELL_H was originally fitted at 46.34 from sprite centroids, but sprites are
# not centred in their cells and the residual accumulated down the grid: rows 0
# and 1 read perfectly while rows 2+ read almost nothing.  Re-measured by
# sweeping (GRID_Y0, CELL_H) and counting cells whose tier actually matched a
# template -- 30/120 at the old values, 83/120 at these.  Fitting to the thing
# that matters beats fitting to a proxy.
#
# Game coordinates (960x540 canvas).  Convert with the window scale and add the
# window origin before clicking.

CELL_W = 46.97
CELL_H = 46.95
GRID_X0 = 49.3        # centre of column 0
GRID_Y0 = 53.0        # centre of row 0
GRID_ROWS = 8


def slot_to_xy(slot):
    """Centre of `slot` in game coordinates."""
    if not 0 <= slot < BOARD_SLOTS:
        raise ValueError(f"slot {slot} outside 0..{BOARD_SLOTS - 1}")
    col, row = slot % GRID_COLS, slot // GRID_COLS
    return (GRID_X0 + CELL_W * col, GRID_Y0 + CELL_H * row)


def xy_to_slot(x, y):
    """Inverse, for checking a detection against the model.  None if outside."""
    col = round((x - GRID_X0) / CELL_W)
    row = round((y - GRID_Y0) / CELL_H)
    if not (0 <= col < GRID_COLS and 0 <= row < GRID_ROWS):
        return None
    return row * GRID_COLS + col


def plan_full_sort(board, min_tier=None):
    """
    Swaps that order every sushi, highest first, reading top-left to
    bottom-right.  Returns (swaps, board).

    Uses CYCLE DECOMPOSITION, not selection sort.  Selection sort displaces a
    sushi into the source slot, which then often needs moving again, so a sushi
    that belongs at the far end shuffles across the board one cell at a time.
    Decomposing the permutation into cycles moves every sushi straight to its
    final slot: a cycle of length k costs exactly k-1 swaps, and nothing is
    touched twice.

    Only OCCUPIED cells are permuted -- dragging onto an empty cell is a
    separate operation (see plan_compaction).
    """
    b = list(board)
    slots = [s for s in range(len(b))
             if b[s] >= 0 and (min_tier is None or b[s] >= min_tier)]
    if not slots:
        return [], b

    # Target: slots in index order hold tiers in descending order.  Ties are
    # broken by current position so already-placed sushi tend to stay put.
    order = sorted(slots, key=lambda s: (-b[s], s))
    target = {dst: src for dst, src in zip(slots, order)}

    swaps = []
    pos = {s: s for s in slots}          # where each original sushi sits now
    at = {s: s for s in slots}           # what sits at each slot now
    for dst in slots:
        want = target[dst]
        cur = at[dst]
        if cur == want:
            continue
        src = pos[want]
        swaps.append((src, dst))
        b[dst], b[src] = b[src], b[dst]
        at[dst], at[src] = want, cur
        pos[want], pos[cur] = dst, src
    return swaps, b


def plan_sort_and_compact(board, min_tier=None):
    """
    Order every sushi by tier AND close the holes, as one sequence of drags.

    Holes matter beyond tidiness: the cascade stops at the first empty slot, so
    a hole in the middle of the block caps every chain that would have crossed
    it.  Merging punches holes (each merge empties its source), so sorting
    without compacting leaves the board steadily less mergeable.

    Compaction runs FIRST so the sort has a contiguous region to order.
    """
    drags, b = plan_compaction(board)
    swaps, b = plan_full_sort(b, min_tier)
    return drags + swaps, b


def plan_compaction(board):
    """
    Drags that pull sushi back into empty cells, so the block stays contiguous.

    Returns (drags, board).  Each drag moves the LAST occupied sushi into the
    EARLIEST empty cell before it, which closes gaps without disturbing order
    any more than necessary.

    A merge empties its source cell, so a merging run steadily punches holes in
    the block -- and holes break the cascade, because the chain stops at the
    first unoccupied slot.  Compacting between rounds is what keeps the chains
    long.

    NOTE: dragging onto an empty cell is not yet confirmed to register.  Five
    such drags failed once, but those targeted slots beyond the owned grid --
    cells that do not exist -- rather than real empty ones.  Verify the first
    compaction against the screen before trusting a long run of them.
    """
    b = list(board)
    drags = []
    while True:
        holes = [i for i in range(OWNED_SLOTS) if b[i] < 0]
        occupied = [i for i in range(OWNED_SLOTS) if b[i] >= 0]
        if not holes or not occupied:
            break
        hole = holes[0]
        last = occupied[-1]
        if last < hole:
            break                       # every hole is past the block already
        b[hole], b[last] = b[last], b[hole]
        drags.append((last, hole))
    return drags, b


# ── Cook button ───────────────────────────────────────────────────────────────
# The flame button in the right-hand panel (reads FULL when fuel is capped, but
# the position does not move).  Located from a live frame, not guessed.
COOK_BUTTON = (931, 176)


def find_pair_merges(board, min_tier=None, unique_sushi=None,
                     require_chain=True):
    """
    Merges worth making, best first: (src, dst, tier).

    TWO different rules, because the game has two regimes:

      * Tiers more than 5 below the best-ever tier CASCADE.  A run of three
        promotes two sushi for one drag, so three adjacent is the bar -- a pair
        would spend two to promote one.  Lowest tier first, so 26s feed 27s
        feed 28s.

      * The top few tiers CANNOT cascade (`tier < UniqueSushi - 5` fails), so
        there is no cascade to wait for.  TWO adjacent is enough, and they go
        FIRST: raising the highest tier is the main driver of income, and a
        pair sitting at the top earns nothing while it waits for a third that
        may never come.

    Ordering is therefore: top-tier pairs (highest first), then cascading runs
    (lowest first).
    """
    if unique_sushi is None:
        present = [v for v in board if v >= 0]
        unique_sushi = max(present) if present else 0
    if min_tier is None:
        min_tier = base_tier(board)
    ceiling = unique_sushi - 5          # at or above this, no cascade

    top, deep = [], []
    i = 0
    while i < len(board):
        t = board[i]
        if t < min_tier:
            i += 1
            continue
        j = i
        while j + 1 < len(board) and board[j + 1] == t:
            j += 1
        run = j - i + 1
        if t >= ceiling:
            if run >= 2:
                top.append((i, t))
        elif run >= 3:
            deep.append((i, t))
        i = j + 1

    top.sort(key=lambda r: (-r[1], r[0]))      # highest tier first
    deep.sort(key=lambda r: (r[1], r[0]))      # lowest tier first
    return [(i, i + 1, t + 1) for i, t in top + deep]


def apply_drag_merge(board, src, dst, max_tier, unique_sushi,
                     chain_upgrade_lv=1, mask=None):
    """
    Board after dragging `src` onto same-tier `dst`.  Returns a NEW board.

    Fireplace and cook bonuses are NOT modelled -- they are random, so this is
    the FLOOR of what the merge achieves, never the expectation.
    """
    b = list(board)
    if b[src] != b[dst] or b[src] < 0:
        raise ValueError(f"slots {src},{dst} are not the same tier")
    b[src] = -1
    return apply_merge(b, dst, max_tier, unique_sushi, chain_upgrade_lv, mask)
