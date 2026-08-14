"""
The "Wanna shoot some hoops?" confirmation.

Clicking the basketball does not start a game.  A dark prompt box appears a
second or two later offering "You know it!" in green and "Nahhh..." in red,
and until the green one is clicked the minigame never begins.  Arriving at the
Valley of the Beans and then sitting in front of an unanswered prompt looks
exactly like a broken bot: the travel worked, the run did nothing.

The green option is FOUND rather than clicked at a remembered spot.  The two
choices sit side by side and the red one cancels, so a few pixels of drift in
the wrong direction would reliably decline the game -- and a decline is
invisible in the logs, because both answers make the prompt disappear.
Matching on colour cannot make that mistake: there is nothing red about the
text we look for.

HOOPS ONLY.  Throwy Darts starts the moment its entrance is clicked, with no
prompt in between -- confirmed by a darts run that played through without one.
Do not wire this into DartsTask on the assumption that the two games are
symmetrical; they are not.
"""

import os
import time

# The prompt box in game coordinates, generously bounded.  Searching only here
# keeps the game's own greenery -- this is a field of green frogs on grass --
# from ever being a candidate.
REGION = (36, 236, 464, 372)          # left, top, right, bottom

# Bright green lettering on a near-black box.  The value floor is what
# separates the text from the grass behind the box, which is darker and much
# less saturated.
HUE = (40, 90)
SAT_MIN = 110
VAL_MIN = 150

# Enough pixels to be lettering rather than a stray anti-aliased edge.
MIN_PIXELS = 60

# Red wraps around zero in OpenCV's 0-179 hue scale.
RED_HUE = 10

# The box: near-black, and big.  Coverage is measured across the whole search
# region, so a dark cave mouth or a shadow cannot reach it on its own.
BOX_VALUE_MAX = 70
BOX_FILL = 0.75                       # solid, not a dark outline
BOX_MIN = (260, 70)                   # width, height in pixels


def find_yes(frame):
    """
    Game coordinates of the green "You know it!", or None if not showing.

    The BOX is found first, and the green is only looked for inside it.  Green
    alone is not evidence of anything here: the Valley of the Beans is a field
    of bright green frogs on grass, and searching for green text across it
    found a frog every single time.  The near-black prompt box is the part
    that has no counterpart in the scenery.

    Returns the centroid of the green pixels, which lands inside the text --
    the option is a wide word, so the centre is comfortably within it.
    """
    import cv2
    import numpy as np

    x0, y0, x1, y1 = REGION
    if frame.shape[0] < y1 or frame.shape[1] < x1:
        return None
    box = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    h, s, v = box[:, :, 0], box[:, :, 1], box[:, :, 2]

    # One solid rectangle, not merely a lot of dark pixels.  Measuring dark
    # COVERAGE was the first attempt and it passed on half the screens in the
    # game: shadows, cave mouths and the dark strip under a cliff are dark in
    # quantity, and the bounding box of scattered dark pixels covers almost
    # the whole search region, which then let any green through.  A connected
    # component that fills its own bounding box is what a drawn box looks like
    # and what scenery does not.
    dark = (v < BOX_VALUE_MAX).astype(np.uint8)
    n, _lbl, stats, _c = cv2.connectedComponentsWithStats(dark, 8)
    best, best_area = None, 0
    for i in range(1, n):
        bx, by, bw, bh, area = stats[i]
        if bw < BOX_MIN[0] or bh < BOX_MIN[1]:
            continue
        if area < BOX_FILL * bw * bh:          # hollow: not a drawn box
            continue
        if area > best_area:
            best, best_area = (bx, by, bw, bh), area
    if best is None:
        return None
    bx, by, bw, bh = best
    rx0, ry0_, rx1, ry1_ = bx, by, bx + bw, by + bh

    inside = np.zeros(dark.shape, bool)
    inside[ry0_:ry1_, rx0:rx1] = True
    bright = (s >= SAT_MIN) & (v >= VAL_MIN)
    green = inside & bright & (h >= HUE[0]) & (h <= HUE[1])
    red = inside & bright & ((h <= RED_HUE) | (h >= 180 - RED_HUE))
    if int(green.sum()) < MIN_PIXELS or int(red.sum()) < MIN_PIXELS:
        # BOTH answers must be in the box.  A dark panel with something green
        # on it is common -- the refinery is one -- but a black box holding a
        # green word and a red word beside it is this prompt and nothing else.
        return None

    gy, gx = np.nonzero(green)
    ry, rx = np.nonzero(red)
    gx_mean, rx_mean = float(gx.mean()), float(rx.mean())
    if gx_mean >= rx_mean:
        # "You know it!" is the LEFT option and "Nahhh..." the right one.  If
        # that is not what we are looking at, the reading is wrong somehow --
        # and guessing here means a 50/50 chance of declining the game, which
        # leaves no trace because both answers dismiss the box.
        return None
    return (x0 + int(gx_mean), y0 + int(gy.mean()))


# The EXIT button on the game-over screen: bottom-right corner, red on a dark
# blue night sky.  Bounded generously; nothing else down there is red.
EXIT_REGION = (860, 490, 960, 540)
EXIT_MIN_PIXELS = 120


def find_exit(frame):
    """
    Game coordinates of the EXIT button, or None.

    "Game over! Exit to claim your points" means exactly that: the points are
    not banked until the button is clicked, and until then the character is
    stuck on a screen that blocks the map -- so a run that plays perfectly and
    does not press this scores nothing and strands every task behind it.

    The button is the only saturated red in that corner; the screen behind it
    is a dark blue night sky, which is as far from red as the wheel goes.
    """
    import cv2
    import numpy as np

    x0, y0, x1, y1 = EXIT_REGION
    if frame.shape[0] < y1 or frame.shape[1] < x1:
        return None
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    red = (((h <= RED_HUE) | (h >= 180 - RED_HUE)) & (s >= 120) & (v >= 120))
    if int(red.sum()) < EXIT_MIN_PIXELS:
        return None
    ys, xs = np.nonzero(red)
    return (x0 + int(xs.mean()), y0 + int(ys.mean()))


def claim_and_exit(cam, rect, clicker, timeout=12.0, log=None):
    """
    Press EXIT on the game-over screen.  True if it was pressed.

    Waits, because the button appears with the game-over banner rather than
    the instant the last life goes.
    """
    from core.capture import to_screen

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        frame, _ = cam.grab()
        hit = find_exit(frame)
        if hit:
            sx, sy = to_screen(rect, hit[0], hit[1])
            clicker.click(sx, sy)
            if log:
                log("Claimed the points and left the game")
            time.sleep(1.5)
            return True
        time.sleep(0.3)
    return False


def confirm(cam, rect, clicker, timeout=8.0, log=None):
    """
    Wait for the prompt and accept it.  True if it was answered.

    False is not a failure: the prompt only appears when a game is actually
    starting, so a run that was already on the shooting screen never sees one.
    The caller carries on either way.
    """
    from core.capture import to_screen

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        frame, _ = cam.grab()
        hit = find_yes(frame)
        if hit:
            sx, sy = to_screen(rect, hit[0], hit[1])
            clicker.click(sx, sy)
            if log:
                log("Accepted the prompt to start a game")
            # The board takes a moment to appear; reading it too early sees
            # the prompt fading out and mistakes it for the game.
            time.sleep(1.2)
            return True
        time.sleep(0.25)
    return False


# "TAP TO PLAY" beside the basketball, with its little hand.  When the game is
# on cooldown the game replaces this with a countdown ("1:28").
READY_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "nav", "hoops_ready.png")
# Wide, because the camera follows the character: the basketball is NOT at a
# fixed screen position, and two captures of this same map put the label 40px
# apart.  A tight box calibrated on one frame simply misses on the next.
#
# The ENTER SHOP sign next door has its own identical "TAP HERE" hand, which
# is why the template covers the LABEL and not just the hand -- "TAP TO PLAY"
# and "TAP HERE" are different words, so the text is what tells them apart
# and a wide search cannot confuse the two.
READY_SEARCH = (480, 240, 960, 470)
READY_MIN = 0.70


def ready_to_play(frame):
    """
    Whether the basketball is offering a game rather than counting down.

    Reading the countdown digits would be the other way to answer this, and
    this way is better: the hand is one fixed icon, while the timer is
    variable-width text that changes every second and reads differently at
    "1:28" and "12:04".  The question is only ever "is it ready?", so match
    the thing that means ready and treat everything else as not.
    """
    import cv2

    tpl = cv2.imread(READY_TEMPLATE)
    if tpl is None:
        return None                    # unknown, not "no"
    # Clamp rather than bail.  A real capture came in at 956x540 -- the game
    # window is resizable -- and refusing to answer on a frame a few pixels
    # narrower than the search box turned a clear "cooling down" into
    # "unknown", which is the one answer the caller cannot act on.
    h, w = frame.shape[:2]
    x0, y0 = READY_SEARCH[0], READY_SEARCH[1]
    x1, y1 = min(READY_SEARCH[2], w), min(READY_SEARCH[3], h)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    region = frame[y0:y1, x0:x1]
    if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
        return None
    return float(cv2.matchTemplate(region, tpl,
                                   cv2.TM_CCOEFF_NORMED).max()) >= READY_MIN
