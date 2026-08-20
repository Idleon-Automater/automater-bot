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
"""

import collections
import glob
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tasks", "sushi"))

import sushivision as V          # noqa: E402


def build(folder=None, out=None):
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
    with open(out, "w") as f:
        json.dump({d: lib[d] for d in sorted(lib)}, f)
    return used, {d: len(v) for d, v in sorted(lib.items())}


if __name__ == "__main__":
    n, counts = build()
    print(f"built from {n} labelled crop(s)")
    print("variants per digit:", counts)
    missing = [str(d) for d in range(10) if str(d) not in counts]
    print("MISSING DIGITS:", missing if missing else "none - 0-9 all covered")
