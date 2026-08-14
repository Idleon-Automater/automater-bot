"""
Screen capture for the game window.

Every capture goes through `grab()`, which focuses the window first.  Screen
capture reads whatever is topmost at the window's coordinates, so a game behind
a terminal yields a picture of the terminal -- and every detector downstream
then produces confident nonsense.  That cost a long debugging detour once; it
is cheap to prevent, so it is prevented here rather than in each task.
"""

import numpy as np

from . import window


def grab(lock=False, focus=True, settle=0.35):
    """
    Capture the game canvas.  Returns (frame, rect) or (None, None).

    `rect` carries left/top/width/height/scale so callers can convert game
    coordinates to screen coordinates.
    """
    import mss

    if focus:
        window.focus(settle=settle)
    try:
        rect = window.acquire(lock=lock)
    except RuntimeError:
        return None, None
    mon = {k: rect[k] for k in ("left", "top", "width", "height")}
    with mss.mss() as sct:
        frame = np.asarray(sct.grab(mon))[:, :, :3]
    return frame, rect


def to_screen(rect, x, y):
    """Game coordinates -> screen coordinates."""
    return (rect["left"] + int(round(x * rect["scale"])),
            rect["top"] + int(round(y * rect["scale"])))
