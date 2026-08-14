"""
Reading the refinery panel.

Everything here was measured against a real capture at 960x540, not guessed --
because the one thing this task must never do is press "Refine" when it meant
"Refine RANK UP".

TELLING THE TWO BUTTONS APART
-----------------------------
They sit in the same place, are the same size, and differ by one word.  Colour
is no help: each salt's button is a different colour (REDOX pink, EXPLOSIVE
tan, SPONTANEOUS blue), and the text polarity flips with it -- dark ink on the
light plates, white ink on the blue one.  An early attempt tested for "dark
ink" and declared all three ready, which would have clicked the one button that
must not be clicked.

What does separate them is structure: "Refine RANK UP" is two lines of text,
"Refine" is one.  Measuring per-row contrast inside the plate -- how much the
pixels vary along each row, regardless of which way round the colours are --
gives the bottom third of the button a contrast of 71-101 when RANK UP is
present and exactly 0 when it is not.  That is the check used here, and the
margin is wide enough that a near miss is not a coin flip.

MISSING MATERIALS
-----------------
A fuel count drawn in red means that component has run out.  Red text is a
colour test rather than a number to read, which makes it the dependable kind of
check -- but the digits are outlined, a pale pink fill inside a dark red edge,
so the test has to accept both and not just the saturated core.
"""

import cv2
import numpy as np

# The three combustion panels' button column, and the tab strip.  Measured.
BUTTON_X = (640, 694)
BUTTON_SEARCH_Y = (100, 420)
TABS = [(111, 26), (213, 31), (318, 31)]     # COMBUSTION, then the other salts
FUEL_AREA = (95, 95, 520, 455)               # x0, y0, x1, y1


def find_buttons(frame):
    """
    The refine buttons currently on screen, top to bottom.

    Found rather than hardcoded: the panel has three salts today and the rows
    would move if that ever changed.  Returns [(y_top, y_bottom), ...].
    """
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    x0, x1 = BUTTON_X
    y0, y1 = BUTTON_SEARCH_Y
    frac = (g[y0:y1, x0:x1] > 100).mean(axis=1)
    runs, start = [], None
    for y, v in enumerate(frac):
        if v > 0.40 and start is None:
            start = y
        elif v <= 0.40 and start is not None:
            if y - start >= 14:
                runs.append((y0 + start, y0 + y - 1))
            start = None
    if start is not None and (len(frac) - start) >= 14:
        runs.append((y0 + start, y1 - 1))
    return runs


def can_rank_up(frame, button):
    """
    True only when this button clearly reads "Refine RANK UP".

    Looks for a SECOND band of text below the first, separated by blank rows.
    An earlier version averaged the contrast of the button's lower third and
    called anything above a threshold ready -- which one strongly-drawn line
    can satisfy on its own, and it clicked a salt that was not complete.  A
    mean says "there is ink somewhere down there"; what actually distinguishes
    the two buttons is a gap and then more ink.

    Measured on a real panel, per row of the plate:
        RANK UP  ... 69 77 70 73 33 | 0 0 0 | 71 86 101 92 92 94
        Refine   ... 64 73 65 68 38 | 0 0 0   0  0   0  0  0  0

    Returns False whenever it cannot tell.  Pressing the plain button spends
    the cycle without the rank, so doing nothing is always the cheaper mistake.
    """
    top, bottom = button
    x0, x1 = BUTTON_X
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(float)
    sd = g[top + 3:bottom - 2, x0:x1].std(axis=1)
    if len(sd) < 10:
        return False

    INK, BLANK, MIN_ROWS = 40.0, 12.0, 3
    bands, blanks, i = [], 0, 0
    run = 0
    for v in sd:
        if v > INK:
            run += 1
        else:
            if run:
                bands.append(run)
            run = 0
            if v < BLANK:
                blanks += 1
    if run:
        bands.append(run)
    # Two bands of real text, and some genuinely blank rows between them.
    strong = [b for b in bands if b >= MIN_ROWS]
    return len(strong) >= 2 and blanks >= 2


def rank_up_evidence(frame, button):
    """The row contrasts behind can_rank_up, for the log when it looks wrong."""
    top, bottom = button
    x0, x1 = BUTTON_X
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(float)
    sd = g[top + 3:bottom - 2, x0:x1].std(axis=1)
    return " ".join(f"{v:.0f}" for v in sd)


def missing_materials(frame):
    """
    How many fuel counts are drawn in red -- each one a component run dry.

    The digits are outlined: a pale pink core inside a dark red edge, so both
    ends of that range count.  A first attempt required a saturated red and
    found nothing, because the fill it was looking at is only 30% saturated.
    """
    x0, y0, x1, y1 = FUEL_AREA
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2]
    red = (((h < 10) | (h > 168)) & (s > 60) & (v > 70)).astype(np.uint8)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((3, 9), np.uint8))
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(red, 8)
    # Digits are small and wide-ish; the panel's red artwork is much bigger.
    # Shape separates the digits from everything else red on the panel.  The
    # count reads w=27 h=12 and fills 98% of its box -- it is a solid little
    # block of text.  The red artwork nearby is either far taller (an icon,
    # 36% filled) or a hairline rule 4 px high, so a fill test plus a height
    # range picks out the numbers and nothing else.
    found = []
    for i in range(1, n):
        w, h_, area = stats[i, 2], stats[i, 3], stats[i, 4]
        if not (10 <= w <= 90 and 8 <= h_ <= 18):
            continue
        if area / float(w * h_) < 0.70:
            continue
        found.append((int(cent[i][0]) + x0, int(cent[i][1]) + y0))
    return found


def is_refinery(frame):
    """
    Whether the refinery panel is what is on screen.

    Matched against the OUTPUT header, a piece of the panel's own chrome.
    Counting refine-button-shaped blobs was tried first and is not enough: a
    town frame produced three of them too, because bright blocks in a column
    are not rare.  The header scores 1.000 here and does not appear in a town
    frame at all.
    """
    import os
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "nav", "panel_output.png")
    tpl = cv2.imread(tpl_path)
    if tpl is None:
        return False
    from core.navigate import find_icon
    return find_icon(frame, tpl, min_score=0.85) is not None
