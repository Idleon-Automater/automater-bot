#!/usr/bin/env python3
"""
Build the digit library from the labelled tier crops.

WHY THE LIBRARY IS BUILT, NOT HARVESTED
---------------------------------------
It used to be harvested at runtime: every board read fed its glyphs back into
the library, labelled by whatever the TIER templates had just said.  That is a
loop with no ground truth in it -- one template misread teaches a wrong glyph,
which makes the next read worse.  Measured before this change, the digit reader
disagreed with the tier templates on 8 of 16 live cells and returned 22 where
the tier was 29, five separate times.  '4' had accumulated 62 variants.

The tier crops in sushi_tiers/ are labelled by hand, one file per tier, so the
digits in them are known: "43.png" contains a 4 and a 3.  Building from those
gives every digit 0-9 across every colour band the game uses, and the result is
fixed -- it cannot drift, because nothing writes to it at runtime.

Run this after labelling a new tier.  It is not needed for the reader to handle
new TIERS: the digits generalise, and 53 or 61 reads itself with no new crop.

--from-screen adds whatever is on the board right now, labelled from the SAVE.
Sushi[0] holds the real tier of every occupied slot, so the labels come from
outside the vision code and the loop that poisoned the old library cannot form.
It refuses to harvest unless the save and the screen agree about which slots
are occupied, because the save flushes only every ~175 s and a board that has
merged since would attach the wrong tier to a glyph.

That mode exists because the crops cannot cover everything on their own: they
run 25-52, so the left slot has only ever held 2, 3, 4 and 5.  One board of low
tiers supplies the 0, 1 and 6-9 that no high-level player's grid will ever show
in that position.
"""

import collections
import glob
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tasks", "sushi"))

import sushivision as V          # noqa: E402


def from_screen(lib, seen):
    """Harvest the live board, labelled by the save.  Returns cells used."""
    import core.input as _input
    import core.minigame as _minigame
    import core.window as _window
    sys.modules.setdefault("gamewindow", _window)
    sys.modules.setdefault("clicker", _input)
    sys.modules.setdefault("minigame", _minigame)
    import sushi_watch as W
    import sushisim as M

    frame, scale = V.grab_station()
    if frame is None:
        print("  screen: the station is not open - skipping")
        return 0
    data = W.read_sushi()
    if not data or not data.get(0):
        print("  screen: cannot read the save - skipping")
        return 0
    save = data[0]

    # Is the save describing the board that is on screen right now?
    #
    # It flushes only every ~175 s, so a board that has merged since would
    # attach the wrong tier to a glyph and teach the library a lie -- which is
    # exactly what ruined the old harvester.
    #
    # Two things are NOT used for this, both tried and both wrong.  Occupancy
    # is not, because an empty cell the player owns is drawn as a coloured
    # tile and red, blue and tan are bright enough to look like ink.  And the
    # tier templates are not, even though they are a genuinely independent
    # reader, because they are independently WRONG in the case that matters:
    # asked about a board of tiers 1-28 they called 17, 18 and 20 "27", "29"
    # and "29", having no template below 25 and no way to say so.  Using them
    # as the arbiter would reject every board this mode exists to harvest.
    #
    # What is used instead: the board must be holding still, and the library
    # built from it must read it back.  A still board is one the save has had
    # time to describe; a library that cannot reproduce the board it was just
    # taught was taught something inconsistent, whatever the cause.
    import time
    def digit_pixels(fr, sc):
        return [V.digit_region(fr, s, sc) for s in range(M.BOARD_SLOTS)]

    first = digit_pixels(frame, scale)
    time.sleep(6.0)
    frame2, scale2 = V.grab_station()
    if frame2 is None:
        print("  screen: lost the station mid-check - skipping")
        return 0
    second = digit_pixels(frame2, scale2)
    moved = sum(1 for a2, b2 in zip(first, second)
                if (a2 is None) != (b2 is None)
                or (a2 is not None and a2.shape == b2.shape and not (a2 == b2).all()))
    if moved:
        print(f"  screen: {moved} cell(s) changed during the check - the board "
              f"is still moving, leave it alone and try again")
        return 0

    on_save = sorted(s for s in range(M.BOARD_SLOTS) if save[s] >= 0)
    print(f"  screen: board is holding still, {len(on_save)} occupied cell(s)")

    used = 0
    fresh = []
    for slot in sorted(on_save):
        want = str(save[slot] + 1)
        glyphs = V.split_digits(V.digit_region(frame, slot, scale))
        if len(glyphs) != len(want):
            continue
        used += 1
        for ch, g in zip(want, glyphs):
            key = (ch, g.tobytes())
            if key in seen:
                continue
            seen.add(key)
            lib[ch].append(g.astype(bool).tolist())
            fresh.append((ch, key))
    return used


def build(folder=None, out=None, screen=False):
    folder = folder or V.TIER_DIR
    out = out or V.DIGITS_FILE
    lib = collections.defaultdict(list)
    seen = set()
    used = 0
    for path in sorted(glob.glob(os.path.join(folder, "*.png"))):
        stem = os.path.splitext(os.path.basename(path))[0].split("_")[0]
        if not stem.isdigit():
            continue
        img = cv2.imread(path)
        if img is None:
            continue
        cell = cv2.resize(img, (V.CELL_PAD * 2, V.CELL_PAD * 2),
                          interpolation=cv2.INTER_AREA)
        glyphs = V.split_digits(V.cell_digit_mask(cell))
        if len(glyphs) != len(stem):
            # A crop whose number does not split into the right number of
            # glyphs is not evidence about anything; skip it rather than
            # teach the library a glyph under the wrong label.
            continue
        used += 1
        for ch, g in zip(stem, glyphs):
            key = (ch, g.tobytes())
            if key in seen:
                continue          # identical variants add nothing but time
            seen.add(key)
            lib[ch].append(g.astype(bool).tolist())
    if screen:
        n = from_screen(lib, seen)
        print(f"  screen: harvested {n} cell(s) from the live board")
    with open(out, "w") as f:
        json.dump({d: lib[d] for d in sorted(lib)}, f)
    return used, {d: len(v) for d, v in sorted(lib.items())}


if __name__ == "__main__":
    n, counts = build(screen="--from-screen" in sys.argv)
    print(f"built from {n} labelled crop(s)")
    print("variants per digit:", counts)
    missing = [str(d) for d in range(10) if str(d) not in counts]
    print("MISSING DIGITS:", missing if missing else "none - 0-9 all covered")
