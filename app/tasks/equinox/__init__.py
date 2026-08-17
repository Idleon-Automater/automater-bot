"""
World 3 Equinox: spend a full bar on the dream the user picked.

WHAT IT DOES
------------
    1. travel to Equinox Valley (World 3, its own map -- costs a teleport)
    2. click the mirror to open the dream screen
    3. if the bar is NOT full, finish having clicked nothing
    4. if it is, click the chosen dream, then UPGRADE

Step 3 is the normal outcome and the reason the task is worth having.  The bar
fills at about 17,500/hr against a 730,265 cap, so it is ready roughly once
every 41 hours -- which is exactly the sort of thing a person forgets and a
task list does not.  Doing nothing safely is most of this task's job.

WHY IT DOES NOT REMEMBER THE SELECTION
--------------------------------------
The game does not either: the dream has to be picked again every time the
screen is opened.  So there is no "is it already selected?" shortcut to take,
and the click is unconditional.
"""

import os
import time

import core.input as _input
import core.window as _window
from core.navigate import Location
from core.task import Blocked, Param, Progress, Result, Task

from . import vision as V

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(_HERE, "nav")


class EquinoxTask(Task):
    name = "W3 Equinox"
    world = 3
    description = ("Spends a full Equinox bar on the dream you choose. Does "
                   "nothing if the bar is not full yet.")
    requirements = [
        "Have Equinox unlocked, and the World 3 map reachable",
        "The bar fills about once every 41 hours, so most runs will "
        "correctly do nothing",
    ]
    params = [
        Param("dream", "Dream to upgrade", "choice",
              choices=V.DREAMS, default="Equinox Symbols",
              help="Picked again on every visit, because the game forgets it "
                   "each time the screen is opened"),
    ]
    nominal_seconds = 40.0

    location = Location(
        world=3,
        map_name="Equinox Valley",
        via_town=False,
        map_icon=os.path.join(NAV, "map_equinox.png"),
        entry_icon=os.path.join(NAV, "entry_equinox.png"),
    )

    def __init__(self, dream="Equinox Symbols", **kw):
        self.dream = dream
        self._summary = None

    def _camera(self):
        import mss
        import idleon_hoops_bot as hoops
        rect = _window.acquire(lock=False)
        return hoops.Camera(mss.mss(), rect), rect

    def can_run(self):
        # Proving the dream screen is OPEN, not merely that a window exists.
        # The first version checked only the latter, so ensure_at concluded
        # "already at W3 Equinox" and skipped travelling entirely -- then read
        # the bar off whatever screen was showing and reported "not full".
        # A false negative that reads like a clean result is worse than a
        # failure, because nothing about it looks wrong.
        if not os.path.exists(self.location.entry_icon):
            raise Blocked("the Equinox mirror has not been captured yet")
        try:
            cam, _rect = self._camera()
        except RuntimeError as e:
            raise Blocked(str(e))
        # Looked at a few times over a second and a half.  ensure_at asks this
        # immediately after clicking the mirror, and the screen animates in --
        # a single look decides against a screen that is on its way up.
        for attempt in range(6):
            frame, _ = cam.grab()
            if V.on_equinox_screen(frame, cam):
                return
            time.sleep(0.25)
        raise Blocked("the Equinox dream screen is not open")

    def run(self, stop=None):
        cam, rect = self._camera()
        clicker = _input.Clicker()
        stopping = (lambda: bool(stop and stop()))

        frame, _ = cam.grab()
        if not V.bar_is_full(frame, cam):
            # The common case, and not a failure.  Saying so plainly beats a
            # warning: nothing went wrong, there is simply nothing to spend.
            self._summary = "W3 Equinox: bar not full yet, nothing to upgrade"
            yield Progress("the bar is not full yet - leaving it alone")
            return

        yield Progress("the bar is full")
        if stopping():
            return

        spot = V.dream_xy(self.dream)
        if spot is None:
            raise Blocked(f"'{self.dream}' is not a dream this task knows")
        yield Progress(f"selecting {self.dream}")
        clicker.click_at(rect["left"] + cam.to_screen(spot[0]),
                         rect["top"] + cam.to_screen(spot[1]),
                         time.perf_counter() + 0.25)
        time.sleep(0.9)          # the panel redraws with the dream's details

        if stopping():
            return

        frame, _ = cam.grab()
        btn = V.find_upgrade_button(frame, cam)
        if btn is None:
            # Never click a position that was not found.  A stray click in this
            # panel lands on a level counter or a cloud, and the cost of doing
            # nothing here is one wasted visit against a bar that is still
            # full -- the next run will spend it.
            raise Blocked(
                "the UPGRADE button could not be found after selecting "
                f"{self.dream} - not clicking a guessed position. The bar is "
                "still full, so nothing was lost")

        yield Progress("pressing UPGRADE")
        clicker.click_at(rect["left"] + cam.to_screen(btn[0]),
                         rect["top"] + cam.to_screen(btn[1]),
                         time.perf_counter() + 0.25)
        time.sleep(1.2)

        # Upgrading empties the bar.  That is the observable proof the click
        # landed, and it is worth checking rather than assuming: a click that
        # missed leaves the bar full, which would otherwise be reported as a
        # successful upgrade.
        frame, _ = cam.grab()
        if V.bar_is_full(frame, cam):
            self._summary = (f"W3 Equinox: pressed UPGRADE on {self.dream}, "
                             f"but the bar is still full - it may not have "
                             f"registered")
            yield Progress("the bar is still full - the upgrade may not have "
                           "registered")
        else:
            self._summary = f"W3 Equinox: upgraded {self.dream}"
            yield Progress("the bar emptied - upgrade applied")

    def report(self, steps, seconds):
        return Result(ok=True,
                      summary=self._summary or "W3 Equinox: finished",
                      seconds=seconds)
