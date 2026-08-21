"""
World 4 Ribbons: merge duplicate ribbons on the cooking shelf.

WHAT IT DOES
------------
    1. travel to World 4 town (free -- the town teleport)
    2. click the MENU signboard to open the cooking screen
    3. read the ribbon shelf, drag one duplicate onto its twin, repeat
    4. stop when nothing on the shelf matches anything else

ONE MERGE PER READING, NEVER A BATCH
------------------------------------
The same lesson the sushi board taught, and for a stronger reason here.  A
merge puts the new ribbon in the slot it was dragged ONTO and empties the one
it came from, so every later pair in a batch refers to a shelf that has moved.
Worse, the result is not predictable: two rank 10s usually give an 11 and
sometimes a 12, so simulating the batch forward does not rescue it either.

Re-reading costs one screen grab.  Getting it wrong costs a drag onto the
wrong ribbon, which merges something the player did not intend and cannot be
undone.

WHY THERE IS NO ASLEEP()
------------------------
Unlike the summoning familiar or the Equinox bar, nothing here is on a timer.
The shelf either has duplicates or it does not, and the only way to know is to
look -- the save does hold the shelf, but it is ~175 s stale, and a task that
skipped itself on that would sit out the minutes after the player earned a
ribbon.  So it always goes, and finishing with "nothing to merge" is a normal
outcome rather than a failure.
"""

import os
import time

import core.input as _input
import core.window as _window
from core.task import Blocked, Progress, Result, Task

from . import vision as V

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(_HERE, "nav")

# Reaching the signboard.  The town camera follows the character, so the sign
# is located rather than clicked at a remembered spot.
OPEN_TRIES = 16
OPEN_POLL = 0.3

# After a drag, WAIT FOR THE SHELF TO CHANGE rather than for a fixed delay.
#
# A single check 0.7 s after the drag is what the first version did, and it
# read the shelf mid-animation: the merge had happened, both ribbons were
# still drawn, the count had not fallen yet, and the run concluded the drag
# had failed and stopped with one merge done and three more available.
#
# A merge always costs a slot -- one rank up or two, the source empties either
# way -- so "the occupied count fell" is the thing to watch for, and watching
# costs nothing when it happens quickly.
MERGE_SETTLE = 0.35       # let the drag land before looking at all
MERGE_WAIT_S = 4.0        # how long to wait for the count to fall
MERGE_POLL = 0.2

# A ceiling on merges in one visit.  Not expected to be reached -- 28 slots
# cannot yield more than 27 merges even if every one cascaded -- and it exists
# so a misread that keeps finding the same phantom pair cannot loop forever.
MAX_MERGES = 40


class RibbonsTask(Task):
    name = "W4 Ribbons"
    world = 4
    description = ("Merges duplicate ribbons on the cooking shelf into higher "
                   "ranks. Does nothing if there are no duplicates.")
    requirements = [
        "Have World 4 town unlocked (the town teleport is free)",
        "Have the cooking table and its ribbon shelf unlocked",
    ]
    # A drag and a re-read is about two seconds, and a full shelf rarely offers
    # more than a handful of pairs.  Replaced by the observed median once there
    # are runs to go on.
    nominal_seconds = 45.0

    # Its own route, for the same reason as W6 Summoning: ensure_at() travels
    # and then clicks ONE entry template, and this needs the town teleport
    # followed by a signboard that is only reachable once the town has loaded.
    # can_run() stays permissive so ensure_at returns without travelling.
    location = None

    def __init__(self, **kw):
        self._summary = None
        self._merged = 0

    def _camera(self):
        import mss

        import idleon_hoops_bot as hoops
        rect = _window.acquire(lock=False)
        return hoops.Camera(mss.mss(), rect), rect

    def can_run(self):
        try:
            self._camera()
        except RuntimeError as e:
            raise Blocked(str(e))

    def _open_cooking(self, cam, rect, clicker, stopping):
        """Travel if needed, then open the cooking screen.  Yields Progress."""
        from core.navigate import Navigator

        frame, _ = cam.grab()
        if V.on_cooking_screen(frame):
            return                              # already looking at the shelf

        nav = Navigator(rect, clicker)
        if V.find_menu_sign(frame) is None:
            yield Progress("travelling to World 4 town")
            nav.open_map()
            nav.go_to_town(4)
            yield Progress("arrived in town")
            if stopping():
                return
            frame, _ = cam.grab()

        sign = V.find_menu_sign(frame)
        if sign is None:
            # Teleporting to the world you are ALREADY in does nothing, so a
            # run that starts in World 4 but out of sight of the sign travels,
            # moves nowhere, and looks at the same view again.  Reported live.
            # Bouncing through another world makes the return a real map
            # change, which puts the character back at the town's landing spot.
            yield Progress("still no signboard -- hopping out and back")
            nav.bounce_via(4)
            if stopping():
                return
            frame, _ = cam.grab()
            sign = V.find_menu_sign(frame)

        if sign is None:
            raise Blocked(
                f"could not find the cooking MENU signboard in World 4 town "
                f"-- best gold-frame score {V.menu_sign_score(frame):.2f} "
                f"(needs {V.SIGN_GOLD_MIN})")

        yield Progress(f"opening the cooking screen at {sign}")
        clicker.click_at(rect["left"] + cam.to_screen(sign[0]),
                         rect["top"] + cam.to_screen(sign[1]),
                         time.perf_counter() + 0.25)
        for _ in range(OPEN_TRIES):
            frame, _ = cam.grab()
            if V.on_cooking_screen(frame):
                return
            if stopping():
                return
            time.sleep(OPEN_POLL)
        raise Blocked(
            f"the cooking screen did not open -- RIBBON SHELF heading best "
            f"match {V.shelf_title_score(frame):.2f}")

    def run(self, stop=None):
        # ensure_at() focuses the game and this task returns from it early, so
        # nothing else would.  Screen capture reads the pixels at the window's
        # coordinates, so an overlapped game is captured as whatever covers it.
        _window.focus()
        cam, rect = self._camera()
        clicker = _input.Clicker()
        stopping = (lambda: bool(stop and stop()))

        for step in self._open_cooking(cam, rect, clicker, stopping):
            yield step
            if stopping():
                return
        frame, _ = cam.grab()
        if not V.on_cooking_screen(frame):
            raise Blocked("the ribbon shelf is not on screen")

        shelf = V.read_shelf(frame)
        yield Progress(f"shelf has {V.occupied(shelf)} ribbon(s) "
                       f"in {V.SLOTS} slot(s)")

        while self._merged < MAX_MERGES:
            if stopping():
                break
            pairs = V.find_pairs(shelf)
            if not pairs:
                break
            src, dst = pairs[0]
            sx, sy = V.slot_xy(src)
            dx, dy = V.slot_xy(dst)
            yield Progress(f"merging slot {src} onto slot {dst}")
            _input.drag(clicker,
                        rect["left"] + cam.to_screen(sx),
                        rect["top"] + cam.to_screen(sy),
                        rect["left"] + cam.to_screen(dx),
                        rect["top"] + cam.to_screen(dy))
            time.sleep(MERGE_SETTLE)

            before = V.occupied(shelf)
            after, closed = None, False
            deadline = time.perf_counter() + MERGE_WAIT_S
            while time.perf_counter() < deadline:
                frame, _ = cam.grab()
                if not V.on_cooking_screen(frame):
                    closed = True
                    break
                after = V.read_shelf(frame)
                if V.occupied(after) < before:
                    break
                time.sleep(MERGE_POLL)

            if closed:
                # The screen went away mid-merge.  Stopping is the only safe
                # response: the next drag would be issued at shelf coordinates
                # over whatever is showing instead.
                yield Progress("the cooking screen closed - stopping")
                break
            if after is None or V.occupied(after) >= before:
                yield Progress(f"slot {src} did not merge after "
                               f"{MERGE_WAIT_S:.0f}s - stopping")
                if after is not None:
                    shelf = after
                break
            shelf = after
            self._merged += 1
            # The shelf is re-read every time round rather than planned once,
            # which is what lets a merge's own result pair with something --
            # two 3s make a 4, and if a 4 was already there that is a new
            # merge that did not exist a moment ago.

        left = V.occupied(shelf)
        if self._merged:
            self._summary = (f"W4 Ribbons: {self._merged} merge(s), "
                             f"{left} ribbon(s) left on the shelf")
        else:
            self._summary = (f"W4 Ribbons: nothing to merge, "
                             f"{left} ribbon(s) on the shelf")
        yield Progress(self._summary)

    def report(self, steps, seconds):
        return Result(ok=True,
                      summary=self._summary or "W4 Ribbons: finished",
                      detail={"merges": self._merged},
                      seconds=seconds)
