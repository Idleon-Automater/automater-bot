#!/usr/bin/env python3
"""
What would ship, and what would not.

    python tools/release_audit.py

The rule is an ALLOWLIST.  A release contains what this file names and nothing
else, because the failure modes are not symmetric: forgetting to exclude a file
leaks it, while forgetting to include one produces an obvious crash on the
first run.  A blocklist gets that backwards -- it fails silently, in the
direction that matters.

WHAT THIS IS GUARDING AGAINST
-----------------------------
The working folder holds recordings of live play, screenshots taken mid-session
and debug dumps.  Those show a character name, a level, an account's progress,
and whoever else happened to be standing nearby.  None of it belongs in
something handed to other people, and none of it is needed to run the bot.

Anything that survives the allowlist and could still carry a picture is listed
under REVIEW rather than passed silently, because "it is only a small crop" is
exactly the reasoning that ships a nameplate.
"""

import os
import sys

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Everything a release needs, and nothing else.  Directories are listed with a
# trailing slash and cover their contents.
ALLOW = [
    "run.py",
    "core/",
    "ui/",
    "tools/",
    # The whole tasks tree, rather than one line per task package.  Listing
    # them individually was the honest reading of "allowlist", and it broke
    # twice: a new package is invisible to the build until someone remembers
    # this file, and the failure is a crash on launch rather than a warning.
    # The DENY list below still removes the data folders, and every image that
    # survives is printed under NEEDS YOUR REVIEW, so nothing ships unseen.
    "tasks/",
]

# Never shipped, even where the allowlist would otherwise catch them.
DENY = [
    "__pycache__/",
    "sushi_unknown/", "wind_unknown/", "sushi_label/",
    "misses/", "recordings/",
    "hoops_stats.json",          # a record of this account's runs
    ".png.bak",
]

# Extensions that can carry a picture of somebody's screen.
PICTURE = (".png", ".jpg", ".jpeg", ".bmp", ".npy")


def classify(rel):
    parts = rel.replace("\\", "/")
    for d in DENY:
        if d.endswith("/") and (parts.startswith(d) or f"/{d}" in parts):
            return "DENY", d
        if not d.endswith("/") and parts.endswith(d):
            return "DENY", d
    for a in ALLOW:
        if a.endswith("/") and parts.startswith(a):
            return "SHIP", a
        if parts == a:
            return "SHIP", a
    return "EXCLUDED", "not on the allowlist"


def main():
    ship, denied, excluded, review = [], [], [], []
    for root, dirs, files in os.walk(_APP):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, _APP).replace("\\", "/")
            verdict, why = classify(rel)
            size = os.path.getsize(full)
            row = (rel, size, why)
            if verdict == "SHIP":
                ship.append(row)
                if rel.lower().endswith(PICTURE):
                    review.append(row)
            elif verdict == "DENY":
                denied.append(row)
            else:
                excluded.append(row)

    def show(title, rows, cap=None):
        total = sum(r[1] for r in rows)
        print(f"\n{title}  ({len(rows)} files, {total/1024:.0f} KB)")
        for rel, size, why in sorted(rows)[:cap]:
            print(f"    {rel:52} {size/1024:8.1f} KB")
        if cap and len(rows) > cap:
            print(f"    ... and {len(rows)-cap} more")

    show("SHIPS", [r for r in ship if not r[0].lower().endswith(PICTURE)], 40)
    show("EXCLUDED (not on the allowlist)", excluded, 20)
    show("DENIED explicitly", denied, 20)

    print(f"\n{'=' * 68}")
    print(f"NEEDS YOUR REVIEW -- images that would ship ({len(review)} files, "
          f"{sum(r[1] for r in review)/1024:.0f} KB)")
    print("=" * 68)
    by_dir = {}
    for rel, size, _ in review:
        by_dir.setdefault(os.path.dirname(rel), []).append((rel, size))
    for d, rows in sorted(by_dir.items()):
        tot = sum(s for _, s in rows)
        print(f"\n  {d}/   {len(rows)} files, {tot/1024:.0f} KB")
        for rel, size in sorted(rows)[:6]:
            print(f"      {os.path.basename(rel):32} {size/1024:7.1f} KB")
        if len(rows) > 6:
            print(f"      ... and {len(rows)-6} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
