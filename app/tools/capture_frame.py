#!/usr/bin/env python3
"""
Save a frame of the game, for measuring HUD coordinates against.

    python tools/capture_frame.py map                # map screen open
    python tools/capture_frame.py world              # standing in the world
    python tools/capture_frame.py popup --delay 6    # counts down, then shoots

Writes into `_dev/`, which is never part of a release: these frames show the
character name, the level, and whoever else is standing nearby.  They exist so
coordinates can be measured from the real window at its real size rather than
guessed from a pasted screenshot, and they stay on this machine.

Why measure rather than guess: every HUD coordinate here ends up as a click.  A
guessed one still clicks somewhere, and on the world map somewhere means the
character walks -- which moves every entry icon and breaks the whole queue, not
just the step that was wrong.
"""

import os
import sys
import time

import numpy as np

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

import core.window as gamewindow          # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(_APP), "_dev")


def capture(label="frame", delay=0.0):
    import cv2
    import mss

    # A delay exists for the popups: they only show while the mouse is hovering
    # them, so the shot has to be taken while the user's hand is on the game,
    # not while it is on the terminal.
    if delay:
        for left in range(int(delay), 0, -1):
            print(f"[Capture] {left}...")
            time.sleep(1.0)

    gamewindow.focus(settle=0.4)
    rect = gamewindow.acquire(lock=False)
    mon = {k: rect[k] for k in ("left", "top", "width", "height")}
    with mss.mss() as sct:
        frame = np.asarray(sct.grab(mon))[:, :, :3]

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{label}.png")
    cv2.imwrite(path, frame)

    print(f"[Capture] {frame.shape[1]}x{frame.shape[0]} -> {path}")
    print(f"[Capture] canvas at ({rect['left']},{rect['top']}) "
          f"{rect['width']}x{rect['height']}  scale={rect['scale']:.4f}")
    print(f"[Capture] game coordinates are screen pixels / {rect['scale']:.4f}")
    return path


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "frame"
    delay = 0.0
    if "--delay" in sys.argv:
        delay = float(sys.argv[sys.argv.index("--delay") + 1])
    try:
        capture(label, delay)
    except RuntimeError as e:
        print(f"[Capture] {e}")
        sys.exit(1)
