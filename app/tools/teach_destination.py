#!/usr/bin/env python3
"""
Teach the bot where a task lives, by pointing at it once.

    python tools/teach_destination.py sushi map     # map screen, right world
    python tools/teach_destination.py sushi entry   # standing in the world

A window opens showing the current game frame.  Click the thing the bot should
click -- the destination marker on the map, or the entry icon in the world --
and a small template is cut around that point and saved with the task.  Press
ESC to cancel, or click again to re-pick before confirming with ENTER.

WHY POINTING BEATS RECORDING
----------------------------
What gets saved is a picture of the target, not a coordinate.  At run time the
bot searches for that picture, so the click follows the target when the camera
moves -- which it does constantly, since the camera tracks the character.  A
recorded coordinate would only be right from the exact spot it was recorded.

The templates are small crops of game artwork: terrain and icons, no nameplate,
no character panel.  They are the one thing from this process that ships, so
they are cut deliberately small and away from the HUD.

WHY THIS WORKS AT ALL
---------------------
Measured on the World 7 map: a marker template scores 1.000 where it belongs
and 0.73 for the next best place on the same map -- the markers look alike but
the terrain under each one does not.  The entry bubble is starker still: 1.000
against 0.33.  Both are wide enough margins to click on.
"""

import os
import sys

import cv2
import numpy as np

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

import core.window as gamewindow            # noqa: E402
from core.navigate import load_calibration  # noqa: E402

# Half-sizes of the saved crop.  Big enough to carry surrounding terrain, which
# is what makes a marker unique; small enough not to swallow the HUD.
HALF_W, HALF_H = 16, 14


def grab():
    import mss
    gamewindow.focus(settle=0.4)
    rect = gamewindow.acquire(lock=False)
    mon = {k: rect[k] for k in ("left", "top", "width", "height")}
    with mss.mss() as sct:
        return np.asarray(sct.grab(mon))[:, :, :3]


def uniqueness(frame, tpl, region):
    """How well the crop distinguishes its spot from everywhere else nearby."""
    x0, y0, x1, y1 = region
    sub = frame[y0:y1, x0:x1]
    if tpl.shape[0] > sub.shape[0] or tpl.shape[1] > sub.shape[1]:
        return None, None
    res = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
    _, best, _, loc = cv2.minMaxLoc(res)
    r2 = res.copy()
    r2[max(0, loc[1] - 14):loc[1] + 14, max(0, loc[0] - 14):loc[0] + 14] = -1
    _, second, _, _ = cv2.minMaxLoc(r2)
    return best, second


def teach(task_dir, kind):
    frame = grab()
    cal = load_calibration()
    region = tuple(cal["map_panel"]) if kind == "map" \
        else (0, 0, frame.shape[1], frame.shape[0])

    picked = {"pt": None}
    title = f"Click the {'destination marker' if kind == 'map' else 'entry icon'}" \
            f"   [ENTER = save, ESC = cancel]"

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            picked["pt"] = (x, y)

    cv2.namedWindow(title)
    cv2.setMouseCallback(title, on_mouse)
    while True:
        shown = frame.copy()
        if kind == "map":
            x0, y0, x1, y1 = region
            cv2.rectangle(shown, (x0, y0), (x1, y1), (0, 200, 255), 1)
        if picked["pt"]:
            x, y = picked["pt"]
            cv2.rectangle(shown, (x - HALF_W, y - HALF_H),
                          (x + HALF_W, y + HALF_H), (0, 255, 0), 1)
        cv2.imshow(title, shown)
        key = cv2.waitKey(30) & 0xFF
        if key == 27:
            cv2.destroyAllWindows()
            print("[Teach] cancelled")
            return 1
        if key == 13 and picked["pt"]:
            break
    cv2.destroyAllWindows()

    x, y = picked["pt"]
    tpl = frame[y - HALF_H:y + HALF_H, x - HALF_W:x + HALF_W]
    best, second = uniqueness(frame, tpl, region)

    out_dir = os.path.join(_APP, "tasks", task_dir, "nav")
    os.makedirs(out_dir, exist_ok=True)
    name = "destination.png" if kind == "map" else "entry.png"
    path = os.path.join(out_dir, name)
    cv2.imwrite(path, tpl)

    print(f"[Teach] picked ({x}, {y}) -> {path}  ({tpl.shape[1]}x{tpl.shape[0]})")
    if best is not None:
        margin = best - second
        print(f"[Teach] uniqueness: best={best:.3f} runner-up={second:.3f} "
              f"margin={margin:.3f}")
        # A weak margin here becomes a misclick later, and a misclick on the
        # world map walks the character -- so say so now, loudly, rather than
        # letting it fail during a queue.
        # This measures whether the crop can be FOUND again, not whether the
        # right thing was picked -- textured seabed scores just as well as a
        # marker.  Only a weak margin is evidence of anything; a strong one
        # says nothing about aim, so it is not reported as approval.
        if margin < 0.15:
            print("[Teach] WARNING: that crop is not distinctive enough to "
                  "find reliably. Pick a spot with more varied terrain.")
        else:
            print("[Teach] findable. (This does not check you picked the "
                  "right target -- only that the bot can locate it again.)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[2] not in ("map", "entry"):
        print(__doc__)
        sys.exit(2)
    sys.exit(teach(sys.argv[1], sys.argv[2]))
