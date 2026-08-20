#!/usr/bin/env python3
"""
Find where the game keeps something, by watching what changes when you do it.

Searching the save for a field by name only works when it has a name worth
guessing.  This works the other way round and needs no guess at all: take a
snapshot, do the thing in-game, take another, and see what moved.

    python tools/save_diff.py before          (then do the thing)
    python tools/save_diff.py after

The save flushes every ~175 s, so leave a few minutes between the action and
the second snapshot, and do nothing else in the meantime -- the fewer things
that change, the shorter the answer.

Values that tick on their own are dropped: GlobalTime and its neighbours move
every save and would bury everything else.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import savefile as S          # noqa: E402

SNAP_DIR = os.path.join(os.environ.get("TEMP") or "/tmp", "idleon_save_snaps")

# These move by themselves on every flush; reporting them is noise.
ALWAYS_MOVES = {"GlobalTime", "Player", "Cauldron", "Construction", "Forge",
                "Pets", "Printer", "ShopRestock", "BookLib",
                "PostOfficeRefresh", "MinimizeTime", "PlayerAwayTime",
                "CloudsaveTimer"}


def snapshot():
    """{key: values} for every array key, plus TimeAway."""
    text = S._newest_text()
    if not text:
        return None
    # The format writes y<len>:<name>, and <len> is the name's exact length.
    # Use it.  Matching the name with a character class instead is ambiguous --
    # the array markers that follow are letters too, so a greedy match read
    # "Summonaa" as the key and the real Summon array was never captured.
    out = {}
    keys = set()
    for m in re.finditer(r"y(\d+):", text):
        n = int(m.group(1))
        if not 3 <= n <= 24:
            continue
        name = text[m.end():m.end() + n]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            keys.add(name)
    for key in sorted(keys):
        try:
            v = S.values(key, text, window=30000)
        except Exception:
            continue
        if isinstance(v, list) and v:
            out[key] = v
    out["__TimeAway__"] = S.time_away(text)
    return out


def flatten(v, path=""):
    if isinstance(v, list):
        for i, x in enumerate(v):
            yield from flatten(x, f"{path}[{i}]")
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from flatten(x, f"{path}.{k}")
    else:
        yield path, v


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("before", "after"):
        print(__doc__)
        return 2
    which = sys.argv[1]
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, which + ".json")

    snap = snapshot()
    if snap is None:
        print("could not read the save")
        return 1
    with open(path, "w") as f:
        json.dump(snap, f)
    print(f"{which}: {len(snap)} keys saved to {path}")

    if which == "before":
        print("now do the thing in-game, wait ~3 minutes, then run: "
              "python tools/save_diff.py after")
        return 0

    before_path = os.path.join(SNAP_DIR, "before.json")
    if not os.path.exists(before_path):
        print("no 'before' snapshot - run that first")
        return 1
    before = json.load(open(before_path))

    old = dict(flatten(before))
    new = dict(flatten(snap))
    changed = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        if any(w in k for w in ALWAYS_MOVES):
            continue
        changed.append((k, a, b))

    print(f"\n{len(changed)} value(s) changed:\n")
    for k, a, b in changed[:80]:
        print(f"  {k:<44} {a!r:>18}  ->  {b!r}")
    if len(changed) > 80:
        print(f"  ... and {len(changed) - 80} more")
    if not changed:
        print("  nothing moved.  Either the save has not flushed yet, or the "
              "game does not keep this on disk at all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
