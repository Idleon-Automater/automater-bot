#!/usr/bin/env python3
"""
Read the Sushi Station off the screen.

The save also holds the board, but it flushes only every ~175 s -- too stale to
plan from and far too stale to verify with.  Reading pixels makes both instant.

Each cell shows its tier as a number in the bottom-right corner.  There are two
readers for it, tried in that order.

DIGITS FIRST
------------
The number is cut at fixed columns into its glyphs and each glyph matched
against a library of 0-9.  This is what lets the bot read a tier nobody has
labelled: the digits generalise, so 53 and 61 read themselves the day they are
first made, and so do the low tiers a developed board never shows.  The glyphs
are matched on SHAPE alone -- the game draws the number white low down, gold in
the thirties, cyan in the forties and green at fifty-plus, and that colour is
noise.  The library is built by tools/build_digit_masks.py from the labelled
tier crops and never changes at runtime.

TIER TEMPLATES SECOND
---------------------
One template per tier, matched whole rather than split.  It only knows tiers
somebody has saved a crop of, which is why it cannot stand alone: an unlabelled
tier does not read as unknown, it reads as an EMPTY CELL, and the planner then
treats an occupied cell as free space to drag into.  It stays because it is
cheap and catches the occasional cell the digit reader declines.

Matching is done on the WHOLE number there, not on split digits.  Segmenting
into single glyphs kept failing at that stage -- sprite highlights touch the
digits and no crop or shape filter separated them reliably -- and with only ~30
tiers in play, one template per tier removed the problem rather than fighting
it.  The digit reader above succeeds where that failed because it does not
segment at all: it cuts at fixed columns, which is possible because the number
is right-aligned and drawn at a fixed size.

Templates live in sushi_tiers/ as <tier>.png, with <tier>_1.png, <tier>_2.png
for background variants (cells can be plain, gold-bordered or blue-bordered).
Cells that neither reader can read are dumped to sushi_unknown/ for labelling.
"""

import glob
import json
import os

import cv2
import numpy as np

import sushisim as M

_HERE = os.path.dirname(os.path.abspath(__file__))
TIER_DIR = os.path.join(_HERE, "sushi_tiers")
UNKNOWN_DIR = os.path.join(_HERE, "sushi_unknown")

CELL_PAD = 23                 # half a cell, in game pixels
_V_MIN = 150                  # digit cores are bright; sprites and outlines are not

# Number region within a cell crop whose centre is the cell centre.
_NX0, _NX1 = 23, 46
_NY0, _NY1 = 25, 43


def _number_mask(cell):
    """Binary mask of the tier number inside a native-resolution cell crop."""
    r = cell[_NY0:_NY1, _NX0:_NX1]
    if r.size == 0:
        return None
    hsv = cv2.cvtColor(r, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 2] > _V_MIN) & (hsv[:, :, 1] < 120))
    return cv2.resize(m.astype(np.uint8), (24, 20),
                      interpolation=cv2.INTER_NEAREST) > 0


def load_tiers(folder=TIER_DIR):
    """
    {displayed_tier: [mask, ...]} from the labelled crops.

    Several templates per tier: the cell background varies, so one mask per
    tier misses the variants.  Everything before an underscore is the tier.
    """
    lib = {}
    for path in glob.glob(os.path.join(folder, "*.png")):
        name = os.path.splitext(os.path.basename(path))[0]
        stem = name.split("_")[0]
        if not stem.isdigit():
            continue
        img = cv2.imread(path)
        if img is None:
            continue
        # Crops are saved upscaled from a 2*CELL_PAD box; restore that exact
        # size.  Resizing to anything else shifts the number by a pixel and the
        # match drops below threshold while looking identical by eye.
        n = cv2.resize(img, (CELL_PAD * 2, CELL_PAD * 2),
                       interpolation=cv2.INTER_AREA)
        m = _number_mask(n)
        if m is None or m.sum() < 12:
            # A blank template can never match, so it silently loses to the
            # nearest wrong tier forever.  A hand-saved 46.png came out empty
            # and a real 46 read as 45 at 0.883 for several runs, wasting two
            # merge attempts on every pass.  Refuse it loudly instead.
            print(f"[sushivision] template {os.path.basename(path)} has no "
                  f"readable number - ignoring it.  Re-save that crop.")
            continue
        lib.setdefault(int(stem), []).append(m)
    return lib


def _save_unknown(frame, slot, scale, why):
    """Dump a cell that could not be read, big enough to label by eye."""
    try:
        os.makedirs(UNKNOWN_DIR, exist_ok=True)
        cx, cy = M.slot_to_xy(slot)
        x0 = int(round((cx - CELL_PAD) * scale))
        x1 = int(round((cx + CELL_PAD) * scale))
        y0 = int(round((cy - CELL_PAD) * scale))
        y1 = int(round((cy + CELL_PAD) * scale))
        cell = frame[max(0, y0):y1, max(0, x0):x1]
        if cell.size:
            big = cv2.resize(cell, (cell.shape[1] * 5, cell.shape[0] * 5),
                             interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(os.path.join(UNKNOWN_DIR,
                                     f"slot{slot:03d}_{why}.png"), big)
    except Exception:
        pass                              # never let logging break a read


# Minimum gap between the best tier and the second-best DIFFERENT tier.
#
# Measured over 89 cells with a complete template set: every correct read
# scores 1.000 and the tightest margin is 0.108.  The 46-read-as-45 misread
# that wasted merges for several sessions scored 0.883 against a runner-up of
# 0.831 -- a margin of 0.052.  Anything between those separates them, so 0.07
# rejects the ambiguous case without touching a single good read.
#
# This matters because the failure mode is never "unreadable".  A missing or
# blank template makes the nearest wrong tier win outright, and the planner
# then works confidently from a board that is wrong in one cell.
_MIN_MARGIN = 0.07

# ...but only when the winner is not already convincing.
#
# Some digits are inherently alike: 33 scores 0.912 against 32 and 0.904
# against 35, so a slightly imperfect 33 at 0.96 has a margin of only 0.048 and
# a flat margin rule rejects it -- which flooded the unknown folder with tier
# 33 crops that were never in doubt.
#
# A near-perfect match needs no margin: it IS the glyph.  The margin only
# decides between mediocre candidates, which is exactly the 46-read-as-45 case
# (0.883 with a 0.052 margin, because no 46 template existed).
_TRUST_SCORE = 0.96


def _match(mask, lib):
    """
    (tier, score, margin) for the best template match.

    `margin` is the gap to the best score from a DIFFERENT tier -- variants of
    the same tier are alternatives, not competitors, so they must not count
    against each other.
    """
    per_tier = {}
    for tier, tpls in lib.items():
        per_tier[tier] = max(float((tpl == mask).mean()) for tpl in tpls)
    if not per_tier:
        return None, 0.0, 0.0
    ranked = sorted(((sc, t) for t, sc in per_tier.items()), reverse=True)
    best_score, best = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else 0.0
    return best, best_score, best_score - runner


def read_cell_tier(frame, slot, lib, scale=1.0, min_score=0.88, dump=False):
    """Displayed tier in `slot`, or None if empty or unreadable."""
    if frame is None or scale is None:
        raise ValueError("no frame - the sushi station is not on screen "
                         "(focus the game and open the station)")
    cx, cy = M.slot_to_xy(slot)
    x0, x1 = (int(round((cx - CELL_PAD) * scale)),
              int(round((cx + CELL_PAD) * scale)))
    y0, y1 = (int(round((cy - CELL_PAD) * scale)),
              int(round((cy + CELL_PAD) * scale)))
    cell = frame[max(0, y0):y1, max(0, x0):x1]
    if cell.shape[0] < 40 or cell.shape[1] < 40:
        return None
    m = _number_mask(cell)
    if m is None or m.sum() < 12:          # nothing bright: an empty cell
        return None
    best, score, margin = _match(m, lib)
    if score >= min_score and (score >= _TRUST_SCORE or margin >= _MIN_MARGIN):
        return best
    if dump:
        why = (f"m{int(score * 100):02d}" if score < min_score
               else f"amb{int(margin * 100):02d}")
        _save_unknown(frame, slot, scale, why)
    return None


def read_board(frame, scale=1.0, lib=None, dump=False, digits=None):
    """
    The whole board as stored tiers (displayed - 1), -1 for empty.

    DIGITS FIRST, tier templates as a fallback.
    
    Digit matching is exact at native scale and generalises: once 0-9 are
    known, a tier never seen before -- 51, 52, 60 -- reads itself with no
    labelling.  The tier templates only cover tiers that have been labelled by
    hand, which is why every new top tier used to go blind at the moment it was
    created.

    The fallback stays because the digit path still fails to split a few cells
    per board; between them the coverage is better than either alone.
    """
    lib = lib if lib is not None else load_tiers()
    digits = digits if digits is not None else load_digit_masks()
    out = []
    for s in range(M.BOARD_SLOTS):
        t = read_tier_digits(frame, s, digits, scale) if digits else None
        if t is None:
            t = read_cell_tier(frame, s, lib, scale, dump=dump)
        out.append((t or 0) - 1)
    return out


def is_station(frame):
    """
    True if this frame really is the Sushi Station.

    Screen capture reads whatever is on top at the window's coordinates, so a
    game behind a terminal yields a capture of the terminal, and every detector
    then produces confident nonsense.  Cheap to check, so check.
    """
    h, w = frame.shape[:2]
    panel = frame[int(h * 0.10):int(h * 0.75), int(w * 0.03):int(w * 0.72)]
    if panel.size == 0:
        return False
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    green = ((hsv[:, :, 0] > 35) & (hsv[:, :, 0] < 95)
             & (hsv[:, :, 1] > 40) & (hsv[:, :, 2] < 150))
    return float(green.mean()) > 0.15


def grab_station(settle=0.35):
    """
    Focus the game, capture, and refuse a frame that is not the station.
    Returns (frame, scale) or (None, None).
    """
    import mss
    import gamewindow

    gamewindow.focus(settle=settle)
    rect = gamewindow.acquire(lock=False)
    mon = {k: rect[k] for k in ("left", "top", "width", "height")}
    with mss.mss() as sct:
        frame = np.asarray(sct.grab(mon))[:, :, :3]
    if not is_station(frame):
        return None, None
    return frame, rect["scale"]


# Fuel bar.  It runs the FULL width of the panel; a first attempt measured only
# x 856..952 -- the right-hand end -- which is empty until the tank is nearly
# full, so the reader returned 0% for anything under about half and cooking
# stopped with 1.9B still in the tank.
_FUEL_BAR = (192, 197, 755, 957)          # y0, y1, x0, x1 in game coords


def fuel_fraction(frame, scale=1.0):
    """How full the fuel bar looks, 0.0-1.0, or None."""
    y0, y1, x0, x1 = _FUEL_BAR
    roi = frame[int(round(y0 * scale)):int(round(y1 * scale)),
                int(round(x0 * scale)):int(round(x1 * scale))]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    filled = ((hsv[:, :, 0] < 30) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 140))
    cols = filled.mean(axis=0) > 0.4
    if not cols.any():
        return 0.0
    return float((np.nonzero(cols)[0].max() + 1) / len(cols))


# Cook tier.  The panel's sushi sprite carries the tier the cook button will
# spawn, in the same font and corner as a board cell, so the tier templates read
# it directly.  It is user-adjustable and is the main lever over how long a run
# lasts -- a low base tier is cheap and cycles almost indefinitely, a high one
# costs far more fuel per press -- so it must be READ, never assumed.
_COOK_TIER_CELL = (771, 172)              # centre of the panel sprite, game px


def read_cook_tier(frame, scale=1.0, lib=None):
    """The tier the cook button will spawn (displayed), or None."""
    lib = lib if lib is not None else load_tiers()
    if not lib:
        return None
    cx, cy = _COOK_TIER_CELL
    x0, x1 = (int(round((cx - CELL_PAD) * scale)),
              int(round((cx + CELL_PAD) * scale)))
    y0, y1 = (int(round((cy - CELL_PAD) * scale)),
              int(round((cy + CELL_PAD) * scale)))
    cell = frame[max(0, y0):y1, max(0, x0):x1]
    if cell.shape[0] < 40 or cell.shape[1] < 40:
        return None
    m = _number_mask(cell)
    if m is None or m.sum() < 12:
        return None
    best, score, margin = _match(m, lib)
    return best if score >= 0.85 and (score >= _TRUST_SCORE
                                      or margin >= _MIN_MARGIN) else None


# ── Digit reading (native resolution, no resize) ──────────────────────────────
#
# The game draws tier numbers in one fixed font at one fixed size, always in the
# same corner.  Read straight off the screen with NO resizing, two cells of the
# same tier produce byte-identical masks -- measured agreement 1.0000 across
# every pair on a full board.
#
# Everything that went wrong with the whole-number reader came from resizing:
# templates were loaded from 5x-upscaled PNGs, downscaled to 46x46, then
# normalised to 24x20.  Two lossy steps between the screen and the comparison,
# which is why a 33 could score 0.96 instead of 1.000 and why digit splitting
# never worked -- an 8px glyph does not survive that.
#
# Reading digits instead of whole numbers means new tiers need no labelling at
# all: once 0-9 are known, 51 and 52 and 60 read themselves.
DIGITS_FILE = os.path.join(_HERE, "sushi_digit_masks.json")

# Number region, relative to the cell's top-left corner, in game pixels.
_DNX0, _DNX1 = 27, 46
_DNY0, _DNY1 = 32, 42


# Where the two glyphs sit inside the number region.
#
# The number is right-aligned in the corner and the game draws it at a fixed
# size, so the glyphs land in the same columns every time: measured over 103
# labelled crops, the left glyph occupies columns 0-7 and the right one 9-16,
# with the gap closing to nothing on about a quarter of them.  Cutting at fixed
# columns therefore beats splitting on blank columns, which is what the old
# reader did -- when the two glyphs touched it saw one run 16 wide and gave up,
# and that was 25 of those 103 crops.
#
# BOTH SLOTS ARE THE SAME WIDTH, AND THAT MATTERS MORE THAN IT LOOKS
# ------------------------------------------------------------------
# The first version cut at 0-8 and 8-17, which made the right slot a column
# wider than the left.  A glyph can then only ever match a variant learned in
# the SAME slot, and the library is built from tiers 25-52, where the only
# tens digits are 2, 3, 4 and 5.  So the left slot had never seen a 1 -- and
# every tier from 10 to 19 was unreadable for want of a digit the library knew
# perfectly well in the other position.  Measured on a board of tiers 1-28:
# 27 of 45 cells read.  Cutting both slots 8 wide, so the ink sits at the same
# offset in each and variants are interchangeable, took it to 38 with no new
# data at all.
_DIGIT_L0, _DIGIT_L1 = 0, 8
_DIGIT_R0, _DIGIT_R1 = 9, 17

# A left slot with less ink than this is empty, not a digit: single-digit tiers
# put nothing there.  A real glyph carries 15-25 pixels, so 4 is far below
# anything genuine and well above stray sprite bleed.
_MIN_INK = 4


def cell_digit_mask(cell):
    """
    Binary mask of the number, from a native-resolution cell.

    Deliberately colour-blind.  The digits are drawn in a different colour for
    each tier band -- white low down, gold in the thirties, cyan in the
    forties, green at fifty-plus -- but the SHAPE is identical, so colour is
    noise here and nothing else.  The old rule asked for bright AND desaturated
    pixels, which quietly cut into every saturated fill: a gold or green glyph
    lost its edges and stopped matching the same glyph drawn in white.

    Taking whatever is brightest in this particular region instead means the
    threshold follows the fill wherever the palette puts it, and the floor of
    120 stops a cell with no number at all from promoting its own background.
    """
    r = cell[_DNY0:_DNY1, _DNX0:_DNX1]
    if r.size == 0:
        return None
    v = cv2.cvtColor(r, cv2.COLOR_BGR2HSV)[:, :, 2].astype(int)
    return v >= max(120, int(v.max()) - 60)


def digit_region(frame, slot, scale=1.0):
    """Binary mask of a cell's tier number, native scale, never resized."""
    cx, cy = M.slot_to_xy(slot)
    x0 = int(round((cx - CELL_PAD) * scale))
    x1 = int(round((cx + CELL_PAD) * scale))
    y0 = int(round((cy - CELL_PAD) * scale))
    y1 = int(round((cy + CELL_PAD) * scale))
    cell = frame[max(0, y0):y1, max(0, x0):x1]
    if cell.shape[0] < _DNY1 or cell.shape[1] < _DNX1:
        return None
    return cell_digit_mask(cell)


def split_digits(mask):
    """
    The number's glyphs, left to right, cut at fixed columns.

    Returns one glyph for a single-digit number and two otherwise, so the
    caller can tell 5 from 45 without a separate rule.
    """
    if mask is None or not mask.any():
        return []
    left = mask[:, _DIGIT_L0:_DIGIT_L1]
    right = mask[:, _DIGIT_R0:_DIGIT_R1]
    if left.sum() < _MIN_INK:
        return [right]
    return [left, right]


def _score_glyph(g, by_shape):
    """
    Best agreement between `g` and any variant, allowing a pixel of slide.

    The number is not drawn on exactly the same pixel in every cell -- a shift
    of one is common and costs a tenth of the score, which was enough on its
    own to reject three of seventeen live cells whose correct digit was still
    ranked first.  Nine offsets removes that whole class of miss.

    `by_shape` is {shape: stacked array of variants}, compared all at once.
    Looping over variants in Python instead cost 254 ms a board against 6 ms
    for the tier templates, and the board is read several times a cycle.
    """
    stack = by_shape.get(g.shape)
    if stack is None:
        return 0.0
    best = 0.0
    # No offset first, and stop the moment something matches exactly.  The
    # glyph usually IS drawn where it was learned, so most calls end on the
    # first comparison; without this the library growing made the board read
    # nearly three times slower for no better answer.
    for dy, dx in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1),
                   (-1, -1), (-1, 1), (1, -1), (1, 1)):
        shifted = g if (dy or dx) == 0 else np.roll(np.roll(g, dy, 0), dx, 1)
        score = float((stack == shifted).mean(axis=(1, 2)).max())
        if score >= 1.0:
            return score
        best = max(best, score)
    return best


def load_digit_masks(path=DIGITS_FILE):
    """
    {digit: {shape: stacked variants}} built by tools/build_digit_masks.py.

    Grouped by shape and stacked here rather than at every comparison: the
    left and right slots are different widths, so a digit's variants are not
    all the same shape and only the matching ones can be compared at all.
    """
    if not os.path.exists(path):
        return {}
    raw = json.load(open(path))
    out = {}
    for d, v in raw.items():
        variants = v if isinstance(v[0][0], list) else [v]
        by_shape = {}
        for m in variants:
            a = np.array(m, bool)
            by_shape.setdefault(a.shape, []).append(a)
        out[d] = {k: np.array(v2) for k, v2 in by_shape.items()}
    return out


# Accept a glyph only when it is this close to a known one, and this far
# clear of the runner-up.  Both are needed and both are loose on purpose: the
# point is not to squeeze the last read out of the reader but to be certain
# about the ones it does return, because a wrong digit is a wrong tier and the
# planner acts on it.  Over 103 labelled crops leave-one-out and 17 live cells
# checked against the save, this pair returned 117 correct, 0 wrong, 3
# declined.  Anything it declines falls through to the tier templates.
_DIGIT_SCORE = 0.88
_DIGIT_MARGIN = 0.05

# Below this, the left slot holds no digit at all and the tier is a single one.
# Set between the two populations measured on a board of tiers 1-52: sprite
# bleed reached 0.700, the worst genuine digit was 1.000.
_NOT_A_DIGIT = 0.80


def _classify_glyph(g, digits):
    """(digit, best score) for one glyph.  Digit is None when it is not sure."""
    scores = {d: _score_glyph(g, by_shape) for d, by_shape in digits.items()}
    if not scores:
        return None, 0.0
    ranked = sorted(((sc, d) for d, sc in scores.items()), reverse=True)
    best_score, best = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < _DIGIT_SCORE or best_score - runner < _DIGIT_MARGIN:
        return None, best_score
    return best, best_score


def read_tier_digits(frame, slot, digits, scale=1.0):
    """
    Tier by DIGIT matching, or None if not certain.

    This is what makes the reader work on a tier nobody has labelled.  A tier
    template only knows the tiers someone has saved a crop of, so every new top
    tier used to go blind at the moment it was created -- and not merely
    unreadable: an unlabelled 52 read as EMPTY, and the planner treated an
    occupied cell as free space to drag into.  Digits generalise, so 53 and 61
    read themselves, and so do the low tiers no developed board ever shows.

    Returns None rather than a guess.  The caller falls back to the templates.
    """
    if not digits:
        return None
    mask = digit_region(frame, slot, scale)
    if mask is None or not mask.any():
        return None
    left = mask[:, _DIGIT_L0:_DIGIT_L1]
    right = mask[:, _DIGIT_R0:_DIGIT_R1]

    units, _ = _classify_glyph(right, digits)
    if units is None:
        return None

    # HOW A ONE-DIGIT TIER IS TOLD FROM A TWO-DIGIT ONE
    # -------------------------------------------------
    # Not by how much ink is in the left slot.  That was the first rule and it
    # is wrong: the sushi sprite bleeds into the corner, and on tiers 6 and 8
    # it put 37 and 30 lit pixels there -- as much as a real digit carries.
    #
    # By whether the left slot MATCHES a digit instead.  Measured across a
    # board holding every tier from 1 to 52: all 59 genuine tens digits scored
    # 1.000, and the three cells where a sprite bled into the slot scored 0.688
    # to 0.700.  Nothing landed between, so the question is not close.
    #
    # The middle ground still declines.  A left slot that scores like neither
    # -- too poor to be a digit, too good to dismiss -- could be a tens digit
    # this library has not seen, and reading it as a one-digit tier would turn
    # 45 into 5 and hand the planner a board that is wrong rather than short.
    if left.sum() < _MIN_INK:
        txt = units
    else:
        tens, tens_score = _classify_glyph(left, digits)
        if tens is not None:
            txt = tens + units
        elif tens_score < _NOT_A_DIGIT:
            txt = units
        else:
            return None

    try:
        n = int(txt)
    except ValueError:
        return None
    # A tier outside what the game can produce means the glyphs were read out
    # of something that is not a number.
    return n if 1 <= n <= 70 else None


