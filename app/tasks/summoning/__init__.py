"""
World 6 Summoning: buy the familiar out to 25/25 while its cost is reset.

WHAT IT DOES
------------
    1. travel to World 6 town (free -- the town teleport)
    2. walk left to the sanctuary, which the teleport does not land at
    3. click the rune pillars to open the summoning screen
    4. hover the familiar to bring up its panel
    5. HOLD the UPGRADE button, watching the level, and let go at 25/25

WHY IT HOLDS RATHER THAN CLICKS
-------------------------------
Roughly one press in five counts, so reaching 25 takes anywhere from a couple
of seconds to twenty.  One click per attempt would be far slower than the game
allows, and a fixed hold would either stop half done or keep pressing past the
cap.  So the button is held and the level is watched: a region of the screen
re-reads 60 times a second, one per display frame, which is far finer than the
level can move.

WHY THE TASK IS USUALLY ASLEEP
------------------------------
The costs reset weekly, and once the familiar is at 25 there is nothing to buy
until they do.  asleep() answers both from the save before anything travels,
so the task can live permanently in a daily list and cost nothing on the days
it has no work -- which is most of them.
"""

import os
import time

import core.input as _input
import core.window as _window
from core import savefile
from core.navigate import Location
from core.task import Blocked, Progress, Result, Task

from . import vision as V

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(_HERE, "nav")

# How long to keep clicking left before giving up on reaching the sanctuary.
# The walk takes a few seconds; this is loose enough not to matter and tight
# enough that a task which is somehow not in town stops rather than clicking
# the same spot forever.
WALK_TRIES = 12
WALK_SETTLE = 1.1

# The summoning screen takes a moment to draw -- reported at one to two
# seconds -- so this waits on the screen appearing rather than on a fixed
# sleep, which would either waste the fast case or click into a screen that
# has not drawn yet in the slow one.
OPEN_TRIES = 20
OPEN_POLL = 0.25

# Stop holding after this even if nothing else says to.  A safety net, not a
# plan: every ordinary ending is decided by watching the level.
MAX_HOLD_S = 90.0

# Let go once the level has not moved for this long.  It means the essence ran
# out, since a press that can be afforded lands within a second or two.
IDLE_GIVE_UP_S = 8.0


class SummoningTask(Task):
    name = "W6 Summoning"
    world = 6
    description = ("Buys the summoning familiar up to 25/25 while its cost is "
                   "reset. Skips itself when it is maxed or still on cooldown.")
    requirements = [
        "Have World 6 town unlocked (the town teleport is free)",
        "Have the Summoning sanctuary unlocked",
        "Enough summoning essence to buy levels -- it stops when you run out",
    ]
    # Dominated by the hold, which is luck: a couple of seconds to twenty per
    # level, times however many levels are left.  The scheduler replaces this
    # with the observed median once there are runs to go on.
    nominal_seconds = 120.0

    location = Location(
        world=6,
        map_name="World 6 Town",
        via_town=True,
    )

    def __init__(self, **kw):
        self._summary = None
        self._gained = 0

    def asleep(self):
        """Maxed, or the costs have not reset -- both answered from the save."""
        return savefile.summoning_familiar_skip_reason()

    def _camera(self):
        import mss

        import idleon_hoops_bot as hoops
        rect = _window.acquire(lock=False)
        return hoops.Camera(mss.mss(), rect), rect

    def can_run(self):
        # Only that the game is reachable.  Everything else this task needs --
        # standing at the sanctuary, the screen being open -- it creates itself
        # in run(), because the teleport does not land at the sanctuary and
        # there is nothing here for can_run() to usefully insist on.
        try:
            self._camera()
        except RuntimeError as e:
            raise Blocked(str(e))

    def _click(self, clicker, rect, cam, xy, settle=0.0):
        clicker.click_at(rect["left"] + cam.to_screen(xy[0]),
                         rect["top"] + cam.to_screen(xy[1]),
                         time.perf_counter() + 0.25)
        if settle:
            time.sleep(settle)

    def run(self, stop=None):
        cam, rect = self._camera()
        clicker = _input.Clicker()
        stopping = (lambda: bool(stop and stop()))

        # ---- walk to the sanctuary ------------------------------------
        frame, _ = cam.grab()
        if not V.at_sanctuary(frame):
            yield Progress("walking left to the sanctuary")
            for _ in range(WALK_TRIES):
                if stopping():
                    return
                self._click(clicker, rect, cam, V.WALK_LEFT_XY, WALK_SETTLE)
                frame, _ = cam.grab()
                if V.at_sanctuary(frame):
                    break
            else:
                raise Blocked(
                    "could not reach the summoning sanctuary -- the rune "
                    "pillars never came into view after walking left")
        yield Progress("at the sanctuary")

        # ---- open the summoning screen --------------------------------
        if stopping():
            return
        yield Progress("opening the summoning screen")
        self._click(clicker, rect, cam, V.ENTRANCE_XY)
        for _ in range(OPEN_TRIES):
            frame, _ = cam.grab()
            if V.find_familiar(frame) is not None:
                break
            if stopping():
                return
            time.sleep(OPEN_POLL)
        else:
            raise Blocked("the summoning screen did not open")

        # ---- bring up the familiar's panel ----------------------------
        spot = V.find_familiar(frame)
        yield Progress(f"hovering the familiar at {spot}")
        clicker.move(rect["left"] + cam.to_screen(spot[0]),
                     rect["top"] + cam.to_screen(spot[1]))
        time.sleep(0.6)

        frame, _ = cam.grab()
        btn = V.find_upgrade_button(frame)
        if btn is None:
            # Never press a guessed position.  The buttons on this panel spend
            # essence that took days to earn, and the cost of doing nothing is
            # one visit against a cost window that is still open.
            raise Blocked(
                "the familiar's UPGRADE button did not appear -- not pressing "
                "a guessed position. Nothing was spent")

        # ---- hold, watching the level ---------------------------------
        start = savefile.summoning_familiar_level()
        start = int(start) if start is not None else 0
        yield Progress(f"holding UPGRADE from level {start} of {V.MAX_LEVEL}")

        state = {"level": start, "last": time.perf_counter(),
                 "mask": V.level_mask(frame)}

        def done():
            f, _ = cam.grab()
            m = V.level_mask(f)
            now = time.perf_counter()
            if V.level_changed(state["mask"], m):
                state["mask"] = m
                state["level"] += 1
                state["last"] = now
            if state["level"] >= V.MAX_LEVEL:
                return True
            if stopping():
                return True
            # Nothing for a while means the essence ran out.  looks_maxed() is
            # deliberately NOT consulted here: it has never been seen against a
            # real maxed panel, and a false positive would end the hold with
            # the familiar half bought.
            return now - state["last"] > IDLE_GIVE_UP_S

        held, _ = _input.hold_until(
            clicker,
            rect["left"] + cam.to_screen(btn[0]),
            rect["top"] + cam.to_screen(btn[1]),
            done, MAX_HOLD_S)

        reached = state["level"]
        self._gained = reached - start
        yield Progress(f"held for {held:.1f}s, level {start} -> {reached}")

        if reached >= V.MAX_LEVEL:
            self._summary = (f"W6 Summoning: familiar maxed at "
                             f"{V.MAX_LEVEL}/{V.MAX_LEVEL} "
                             f"(+{self._gained} this run)")
        elif self._gained:
            self._summary = (f"W6 Summoning: familiar {reached}/{V.MAX_LEVEL} "
                             f"(+{self._gained}), stopped early - probably out "
                             f"of essence")
        else:
            self._summary = (f"W6 Summoning: nothing bought, familiar still "
                             f"{reached}/{V.MAX_LEVEL} - out of essence?")

    def report(self, steps, seconds):
        return Result(ok=True,
                      summary=self._summary or "W6 Summoning: finished",
                      detail={"levels_gained": self._gained},
                      seconds=seconds)
