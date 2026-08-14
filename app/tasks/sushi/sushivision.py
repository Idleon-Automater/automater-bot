#!/usr/bin/env python3
"""
Read the Sushi Station off the screen.

The save also holds the board, but it flushes only every ~175 s -- too stale to
plan from and far too stale to verify with.  Reading pixels makes both instant.

Each cell shows its tier as a two-digit number in the bottom-right corner.  The
digits are drawn white/cyan/gold depending on the tier band, so matching keys on
SHAPE after a brightness threshold and ignores colour entirely.

Matching is done on the WHOLE number, not on split digits.  Segmenting into
single glyphs kept failing -- sprite highlights touch the digits and no crop or
shape filter separated them reliably -- and with only ~30 tiers in play, one
template per tier removes the problem rather than fighting it.

Templates live in sushi_tiers/ as <tier>.png, with <tier>_1.png, <tier>_2.png
for background variants (cells can be plain, gold-bordered or blue-bordered).
Cells that cannot be read are dumped to sushi_unknown/ for labelling.
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


def digit_region(frame, slot, scale=1.0):
    """Binary mask of a cell's tier number, native scale, never resized."""
    cx, cy = M.slot_to_xy(slot)
    x0 = int(round((cx - CELL_PAD + _DNX0) * scale))
    x1 = int(round((cx - CELL_PAD + _DNX1) * scale))
    y0 = int(round((cy - CELL_PAD + _DNY0) * scale))
    y1 = int(round((cy - CELL_PAD + _DNY1) * scale))
    r = frame[max(0, y0):y1, max(0, x0):x1]
    if r.size == 0:
        return None
    hsv = cv2.cvtColor(r, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 2] > _V_MIN) & (hsv[:, :, 1] < 120))


def split_digits(mask):
    """
    The glyphs in a number mask, left to right.

    Splits on blank columns.  This works natively where it failed on resized
    masks: the digits really are separated by clear columns at full resolution.
    Leading sprite bleed is dropped by keeping only the RIGHTMOST two runs --
    the number is right-aligned in its corner, so anything further left is not
    part of it.
    """
    if mask is None or not mask.any():
        return []
    cols = mask.sum(axis=0)
    runs, start = [], None
    for i in range(len(cols)):
        if cols[i] and start is None:
            start = i
        elif not cols[i] and start is not None:
            runs.append((start, i)); start = None
    if start is not None:
        runs.append((start, len(cols)))
    runs = [r for r in runs if r[1] - r[0] >= 2]
    return [mask[:, a:b] for a, b in runs[-2:]]


def load_digit_masks(path=DIGITS_FILE):
    """{digit: [mask, ...]} harvested from the screen at native scale."""
    if not os.path.exists(path):
        return {}
    raw = json.load(open(path))
    out = {}
    for d, v in raw.items():
        variants = v if isinstance(v[0][0], list) else [v]
        out[d] = [np.array(m, bool) for m in variants]
    return out


def read_tier_digits(frame, slot, digits, scale=1.0):
    """
    Tier by DIGIT matching at native scale, or None.

    Exact matching only: at native resolution a correct glyph is byte-identical
    to its template, so anything less is a glyph we have not seen.  That makes
    the failure honest -- it returns None for an unknown digit rather than the
    nearest lookalike, which is the whole problem the tier-template system had.
    """
    if not digits:
        return None
    gs = split_digits(digit_region(frame, slot, scale))
    if not gs or len(gs) > 2:
        return None
    txt = ""
    for g in gs:
        hit = None
        for d, variants in digits.items():
            for m in variants:
                if m.shape == g.shape and (m == g).all():
                    hit = d
                    break
            if hit:
                break
        if hit is None:
            return None
        txt += hit
    try:
        return int(txt)
    except ValueError:
        return None


def harvest_digits(frame, board, scale=1.0, path=DIGITS_FILE):
    """
    Add any new digit variants seen on a board whose tiers are already known.

    Called after a successful whole-number read, so the digit set fills itself
    in as new tiers appear -- 0 and 1 only show up once a tier containing them
    is on the board.  Returns how many variants were added.
    """
    digits = load_digit_masks(path)
    added = 0
    for slot, v in enumerate(board):
        if v < 0:
            continue
        label = str(v + 1)
        gs = split_digits(digit_region(frame, slot, scale))
        if len(gs) != len(label):
            continue
        for ch, g in zip(label, gs):
            have = digits.setdefault(ch, [])
            if not any(m.shape == g.shape and (m == g).all() for m in have):
                have.append(g)
                added += 1
    if added:
        json.dump({d: [m.astype(int).tolist() for m in ms]
                   for d, ms in digits.items()}, open(path, "w"))
    return added
