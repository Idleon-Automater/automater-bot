#!/usr/bin/env python3
"""
What the bot thinks the game is, and what it sees there.

    python tools/diagnose.py          (or IdleonAutomator.exe --diagnose)

Run it with the game open.  It finds the window, prints what it found, saves
the exact frame the bot would work from, and says whether it can recognise the
map screen, the refinery panel, or the refinery entrance in that frame.

This exists because clicks were going nowhere and screen checks were failing at
the same time, which points past any individual coordinate at the window
itself -- and no amount of reasoning about coordinates settles that, whereas
one look at the frame the bot actually captured does.

Everything is written to the user's home folder as plain text and one PNG, so
it works the same from the packaged exe, which has nowhere to print.
"""

import os
import sys

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP not in sys.path:
    sys.path.insert(0, _APP)


def run():
    import numpy as np
    lines = []

    def say(s):
        lines.append(str(s))

    try:
        import core.window as gamewindow
        say("--- candidate windows ---")
        try:
            for c in gamewindow.enumerate_candidates():
                say(f"   {c}")
        except Exception as e:
            say(f"   (could not enumerate: {type(e).__name__}: {e})")

        rect = gamewindow.acquire(lock=False)
        say("")
        say("--- the window it chose ---")
        for k in sorted(rect):
            say(f"   {k}: {rect[k]}")
    except Exception as e:
        say(f"FAILED to find the game window: {type(e).__name__}: {e}")
        _write(lines)
        return 1

    try:
        import cv2
        import mss
        mon = {k: rect[k] for k in ("left", "top", "width", "height")}
        with mss.mss() as sct:
            frame = np.asarray(sct.grab(mon))[:, :, :3]
        out_png = os.path.join(os.path.expanduser("~"), "idleon_diagnose.png")
        cv2.imwrite(out_png, frame)
        say("")
        say(f"--- captured frame: {frame.shape[1]}x{frame.shape[0]} -> {out_png}")
        say("    (the coordinates all assume 960x540; anything else is the bug)")

        from core.navigate import Navigator, find_icon
        nav = Navigator(rect, None)
        say(f"    map screen open?      {nav.map_is_open(frame)}")

        from tasks.refinery import vision as RV
        say(f"    refinery panel?       {RV.is_refinery(frame)}")
        tpl_path = os.path.join(_APP, "tasks", "refinery", "nav",
                                "entry_refinery.png")
        tpl = cv2.imread(tpl_path)
        if tpl is not None:
            res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
            say(f"    refinery entrance:    best match {res.max():.3f} "
                f"(needs 0.80)")
        sx, sy = rect["left"] + 778, rect["top"] + 506
        say("")
        say(f"--- where the MAP button click would land: screen ({sx}, {sy})")
        say(f"    window spans x {rect['left']}..{rect['left']+rect['width']}, "
            f"y {rect['top']}..{rect['top']+rect['height']}")
        inside = (rect["left"] <= sx <= rect["left"] + rect["width"]
                  and rect["top"] <= sy <= rect["top"] + rect["height"])
        say(f"    inside the window?    {inside}")
    except Exception as e:
        say(f"FAILED while capturing: {type(e).__name__}: {e}")

    _write(lines)
    return 0


def _write(lines):
    out = os.path.join(os.path.expanduser("~"), "idleon_diagnose.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(chr(10).join(lines))
    print(chr(10).join(lines))


if __name__ == "__main__":
    sys.exit(run())
