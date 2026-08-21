#!/usr/bin/env python3
"""
Read the Summoning sanctuary's upgrade screen.

Everything here was measured off real captures rather than guessed, because
every coordinate becomes a click and a click in the wrong place on this screen
buys the wrong upgrade with essence that took days to earn.

WHY TEMPLATES AND NOT FIXED POINTS ALONE
----------------------------------------
The upgrade icons drift.  Measured across two captures taken seconds apart,
262 matched patches moved by (0,0), (1,-2), (-1,2), (1,-1) and the like --
about two pixels, never more.  That is small enough that a fixed centre would
in fact hit, but the icon is only 30 px across and the penalty for missing is
buying a neighbour, so the position is confirmed by matching before it is
clicked and the fixed point is only the place to start looking.

THE UPGRADE BUTTON IS ALSO THE 'PANEL IS OPEN' TEST
--------------------------------------------------
Measured: its template scores 1.000 where it belongs, 0.299 as a runner-up
anywhere else in the same frame, and 0.299 against the grid with nothing
hovered.  So one match answers both "is the familiar's panel up?" and "where
do I click to buy?", and there is no second signal to keep in step with it.
"""

import os

import cv2
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV_DIR = os.path.join(_HERE, "nav")

# Where the familiar sits in the grid, measured in both captures at (498, 164).
FAMILIAR_XY = (498, 164)
# How far to look around that before giving up.  Generous against the ~2 px of
# drift actually seen: the cost of a wider search is a few milliseconds, and
# the cost of too narrow a one is the task silently doing nothing.
SEARCH_PAD = 40

# The UPGRADE button, when the familiar's panel is open: x 373..445, y 135..160.
UPGRADE_XY = (409, 147)

# The level reads "Lv.1/25" at the panel's top right.
# Wide enough to hold the whole string with room either side.  The text runs
# x 376..442 and ends 2 px short of a tighter box that was tried first, which
# left no space for the comparison window in looks_maxed() to sit in.
LEVEL_REGION = (374, 110, 448, 134)      # x0, y0, x1, y1

MAX_LEVEL = 25

# GETTING THERE
# -------------
# The W6 town teleport does not land you at the sanctuary.  You arrive further
# right -- pagoda on the left, money bag and portal on the right -- and have to
# walk left to reach it.  The camera is clamped at that end, so once you are
# there the view stops moving: measured, the walked capture and a second one
# taken after walking again matched at dx=0 dy=0, score 1.000.  That is what
# makes arrival testable at all, since the destination always looks the same.
#
# The rune pillars ARE the entrance -- clicking them opens the summoning
# screen.  They are also the arrival test: the patch below scores 1.000 where
# it belongs against a 0.299 runner-up.
PILLARS_XY = (225, 270)          # where pillars.png sits once you have arrived
ENTRANCE_XY = (210, 285)         # click here to open the summoning screen

# Empty grass at the far left of the landing view.  Clicking it walks left
# without opening anything; the shop, the portal and the pagoda all do open
# something, which is why this is a named point and not "somewhere on the
# left".  Measured off w6town.png: a storage chest sits at x 120..190, so the
# point stays well left of it, and low enough to be on grass rather than on
# the decorative pets that fill x 0..95 up to y 375.
WALK_LEFT_XY = (90, 380)


def at_sanctuary(frame, scale=1.0, min_score=0.75):
    """Whether the rune pillars are on screen, i.e. the walk is finished."""
    score, _ = _match(frame, _load("pillars.png"), scale)
    return score >= min_score


def _load(name):
    path = os.path.join(NAV_DIR, name)
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"missing template: {path}")
    return img


def _match(frame, tpl, scale=1.0, region=None):
    """(score, (cx, cy)) for the best match, in game coordinates."""
    if scale != 1.0:
        tpl = cv2.resize(tpl, (max(1, int(round(tpl.shape[1] * scale))),
                               max(1, int(round(tpl.shape[0] * scale)))),
                         interpolation=cv2.INTER_AREA)
    x0 = y0 = 0
    if region:
        rx0, ry0, rx1, ry1 = [int(round(v * scale)) for v in region]
        rx0, ry0 = max(0, rx0), max(0, ry0)
        frame = frame[ry0:ry1, rx0:rx1]
        x0, y0 = rx0, ry0
    if frame.shape[0] < tpl.shape[0] or frame.shape[1] < tpl.shape[1]:
        return 0.0, None
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    cx = (x0 + loc[0] + tpl.shape[1] // 2) / scale
    cy = (y0 + loc[1] + tpl.shape[0] // 2) / scale
    return float(score), (int(round(cx)), int(round(cy)))


def find_familiar(frame, scale=1.0, min_score=0.80):
    """
    Where the familiar's icon is, or None.

    Searched only near where it belongs.  The panel, when open, draws the same
    slime sprite again at its top left -- measured at 0.907 against the real
    one's 1.000, which is a margin thin enough to lose on a bad frame.  Looking
    only around the grid position removes the question rather than ranking it.
    """
    fx, fy = FAMILIAR_XY
    region = (fx - SEARCH_PAD, fy - SEARCH_PAD,
              fx + SEARCH_PAD, fy + SEARCH_PAD)
    score, xy = _match(frame, _load("familiar_icon.png"), scale, region)
    return xy if score >= min_score else None


def find_upgrade_button(frame, scale=1.0, min_score=0.80):
    """Where the UPGRADE button is, or None when the panel is not open."""
    score, xy = _match(frame, _load("upgrade_button.png"), scale)
    return xy if score >= min_score else None


def panel_is_open(frame, scale=1.0):
    """Whether the familiar's panel is showing."""
    return find_upgrade_button(frame, scale) is not None


# WATCHING THE LEVEL WHILE THE BUTTON IS HELD
# -------------------------------------------
# The button is held rather than clicked because only about one press in five
# counts, so reaching 25 takes anywhere from a couple of seconds to twenty and
# clicking once per attempt would be far slower than the game allows.
#
# Held means something has to say when to let go, and the level itself says
# it.  Measured on this machine: a region of the screen can be re-read 60
# times a second -- 16.67 ms, exactly one display frame, and the same whether
# the region is 58x20 or the whole 960x540, because the capture is locked to
# the refresh and not to the number of pixels.  So the level can be watched
# live and the button released on the frame it reaches its maximum.
#
# The number is NOT read.  The glyphs touch -- "Lv.1/25" segments into a
# single run 66 px wide, not seven -- so splitting them by blank columns does
# not work here the way it does for the sushi tiers.  Two things are used
# instead, and neither needs to know what the digits are:
#
#   * the text CHANGING means a level was gained.  Counting those from a
#     level read out of the save covers the whole range with no reading at
#     all.
#   * the text is right-aligned with a constant "/25", so at maximum the
#     glyphs left of the slash are the same glyphs as those right of it.
#     looks_maxed() is that comparison.
#
# The second is a cross-check on the first, not a replacement: it is written
# from a capture at Lv.1/25 and has never been seen against a real 25/25
# panel, so a task must not depend on it alone.


def level_mask(frame, scale=1.0):
    """
    Binary mask of the "Lv.n/25" text, or None.

    Used for spotting CHANGE rather than for reading, so what matters is that
    it is stable frame to frame while the number holds still.
    """
    x0, y0, x1, y1 = [int(round(v * scale)) for v in LEVEL_REGION]
    r = frame[max(0, y0):y1, max(0, x0):x1]
    if r.size == 0:
        return None
    return cv2.cvtColor(r, cv2.COLOR_BGR2HSV)[:, :, 2] > 170


def level_changed(before, after, min_pixels=6):
    """
    Whether the level text differs enough to be a new number.

    A threshold rather than any-difference: one or two pixels flipping is
    antialiasing on a redraw, and counting those as level-ups would end the
    hold early with the familiar half bought.
    """
    if before is None or after is None or before.shape != after.shape:
        return False
    return int(np.count_nonzero(before != after)) >= min_pixels


def looks_maxed(frame, scale=1.0, tolerance=0.94):
    """
    Whether the level text reads n/n, i.e. the familiar is maxed.

    UNVERIFIED against a real maxed panel -- written from a capture at Lv.1/25,
    where it correctly says False.  Treat as a cross-check.
    """
    m = level_mask(frame, scale)
    if m is None or not m.any():
        return None
    tpl = _load("slash.png")
    score, xy = _match(frame, tpl, scale, LEVEL_REGION)
    if score < 0.7 or xy is None:
        return None
    x0 = int(round(LEVEL_REGION[0] * scale))
    sx = int(round(xy[0] * scale)) - x0
    half = tpl.shape[1]
    w = 20                                # width of the two-digit "25"
    left = m[:, max(0, sx - half // 2 - w):max(0, sx - half // 2)]
    right = m[:, sx + half // 2:sx + half // 2 + w]
    if left.shape != right.shape or left.size == 0:
        return None
    return float(np.count_nonzero(left == right)) / left.size >= tolerance
