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


# Several landmarks, and arrival needs only ONE of them.
#
# The first version used a single patch of the pillars, which live-failed with
# the pillars plainly on screen.  Two things were wrong with it.  It contained
# two of the floating runes, which drift; and it was chosen because it scored
# 1.000 between two captures taken seconds apart, which does not mean static --
# measured properly, the same pair moves other parts of that view a long way
# (the egg cluster on the ground scores 0.495 against itself).
#
# So: three landmarks in different places, and any one of them is arrival.
# A passing player, a wandering pet or a drifted rune can spoil one without
# spoiling all three.  Measured against the two sanctuary captures and, as a
# negative, against the town landing view and the world map:
#
#     sanctuary_chess2   0.919 stable   0.297 town   0.346 map
#     sanctuary_chess    0.888 stable   0.208 town   0.138 map
#     pillars            0.814 stable   0.269 town   0.307 map
#
# The gap between the worst true match and the best false one is wide, and the
# threshold sits in the middle of it rather than near the top.  0.70 was tried
# first and a live arrival scored 0.73 -- correct, but three hundredths from
# being called a failure, and the scene it was looking at had a second player
# and their pet standing in it.  The false side has never exceeded 0.346, so
# there is no reason to crowd the true side.
SANCTUARY_TEMPLATES = ("sanctuary_chess2.png", "sanctuary_chess.png",
                       "pillars.png")


def sanctuary_score(frame, scale=1.0):
    """Best landmark score, and which one -- for logs when arrival fails."""
    best, which = 0.0, None
    for name in SANCTUARY_TEMPLATES:
        try:
            score, _ = _match(frame, _load(name), scale)
        except FileNotFoundError:
            continue
        if score > best:
            best, which = score, name
    return best, which


def at_sanctuary(frame, scale=1.0, min_score=0.60):
    """Whether any sanctuary landmark is on screen, i.e. the walk is done."""
    return sanctuary_score(frame, scale)[0] >= min_score


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


def upgrade_button_score(frame, scale=1.0):
    """(score, position) for the UPGRADE button -- for logs when it is missed."""
    return _match(frame, _load("upgrade_button.png"), scale)


def find_upgrade_button(frame, scale=1.0, min_score=0.80):
    """Where the UPGRADE button is, or None when the panel is not open."""
    score, xy = upgrade_button_score(frame, scale)
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


def looks_maxed(frame, scale=1.0, min_score=0.80):
    """
    Whether the panel is showing MAX LV, i.e. the familiar is bought out.

    At maximum the game does not print "Lv.25/25" -- it replaces the level
    with the words MAX LV in gold.  This was written twice before that was
    known: first comparing the glyphs either side of the slash, on the theory
    that 25/25 would match itself, and it returned None against a real maxed
    panel because there is no slash there to compare across.  Guessing what a
    screen says without having seen it is how that happens.

    What it means for the task is better than what was planned.  MAX LV is a
    distinct, static, two-word template rather than a number that has to be
    read, so the end of the hold is now something the screen states outright
    rather than something inferred from counting changes.
    """
    score, _ = _match(frame, _load("max_lv.png"), scale, LEVEL_REGION)
    return score >= min_score
