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
LEVEL_REGION = (382, 112, 440, 132)      # x0, y0, x1, y1

MAX_LEVEL = 25


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
