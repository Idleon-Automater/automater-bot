"""
Reading the World 3 Equinox screen.

EVERYTHING HERE WAS MEASURED, NOT ESTIMATED
-------------------------------------------
From `_dev/equinox_upgrades.png`, a real capture at 960x540 with the bar full:

  * The thirteen dream tiles are cream squares on a night background, found by
    thresholding and connected components.  They came out on an exact lattice:
    column centres at x = 108 + 101*c for c in 0..6, row centres at
    y = 332 + 89*r for r in 0..1.  The fourteenth slot is a padlock and is not
    cream, which is why only thirteen were detected -- matching the thirteen
    dreams exactly.
  * The fill bar sits at y = 260..276, x = 66..758.
  * The info panel is the dark brown box on the right.

WHY THE BAR IS READ BY COLOUR AND NOT BY ITS NUMBERS
----------------------------------------------------
The bar prints "8,230/730,265" when filling and "BAR FULL" when done.  Reading
those digits would mean another glyph reader, and the darts wind reader is a
standing reminder of how much work that is to get right.  It is unnecessary
here: when the bar is full it is filled to its right-hand end, so sampling the
last stretch of the track answers the question with no text at all.
"""

import os

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(_HERE, "nav")

# ── The dream grid ────────────────────────────────────────────────────────────
# Measured lattice.  Thirteen dreams; the fourteenth slot is a padlock.
GRID_X0, GRID_DX = 108, 101
GRID_Y0, GRID_DY = 332, 89
GRID_COLS = 7

# In grid order: row 1 left to right, then row 2.  This IS the dropdown's list,
# so its order is load-bearing -- position 11 must stay Equinox Symbols.
DREAMS = [
    "Equinox Dreams", "Equinox Resources", "Shades of K", "Liquidvestment",
    "Matching Scims", "Slow Roast Wiz", "Laboratory Fuse",
    "Metal Detector", "Faux Jewels", "Food Lust", "Equinox Symbols",
    "Voter Rights", "Nonstop Studies",
]


def dream_xy(name):
    """Game coordinates of a dream's tile, or None if the name is unknown."""
    if name not in DREAMS:
        return None
    i = DREAMS.index(name)
    r, c = divmod(i, GRID_COLS)
    return GRID_X0 + GRID_DX * c, GRID_Y0 + GRID_DY * r


# ── The fill bar ──────────────────────────────────────────────────────────────
BAR_Y0, BAR_Y1 = 262, 274
BAR_X0, BAR_X1 = 66, 758

# Sampled from the full bar: BGR (185, 232, 254), uniform from x=100 to x=700.
BAR_FULL_BGR = (185, 232, 254)
BAR_TOLERANCE = 40

# The last stretch of the track.  Deliberately near the end: a bar at 1% and a
# bar at 99% look identical anywhere left of their fill line, and only the end
# distinguishes "nearly there" from "ready".
BAR_TEST_X0, BAR_TEST_X1 = 700, 750


def on_equinox_screen(frame, cam):
    """
    Is the dream screen actually open?

    Checks that the grid's tiles are where the lattice says they are.  Without
    this, `bar_is_full` was being asked about whatever happened to be on screen
    -- and since no other screen has the bar's pale blue at that spot, it
    always answered "not full".  The task then reported "bar not full yet,
    nothing to upgrade" without ever having travelled, which is worse than an
    error: it looks like a clean result.
    """
    hits = 0
    for i in range(len(DREAMS)):
        r, c = divmod(i, GRID_COLS)
        cx = cam.to_screen(GRID_X0 + GRID_DX * c)
        cy = cam.to_screen(GRID_Y0 + GRID_DY * r)
        patch = frame[cy - 12:cy + 12, cx - 12:cx + 12]
        if patch.size and patch.reshape(-1, 3).mean() > 120:
            hits += 1
    # Ten of thirteen, not all thirteen: a tile whose art happens to be dark in
    # the middle should not fail the test, and neither should the mouse sitting
    # over one.
    if hits < 10:
        return False

    # Tiles alone are not enough -- the World 3 MAP passed that test, because
    # snowfield sits at those coordinates and is just as bright.  So also
    # require the bar's LEFT END, which is pale blue at any fill level from 1%
    # to full, and is not a colour the map has anywhere near there.
    y0, y1 = cam.to_screen(BAR_Y0), cam.to_screen(BAR_Y1)
    x0, x1 = cam.to_screen(BAR_X0 + 14), cam.to_screen(BAR_X0 + 60)
    lead = frame[y0:y1, x0:x1]
    if lead.size == 0:
        return False
    mean = lead.reshape(-1, 3).mean(axis=0)
    return bool(np.all(np.abs(mean - np.array(BAR_FULL_BGR)) <= BAR_TOLERANCE))


def bar_is_full(frame, cam):
    """
    Is the Equinox bar full, i.e. is there an upgrade to spend?

    NOTE: verified against a FULL bar only.  The negative case -- that a
    partly-filled bar reads as not full -- follows from the fill being drawn
    left to right, but has not been seen in a capture.  If this ever returns
    True on a filling bar, that assumption is where to look.
    """
    y0, y1 = cam.to_screen(BAR_Y0), cam.to_screen(BAR_Y1)
    x0, x1 = cam.to_screen(BAR_TEST_X0), cam.to_screen(BAR_TEST_X1)
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return False
    mean = roi.reshape(-1, 3).mean(axis=0)
    return bool(np.all(np.abs(mean - np.array(BAR_FULL_BGR)) <= BAR_TOLERANCE))


# ── The info panel and its UPGRADE button ─────────────────────────────────────
# The brown box down the right-hand side.
PANEL_X0, PANEL_X1 = 752, 940
PANEL_Y0, PANEL_Y1 = 190, 470


def find_upgrade_button(frame, cam):
    """
    Where the UPGRADE button is, or None.

    Located rather than remembered, because the capture used to measure this
    screen had no dream selected and therefore no button on it -- so its
    position is the one thing here that was never measured directly.  Guessing
    it would mean clicking a coordinate nobody has checked, on a panel where
    the neighbouring pixels are a level counter and a bonus description.

    It is a filled button in the panel's lower half, notably brighter than the
    brown box around it, which is enough to find it without knowing where it
    sits.
    """
    y0, y1 = cam.to_screen(PANEL_Y0), cam.to_screen(PANEL_Y1)
    x0, x1 = cam.to_screen(PANEL_X0), cam.to_screen(PANEL_X1)
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Only the lower half: the bonus text above is bright too, and it is not a
    # button.
    half = g.shape[0] // 2
    band = (g[half:] > 120).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(band, 8)
    best = None
    for s in stats[1:]:
        w, h, area = int(s[2]), int(s[3]), int(s[4])
        # Button-shaped: clearly wider than tall, and a decent size.
        if area < 400 or h < 12 or w < 40 or w > 170 or h > 50 or w < h * 1.6:
            continue
        if best is None or area > best[4]:
            best = s
    if best is None:
        return None
    bx, by = int(best[0]) + int(best[2]) // 2, int(best[1]) + int(best[3]) // 2
    return (cam.to_game(x0 + bx), cam.to_game(y0 + half + by))
