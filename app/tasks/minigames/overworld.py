#!/usr/bin/env python3
"""
The screen between darts runs.

After EXIT the game returns to the world map, where the minigame entry sits as a
small dart sprite with a label above it.  The label is what changes:

    cooling down            ready
        4:00            [hand] TAP TO PLAY
     [dart sprite]        [dart sprite]

Clicking the sprite while it is ready starts a fresh run.  Endless mode is just
that loop -- play, exit, wait, click, play -- so this module answers two
questions: is the cooldown over, and where do I click.

Note that the shop entrance nearby carries its own "TAP HERE" hand, so the
readiness box is kept narrow and centred on the dart; in the reference
screenshot the two are ~110 px apart against a box half-width of 35.

WHY THE CLICK TARGET IS NOT A CONSTANT
--------------------------------------
The entry is a world object, not HUD, so its screen position moves with the
camera.  It is only stable while the character stands still, which it does for
the whole of an endless session -- so the position is LEARNED once and reused,
rather than hardcoded.  `find_entry` tries to locate it; `--dart-at` overrides
that when the detector cannot.

VALIDATION
----------
Checked against recording 1785509141_full (719 whole frames at 6 fps, covering
the map before entering, a full run, and the map again after EXIT):

  * find_entry returns (377, 212..228) on every world-map frame -- the gold
    sparkle on the dart, confirmed by eye against the frame.  The spread is the
    sprite bobbing, and is smaller than the sprite, so any of those values is a
    hit.
  * ready_to_play separates the two states cleanly: 166 px when "TAP TO PLAY"
    is up, 84 px while the timer counts down, on every frame of each.

`--dart-at X,Y` still overrides the detector, and endless mode still refuses to
click a position it has not established -- a stray click on the world map moves
the character, which would move the entry and break every later cycle.
"""

import cv2
import numpy as np

# The countdown sits above the entry sprite and is drawn in the same white
# pixel font as the rest of the HUD.  Its presence alone answers "is the
# cooldown running" -- the digits do not have to be read to know that.
_WHITE_S_MAX = 60
_WHITE_V_MIN = 180

# Gold sparkle on the entry sprite.
_GOLD_LO = np.array([18, 120, 150])
_GOLD_HI = np.array([38, 255, 255])


def _white_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return (hsv[:, :, 1] < _WHITE_S_MAX) & (hsv[:, :, 2] > _WHITE_V_MIN)


# A "white text is present" test does NOT work here, and the first version of
# this function got it wrong.  Both states draw white text above the entry:
#
#     cooling down :   4:00
#     ready        :   [hand icon]  TAP TO PLAY
#
# so presence is true either way, and the bot would have waited forever without
# ever clicking -- indistinguishable from a hang.  What separates them is the
# HAND: a solid filled glyph of a few hundred pixels, against digit strokes that
# are thin and broken into separate small components.  So the test is the size
# of the largest white blob, not how much white there is.
# Measured on recording 1785509141_full, box (70,70), every frame of each
# state: ready -> 166 px, cooling down -> 84 px.  Threshold sits between.
# The box MUST be this tall: at (70,52) the hand bobs partly out of it and
# the reading swung 36-159, overlapping the cooldown value and making the
# result flicker frame to frame.
_HAND_MIN_AREA = 120


def ready_to_play(frame, cam, entry, box=(70, 70)):
    """
    True when the cooldown is over and the entry can be clicked.

    Detects the "TAP TO PLAY" hand icon above `entry` (a game-coord (x, y)).
    Returns False when the entry is unknown, so a caller that trusts this can
    never be talked into clicking a position nobody has established.
    """
    if entry is None:
        return False
    ex, ey = entry
    w, h = box
    x0 = max(0, cam.to_screen(ex - w / 2))
    x1 = max(0, cam.to_screen(ex + w / 2))
    y0 = max(0, cam.to_screen(ey - h))
    y1 = max(0, cam.to_screen(ey - 6))
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return False

    m = _white_mask(roi).astype(np.uint8)
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(m, 8)
    biggest = max((stats[i, cv2.CC_STAT_AREA] for i in range(1, n)), default=0)
    return int(biggest) >= _HAND_MIN_AREA


def cooldown_running(frame, cam, entry, box=(70, 70)):
    """Inverse of ready_to_play, kept because the loop reads better that way."""
    return not ready_to_play(frame, cam, entry, box)


def find_entry(frame, cam, search=(90, 470, 20, 940)):
    """
    Best guess at the minigame entry's centre, in game coords, or None.

    Looks for the gold sparkle on the sprite and takes the largest such blob.
    Confirmed against a real recording; see the module docstring.
    """
    y0, y1, x0, x1 = search
    roi = frame[cam.to_screen(y0):cam.to_screen(y1),
                cam.to_screen(x0):cam.to_screen(x1)]
    if roi.size == 0:
        return None
    m = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), _GOLD_LO, _GOLD_HI)
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    best, best_area = None, 0
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        # A compact sparkle, not a long streak of foliage.
        if area < 12 or area > 900:
            continue
        if w > 60 or h > 60 or max(w, h) > 3 * max(1, min(w, h)):
            continue
        if area > best_area:
            best_area, best = area, cent[i]
    if best is None:
        return None
    return (cam.to_game(best[0]) + x0, cam.to_game(best[1]) + y0)
