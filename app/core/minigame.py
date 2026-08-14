#!/usr/bin/env python3
"""
Which minigame is on screen.

The game distinguishes them internally with `MenuType2` -- 63 is Swishy Hoops,
87 is Throwy Darts (`_event_Hoops` and `_event_Darts` in N.js both gate on it).
That value is not visible from outside, but the two screens could hardly look
less alike: hoops plays against a dark blue night sky, darts against a lit brown
plank wall.  A mean hue over the play area separates them with an enormous
margin, so there is nothing subtle to get wrong.

Kept separate from either game's code so the launcher can pick a mode before
loading anything game-specific.
"""

import cv2
import numpy as np

HOOPS = "hoops"
DARTS = "darts"

# Sampled from real frames of both screens, avoiding the HUD strip at the top
# and the EXIT button at the bottom right.
_ROI = (120, 460, 40, 900)      # y0, y1, x0, x1 in game coordinates


def classify(frame, cam):
    """
    Return HOOPS, DARTS, or None if neither is confidently recognised.

    Also returns None on menus and the overworld, which is what the caller
    wants: better to wait than to drive the wrong game.
    """
    y0, y1, x0, x1 = _ROI
    roi = frame[cam.to_screen(y0):cam.to_screen(y1),
                cam.to_screen(x0):cam.to_screen(x1)]
    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, v = hsv.mean(axis=0)

    # Hue does all the work: measured H=9.6 on darts against H=103.7 on hoops,
    # which is about as far apart as two hues get.  Saturation and value are
    # only there to reject menus and loading screens, and are kept loose --
    # bounding them tightly on one screen without checking the other is how the
    # first version of this rejected every hoops frame it was given.
    if s < 80 or v < 40:
        return None
    if h < 25:
        return DARTS
    if 85 < h < 135:
        return HOOPS
    return None


def describe(frame, cam):
    """Diagnostic: the raw numbers behind a classification."""
    y0, y1, x0, x1 = _ROI
    roi = frame[cam.to_screen(y0):cam.to_screen(y1),
                cam.to_screen(x0):cam.to_screen(x1)]
    h, s, v = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3).mean(axis=0)
    return f"H={h:.1f} S={s:.1f} V={v:.1f} -> {classify(frame, cam)}"
