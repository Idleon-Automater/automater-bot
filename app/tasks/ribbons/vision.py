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

# Fitted against the save rather than eyeballed.  Every (x0, dx, y0, dy) in a
# wide sweep was scored by how closely cells the save calls the same rank
# matched each other, and this one made them identical.
GRID_X0, GRID_DX = 20.0, 46.00
GRID_Y0, GRID_DY = 57.0, 44.25

# Half-width of the patch compared per cell.  Inside the cell's border, so a
# neighbouring slot's artwork can never leak in.
CELL_HALF = 15

# Below this a cell is empty.  Measured: an empty slot is flat dark wood and an
# occupied one carries a bright sprite, so the two populations are far apart --
# this found all ten empty slots and no false ones.
EMPTY_STD = 26.0

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
SIGN_AREA = (1100, 2400)         # pixels of pale interior
SIGN_MAX_CY = 220                # hung high; anything lower is furniture
SIGN_GOLD_MIN = 0.08             # fraction of the surrounding ring that is gold
_SIGN_RING = 8                   # how far outside the blob to look for gold

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
    The shelf as a list of 28 signatures, None for an empty slot.

    A signature is only ever compared with another signature.  It deliberately
    says nothing about which rank it is, because nothing here needs to know
    and pretending to know would mean shipping twenty sprites that go stale
    the moment the game adds a twenty-first.
    """
    out = []
    for slot in range(SLOTS):
        p = _patch(frame, slot, scale)
        if p is None or float(p.std()) < EMPTY_STD:
            out.append(None)
        else:
            out.append(hashlib.md5(np.ascontiguousarray(p).tobytes()).hexdigest())
    return out


def find_pairs(shelf):
    """
    Merges worth making, as (source, destination) slots.

    Destination is the LOWER slot and source the higher, so the shelf packs
    towards its start and the empties collect at the end -- which is where the
    game puts new ribbons, and keeps the shelf from fragmenting.

    One pair per rank per pass, never two.  A merge changes what the shelf
    holds, and the result can be one rank up or two, so any second pair
    planned from the same reading may already be wrong.  The caller re-reads.
    """
    seen = {}
    pairs = []
    for slot, sig in enumerate(shelf):
        if sig is None:
            continue
        if sig in seen:
            pairs.append((slot, seen.pop(sig)))
        else:
            seen[sig] = slot
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
        out.append((gold, (int(round(cent[i][0] / scale)),
                           int(round(cent[i][1] / scale)))))
    out.sort(reverse=True)
    return out


def find_menu_sign(frame, scale=1.0, min_gold=SIGN_GOLD_MIN):
    """Where the cooking MENU signboard is in World 4 town, or None."""
    for gold, xy in _sign_candidates(frame, scale):
        if gold >= min_gold:
            return xy
    return None


def menu_sign_score(frame, scale=1.0):
    """Best gold-ring fraction found -- for logs when the sign is missed."""
    cands = _sign_candidates(frame, scale)
    return cands[0][0] if cands else 0.0


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
