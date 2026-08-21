#!/usr/bin/env python3
"""
The AUTO button, which is all the fighting this task has to do.

There is no need to find a monster and click it.  The quick-access bar has a
button that turns fighting on, it sits at a fixed place in the HUD, and the HUD
does not move with the camera -- so unlike everything in the world, this really
is a constant.

READING WHETHER IT IS ALREADY ON
--------------------------------
The button carries a small OFF flag.  That flag is pixel-identical across all
eight captures on hand -- towns, map screens, the cooking shelf -- so matching
it is a reliable "fighting is off".

What is NOT known is what the button looks like when fighting is ON: every
capture was taken with it off.  So the test is one-sided on purpose -- the OFF
flag being gone is treated as on, rather than pretending to recognise a state
nobody has photographed.  That is enough to decide whether to press it and
enough to confirm the press landed.
"""

import os

import cv2

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV_DIR = os.path.join(_HERE, "nav")

# The AUTO button, measured off the HUD: it spans x 415..472, y 478..533.
AUTO_XY = (443, 505)

# The OFF flag on it, found at (451, 493) in every capture.
AUTO_OFF = "auto_off.png"

# A part of the HUD that never changes, for telling "fighting is on" apart
# from "the quick-access bar is not on screen".  The ITEMS button: identical
# across all eight captures, including map screens and the cooking shelf.
# The MAP button was tried first and is NOT constant -- it lights up when the
# map is open, which is precisely when this would be asked.
HUD_ANCHOR = "hud_anchor.png"


def _load(name):
    path = os.path.join(NAV_DIR, name)
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"missing template: {path}")
    return img


def _match(frame, tpl, scale=1.0):
    if scale != 1.0:
        tpl = cv2.resize(tpl, (max(1, int(round(tpl.shape[1] * scale))),
                               max(1, int(round(tpl.shape[0] * scale)))),
                         interpolation=cv2.INTER_AREA)
    if frame.shape[0] < tpl.shape[0] or frame.shape[1] < tpl.shape[1]:
        return 0.0
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
    return float(cv2.minMaxLoc(res)[1])


def auto_off_score(frame, scale=1.0):
    """How strongly the OFF flag is showing -- for logs."""
    return _match(frame, _load(AUTO_OFF), scale)


def auto_is_off(frame, scale=1.0, min_score=0.85):
    """Whether the AUTO button is showing OFF."""
    return auto_off_score(frame, scale) >= min_score


def hud_visible(frame, scale=1.0, min_score=0.80):
    """
    Whether the quick-access bar is on screen.

    Needed because "the OFF flag is gone" has two causes -- fighting is on, or
    the bar is not being drawn -- and only one of them means the button was
    pressed.  Asked of a NEIGHBOURING button rather than of the AUTO button
    itself, which changes by design.
    """
    return _match(frame, _load(HUD_ANCHOR), scale) >= min_score


def auto_is_on(frame, scale=1.0):
    """
    Whether fighting is on: the bar is showing and the OFF flag is not.

    Deliberately not "the ON flag is showing".  Every capture on hand was
    taken with fighting off, so what ON looks like has never been seen, and a
    template for it would be invented rather than measured.
    """
    return hud_visible(frame, scale) and not auto_is_off(frame, scale)
