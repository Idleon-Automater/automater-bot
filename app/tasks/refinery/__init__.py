"""
The World 3 Refinery: rank up what is ready, and report what has run dry.

THE ONE RULE THAT MATTERS
-------------------------
Click "Refine RANK UP".  Never click a button that says only "Refine".

They sit in the same place and differ by one word.  The plain "Refine" appears
when the power bar beside it is not full -- REDOX and EXPLOSIVE showed
POWER 1M/1M with RANK UP available, while SPONTANEOUS showed 719K/1M and
offered only "Refine".  Pressing the plain one spends the cycle without the
rank, which *slows* progression, so this task is one of the few where clicking
the wrong thing is worse than clicking nothing.  Hence: the button is read
before it is pressed, and anything unread is left alone.

WHAT IT DOES
------------
    1. travel to World 3 town (double-click the world tab)
    2. click the top of the refinery to open it
    3. on each tab in turn, press RANK UP wherever it is offered
    4. note any fuel component whose count is drawn in RED -- that is a
       material that has run out, and the player has to refill it by hand
    5. press EXIT

Step 4 is the reason this task is worth having beyond the clicking: a refinery
quietly starved of one material looks exactly like a refinery that is running,
until a week has gone by.  Red text is a colour test rather than a number to
read, which makes it the dependable kind of check.

EVERYTHING HERE WAS MEASURED
---------------------------
The button positions, the RANK UP test, the red-text test and the entry
template all come from real captures, not from reading a screenshot by eye.
See tasks/refinery/vision.py for the measurements and for the two tests that
looked reasonable and were wrong.
"""

import os
import time

import cv2

import core.input as _input
import core.window as _window
from core.navigate import Location
from core.task import Blocked, Param, Progress, Result, Task

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(_HERE, "nav")


class RefineryTask(Task):
    name = "Refinery"
    world = 3
    description = "Upgrade any refinery ready to rank up."
    requirements = [
        "Have the World 3 town quick access unlocked",
    ]
    params = [
        Param("check_all_tabs", "Check every salt tab", "bool", default=True,
              help="Off = only the tab that is already showing"),
    ]
    # A few seconds of travel plus a handful of clicks per tab.
    nominal_seconds = 45.0

    location = Location(
        world=3,
        map_name="World 3 Town",
        via_town=True,
        entry_icon=os.path.join(NAV, "entry_refinery.png"),
    )

    def __init__(self, check_all_tabs=True, **kw):
        self.check_all_tabs = check_all_tabs

    def can_run(self):
        import mss
        import numpy as np
        from tasks.refinery import vision as V

        if not os.path.exists(self.location.entry_icon):
            raise Blocked("the refinery entrance has not been captured yet")
        _window.focus(settle=0.3)
        rect = _window.acquire(lock=False)
        mon = {k: rect[k] for k in ("left", "top", "width", "height")}
        with mss.mss() as sct:
            frame = np.asarray(sct.grab(mon))[:, :, :3]
        # Either already inside, or in a town where the entrance is visible.
        if V.is_refinery(frame):
            return
        from core.navigate import find_icon
        if find_icon(frame, cv2.imread(self.location.entry_icon)) is None:
            raise Blocked("the refinery is not on screen")

    def run(self, stop=None):
        import mss
        import numpy as np
        from core.capture import to_screen
        from core.navigate import find_icon
        from tasks.refinery import vision as V

        rect = _window.acquire(lock=False)
        clicker = _input.Clicker()
        mon = {k: rect[k] for k in ("left", "top", "width", "height")}

        def grab():
            with mss.mss() as sct:
                return np.asarray(sct.grab(mon))[:, :, :3]

        def click(x, y, settle=0.7):
            sx, sy = to_screen(rect, x, y)
            clicker.click(sx, sy)
            time.sleep(settle)

        frame = grab()
        if not V.is_refinery(frame):
            hit = find_icon(frame, cv2.imread(self.location.entry_icon))
            if hit is None:
                yield Progress("cannot see the refinery entrance")
                return
            yield Progress("opening the refinery")
            click(hit[0], hit[1], settle=1.6)
            frame = grab()
            if not V.is_refinery(frame):
                yield Progress("the refinery did not open")
                return

        ranked, short = 0, []
        tabs = V.TABS if self.check_all_tabs else V.TABS[:1]
        for i, (tx, ty) in enumerate(tabs):
            if stop and stop():
                break
            if i:                                   # the first tab is already up
                click(tx, ty, settle=1.0)
                frame = grab()
                if not V.is_refinery(frame):
                    yield Progress(f"tab {i + 1} did not open - stopping here")
                    break
            buttons = V.find_buttons(frame)
            yield Progress(f"tab {i + 1}: {len(buttons)} salt(s) on screen")

            for n, button in enumerate(buttons):
                if stop and stop():
                    break
                # Read before pressing, every time.  A button that only says
                # "Refine" spends the cycle without the rank, which sets the
                # player back -- so anything not positively identified as
                # RANK UP is left alone.
                if not V.can_rank_up(frame, button):
                    yield Progress(f"    salt {n + 1}: not ready, left alone")
                    continue
                # Say what it saw before pressing.  When this misjudged a salt
                # once, nothing in the log explained why -- and this is the one
                # click that costs the player something when it is wrong.
                yield Progress(f"    salt {n + 1}: reads RANK UP "
                               f"[{V.rank_up_evidence(frame, button)}]")
                cx = sum(V.BUTTON_X) // 2
                cy = sum(button) // 2
                click(cx, cy, settle=1.0)
                ranked += 1
                yield Progress(f"    salt {n + 1}: ranked up")
                frame = grab()

            for (mx, my) in V.missing_materials(frame):
                short.append((i + 1, mx, my))

        if short:
            # Say WHERE each one is, not just how many.  "3 material(s) have
            # run out" tells you to go looking through three tabs of fuel
            # plates; naming the tab and the row tells you where to look.
            #
            # Position rather than the material's name: the name is artwork
            # beside the count, and reading it would mean another glyph
            # reader.  Tab and row is what you navigate by anyway.
            yield Progress(f"NEEDS REFILL: {len(short)} material(s) have run "
                           f"out - refill them by hand:")
            for tab, mx, my in short:
                yield Progress(f"    tab {tab}, "
                               f"{V.fuel_slot_name(mx, my)} "
                               f"(the count is drawn in red)")

        # Leave the way we came in, so the next task starts from the town.
        click(926, 30, settle=1.2)
        self._ranked = ranked
        self._short = short

    def report(self, steps, seconds):
        ranked = getattr(self, "_ranked", 0)
        short = getattr(self, "_short", [])
        bits = [f"{ranked} rank up(s)"]
        if short:
            bits.append(f"{len(short)} material(s) run dry - refill needed")
        return Result(ok=True, summary=f"Refinery: {', '.join(bits)}",
                      detail={"ranked": ranked, "missing": len(short)},
                      seconds=seconds)
