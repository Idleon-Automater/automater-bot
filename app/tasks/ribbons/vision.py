#!/usr/bin/env python3
"""
Read the ribbon shelf off the cooking screen.

NO TEMPLATE PER RIBBON, AND NONE IS NEEDED
------------------------------------------
Merging only ever asks which ribbons MATCH EACH OTHER.  It never needs to know
that a ribbon is a rank 14 -- only that the thing in slot 2 is the same as the
thing in slot 9.  So the shelf is read by comparing cells to each other, and
the twenty rank sprites are not shipped at all.

That this works is measured, not assumed.  Ribbons of the same rank render
PIXEL-IDENTICALLY: taking the save's own record of the shelf as the answer,
the three duplicate groups in it -- slots 13/14, slots 12/15/17, slots 11/16 --
came back with a mean absolute pixel difference of 0.00 against the fitted
grid, every distinct rank had a distinct signature, and all ten empty slots
were found.  A hash per cell is therefore enough, and it costs nothing.

The happy consequence is the same one the sushi digit reader ended up with: a
rank nobody has reached yet needs no work.  Rank 30 will merge with rank 30 on
the day it exists.

WHY THE SAVE IS NOT USED FOR THIS
---------------------------------
It holds the shelf exactly -- Ribbon[0..27], 0 for an empty slot -- and that
is how the grid below was fitted and checked.  But it flushes only every
~175 s, and a merge changes the shelf immediately, so planning a second merge
from it would be planning against a shelf that no longer exists.  Same split
as the sushi board: pixels for what moves, the save for what does not.
"""

import hashlib
import os

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV_DIR = os.path.join(_HERE, "nav")

# The shelf: 4 across, 7 down, 28 slots, read left to right and top to bottom
# in the same order as Ribbon[] in the save.
COLS, ROWS = 4, 7
SLOTS = COLS * ROWS

# Fitted against the save rather than eyeballed: every (x0, dx, y0, dy) in a
# wide sweep, scored by how closely cells the save calls the same rank match.
#
# Refitted once, and the first fit is a lesson.  It used a shelf whose only
# duplicates sat in rows 2 to 4, so it never tested whether a rank in row 1
# matches the same rank in row 4 -- and dy came out 44.25 instead of 44.000.
# A quarter of a pixel per row is a whole pixel by row 4, which is enough to
# change the sampled patch, and a live run duly merged rank 6 in slots 12 and
# 18 while leaving the third rank 6 in slot 5 untouched.  The refit uses a
# shelf with duplicates spanning rows 1, 2, 3, 4 and 5 and scores 0.000.
GRID_X0, GRID_DX = 20.0, 46.00
GRID_Y0, GRID_DY = 57.5, 44.00

# Half-width of the patch compared per cell.  Inside the cell's border, so a
# neighbouring slot's artwork can never leak in.
CELL_HALF = 15

# Below this a cell is empty.  Measured: an empty slot is flat dark wood and an
# occupied one carries a bright sprite, so the two populations are far apart --
# this found all ten empty slots and no false ones.
EMPTY_STD = 26.0

# HOW CLOSE TWO CELLS HAVE TO BE TO COUNT AS THE SAME RIBBON
#
# The first version demanded they be IDENTICAL, which worked until the grid
# was a quarter-pixel out and then failed silently by finding fewer pairs.
# Exactness is the wrong tool when the thing being compared is sampled from a
# fitted grid.
#
# Measured on a shelf of 21 ribbons with the corrected grid, allowing a pixel
# of slide: every same-rank pair scores 0.00 and the closest DIFFERENT-rank
# pair scores 37.71, out of 210 comparisons.  A threshold of 12 sits nowhere
# near either population, so sub-pixel drift can no longer cost a merge.
SAME_TOL = 12.0

# THE MENU SIGNBOARD IS FOUND BY COLOUR, NOT BY TEMPLATE
# ------------------------------------------------------
# It SWAYS.  A template cut from it matched 1.000 against the capture it came
# from and 0.24 against another capture of the same sign a few feet away --
# which is the score for "not on screen", and is exactly what a live run
# reported.  Template matching does not survive rotation, and this is the third
# time an animated thing has been templated in this project (the Equinox mirror
# glass, the summoning runes, now this).
#
# What does not change when it sways: it is a pale board inside a gold frame,
# hung high on a purple wall.  So look for a pale blob of about the right size,
# taller than it is wide, in the upper part of the screen, with gold around it.
# Measured across six captures -- the real sign scores 0.15 and 0.17 for the
# gold ring, and the two pale things that pass every other test score 0.004 and
# 0.002.
# The gold ring alone is NOT enough, which a live run proved by clicking
# something at (300, 84) that was not the sign.  Other players stand in this
# town, and a character with a bright aura is a pale blob with plenty of gold
# around it -- measured on a capture that happens to contain one, it scores
# 0.379 for gold against the real sign's 0.153.
#
# What the sign has and a player does not is DARK CONTENT INSIDE: the word
# MENU, three curls of steam and a bowl, filling about a third of a pale
# board.  Measured: the sign reads 0.29 and 0.32 dark, and every other pale
# blob across eight captures reads 0.18 or less.
# Sized to survive being PARTLY COVERED.  Other players stand in this town and
# one walked in front of the board, which broke the pale interior into pieces
# too small to qualify -- the run reported dark=0.00 gold=0.00, meaning no
# candidate at all, and gave up on a board that was still perfectly clickable.
#
# Simulated by pasting a real player sprite over the sign at four offsets: the
# surviving fragment reads 0.21 to 0.25 dark, against 0.16 to 0.17 for the
# player themselves. So the floor comes down to 0.20, which is a thinner
# margin than the unoccluded 0.29 allowed and is worth it -- clicking a player
# by mistake opens nothing and costs a retry, while failing to find the sign
# costs the whole task.
SIGN_AREA = (550, 2600)          # pixels of pale interior
SIGN_MAX_CY = 220                # hung high; anything lower is furniture
SIGN_GOLD_MIN = 0.08             # fraction of the surrounding ring that is gold
SIGN_DARK_MIN = 0.20             # fraction of the board that is lettering
_SIGN_RING = 8                   # how far outside the blob to look for gold
_SIGN_DARK_V = 110               # below this counts as lettering

# Proof that the cooking screen is open.  The RIBBON SHELF heading: 1.000 on
# that screen and at most 0.218 against the town, the summoning grid and the
# familiar panel.
SHELF_TITLE = "ribbon_shelf_title.png"


def _load(name):
    path = os.path.join(NAV_DIR, name)
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"missing template: {path}")
    return img


def slot_xy(slot):
    """Centre of a shelf slot, in game coordinates."""
    if not 0 <= slot < SLOTS:
        raise ValueError(f"slot {slot} outside 0..{SLOTS - 1}")
    return (GRID_X0 + GRID_DX * (slot % COLS),
            GRID_Y0 + GRID_DY * (slot // COLS))


def _patch(frame, slot, scale=1.0):
    cx, cy = slot_xy(slot)
    h = int(round(CELL_HALF * scale))
    x = int(round(cx * scale))
    y = int(round(cy * scale))
    p = frame[max(0, y - h):y + h, max(0, x - h):x + h]
    return p if p.shape[0] == 2 * h and p.shape[1] == 2 * h else None


def read_shelf(frame, scale=1.0):
    """
    The shelf as a list of 28 cell patches, None for an empty slot.

    Patches rather than hashes.  Nothing here needs to know that a ribbon is a
    rank 14 -- only whether two cells hold the same thing -- and keeping the
    pixels lets that question be answered with a tolerance instead of demanding
    they be identical.
    """
    out = []
    for slot in range(SLOTS):
        p = _patch(frame, slot, scale)
        if p is None or float(p.std()) < EMPTY_STD:
            out.append(None)
        else:
            out.append(np.ascontiguousarray(p).astype(np.int16))
    return out


def same_ribbon(a, b, tol=SAME_TOL):
    """Whether two cell patches hold the same ribbon, allowing a pixel of slide."""
    if a is None or b is None:
        return False
    if not hasattr(a, "shape") or not hasattr(b, "shape"):
        return a == b          # lets a test hand this plain labels
    if a.shape != b.shape:
        return False
    for dy in (0, -1, 1):
        for dx in (0, -1, 1):
            shifted = b if (dy or dx) == 0 else np.roll(np.roll(b, dy, 0), dx, 1)
            if float(np.abs(a - shifted).mean()) <= tol:
                return True
    return False


def _cell_key(p):
    if p is None:
        return None
    if not hasattr(p, "tobytes"):
        return p              # lets a test hand this plain labels
    return hashlib.md5(np.ascontiguousarray(p).tobytes()).hexdigest()


def shelf_key(shelf):
    """A hashable summary, for asking whether the shelf has stopped changing."""
    return tuple(_cell_key(p) for p in shelf)


def find_pairs(shelf):
    """
    Merges worth making, as (source, destination) slots.

    Destination is the LOWER slot and source the higher, so the shelf packs
    towards its start and the empties collect at the end.

    One pair per rank per pass, never two.  A merge changes what the shelf
    holds, and the result can be one rank up or two, so any second pair
    planned from the same reading may already be wrong.  The caller re-reads.
    """
    pairs = []
    taken = set()
    for slot in range(SLOTS):
        if shelf[slot] is None or slot in taken:
            continue
        for other in range(slot + 1, SLOTS):
            if shelf[other] is None or other in taken:
                continue
            if same_ribbon(shelf[slot], shelf[other]):
                pairs.append((other, slot))
                taken.add(slot)
                taken.add(other)
                break
    return pairs


def occupied(shelf):
    return sum(1 for s in shelf if s is not None)


def _match(frame, tpl, scale=1.0):
    if scale != 1.0:
        tpl = cv2.resize(tpl, (max(1, int(round(tpl.shape[1] * scale))),
                               max(1, int(round(tpl.shape[0] * scale)))),
                         interpolation=cv2.INTER_AREA)
    if frame.shape[0] < tpl.shape[0] or frame.shape[1] < tpl.shape[1]:
        return 0.0, None
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), (int(round((loc[0] + tpl.shape[1] // 2) / scale)),
                          int(round((loc[1] + tpl.shape[0] // 2) / scale)))


def _sign_candidates(frame, scale=1.0):
    """Every pale-in-gold board on screen, best gold ring first."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    pale = ((hsv[:, :, 2] > 150) & (hsv[:, :, 1] < 60)).astype(np.uint8)
    # The HUD along the bottom is full of pale panels and is never the sign.
    pale[int(420 * scale):, :] = 0
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(pale, 8)
    out = []
    lo, hi = SIGN_AREA[0] * scale * scale, SIGN_AREA[1] * scale * scale
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not lo <= area <= hi or h <= w or cent[i][1] > SIGN_MAX_CY * scale:
            continue
        m = int(round(_SIGN_RING * scale))
        ring = hsv[max(0, y - m):y + h + m, max(0, x - m):x + w + m]
        if ring.size == 0:
            continue
        ring = ring.reshape(-1, 3)
        gold = float(((ring[:, 0] >= 10) & (ring[:, 0] <= 30)
                      & (ring[:, 1] > 60) & (ring[:, 2] > 120)).mean())
        board = hsv[y:y + h, x:x + w]
        dark = float((board[:, :, 2] < _SIGN_DARK_V).mean())
        out.append((dark, gold, (int(round(cent[i][0] / scale)),
                                 int(round(cent[i][1] / scale)))))
    out.sort(reverse=True)
    return out


def find_menu_sign(frame, scale=1.0):
    """Where the cooking MENU signboard is in World 4 town, or None."""
    for dark, gold, xy in _sign_candidates(frame, scale):
        if gold >= SIGN_GOLD_MIN and dark >= SIGN_DARK_MIN:
            return xy
    return None


def menu_sign_score(frame, scale=1.0):
    """(dark, gold) for the best candidate -- for logs when the sign is missed."""
    cands = _sign_candidates(frame, scale)
    return (cands[0][0], cands[0][1]) if cands else (0.0, 0.0)


def shelf_title_score(frame, scale=1.0):
    """Best score for the RIBBON SHELF heading -- for logs when it is missed."""
    return _match(frame, _load(SHELF_TITLE), scale)[0]


def on_cooking_screen(frame, scale=1.0, min_score=0.70):
    """
    Whether the cooking screen with the ribbon shelf is open.

    Answered by the heading, not by the shelf.  The first version asked
    whether any slot in the fitted grid held something -- which is true of
    almost any picture, and it duly returned True for the town, the summoning
    grid and a menu panel.  A test that cannot fail is not a test.
    """
    return shelf_title_score(frame, scale) >= min_score
