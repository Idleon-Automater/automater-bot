"""
World 6 Summoning: buy the familiar out to 25/25 while its cost is reset.

WHAT IT DOES
------------
    1. travel to World 6 town (free -- the town teleport)
    2. walk left to the sanctuary, which the teleport does not land at
    3. click the rune pillars to open the summoning screen
    4. CLICK the familiar to select it, which brings up its panel
    5. HOLD the UPGRADE button, watching the level, and let go at 25/25

WHY THE COORDINATES ARE TRUSTED HERE AND NOWHERE ELSE
-----------------------------------------------------
This screen does not move.  The upgrade grid, the panel and the UPGRADE
button are drawn at the same place every time -- the familiar has been found
at (498,164), (500,165) and (498,167) across three separate live runs, which
is the +/-2 px the icons drift and nothing more.  So a fixed point is a fair
thing to click, and V.FAMILIAR_XY is used when the template cannot be found.

The world is a different matter and is never trusted that way: the camera
follows the character, so anything out there has to be located before it can
be clicked.  That is the whole reason the walk ends on a landmark rather than
after a set number of clicks.

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
from core.task import Blocked, Progress, Result, Task

from . import vision as V

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(_HERE, "nav")

# HOW MANY TIMES TO CLICK LEFT, AND WHY IT IS THIS SMALL
# ------------------------------------------------------
# Two, and the first version's twelve is what made this dangerous.  A click
# does not mean "walk a bit", it means "walk to there", so clicking again
# while the character is still moving sends it PAST the sanctuary -- and past
# the sanctuary is off the edge of the town onto the next map, which is a
# place nothing in the queue expects to be standing.  Reported: it "walked
# several times left instead of once, leading it to leave the town map and
# wander off".
#
# So: click once, then WATCH rather than click.  A second click only if the
# first plainly did not take, and never a third.
WALK_TRIES = 2
WALK_WATCH_S = 9.0        # how long to watch for the pillars after one click
WALK_POLL = 0.4

# The summoning screen takes a moment to draw -- reported at one to two
# seconds -- so this waits on the screen appearing rather than on a fixed
# sleep, which would either waste the fast case or click into a screen that
# has not drawn yet in the slow one.
OPEN_TRIES = 20
OPEN_POLL = 0.25

# Selecting the familiar, and waiting for its panel.  Generous, because the
# reported delay after a click here is one to two seconds and a click that has
# already worked costs nothing to wait on.
SELECT_TRIES = 3
SELECT_WAIT_S = 3.0
SELECT_POLL = 0.15

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

    # NO Location, and that is deliberate rather than an omission.
    #
    # ensure_at() offers one shape of journey: travel, then find the entrance
    # and click it.  This route does not have that shape -- the World 6
    # teleport lands somewhere the sanctuary cannot be seen from, so there is
    # a walk in the middle, and no entry template exists that is visible at
    # the point ensure_at would go looking for one.
    #
    # Rather than bend the shared machinery around one odd route, the task
    # drives its own: see _travel_to_sanctuary below.  can_run() stays
    # permissive so ensure_at returns immediately and does not travel first.
    location = None

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
        # Only that the game is reachable.  Everything else this task needs it
        # creates itself in run(), and this must NOT insist on being at the
        # sanctuary: ensure_at() asks can_run() first and treats a pass as
        # "already there", so a stricter check here would just make the shared
        # travel machinery attempt a journey it cannot complete.
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

    def _watch_for_pillars(self, cam, stopping, seconds=None):
        """
        Wait for the pillars to come into view.  True if they did.

        `seconds=None` rather than the constant as a default: a default is
        bound once when the function is defined, so writing WALK_WATCH_S there
        makes the constant look adjustable while quietly ignoring any change
        to it -- which is exactly how a test of this loop came out wrong.
        """
        seconds = WALK_WATCH_S if seconds is None else seconds
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            if stopping():
                return False
            frame, _ = cam.grab()
            if V.at_sanctuary(frame):
                return True
            time.sleep(WALK_POLL)
        return False

    def _travel_to_sanctuary(self, cam, rect, clicker, stopping):
        """
        World 6 town, then left to the sanctuary.  Yields Progress.

        Done here rather than through ensure_at() because of the walk in the
        middle -- see the note on `location`.  It also means the teleport
        happens unconditionally unless the pillars are ALREADY in view, which
        is the fix for the first live failure: can_run() passing was taken as
        "already at W6 Summoning" while the character stood in World 5 town,
        so nothing travelled and the walk then clicked at empty scenery.
        """
        from core.navigate import Navigator

        frame, _ = cam.grab()
        if V.at_sanctuary(frame):
            return                      # already standing in front of them

        yield Progress("travelling to World 6 town")
        nav = Navigator(rect, clicker)
        nav.open_map()
        nav.go_to_town(6)
        yield Progress("arrived in town")
        if stopping():
            return

        # One click, then watch.  A click is "walk to there", not "walk a
        # bit", so a second one issued while the character is still moving
        # carries it past the sanctuary and off the map entirely.
        for attempt in range(1, WALK_TRIES + 1):
            yield Progress("walking left to the sanctuary"
                           + (f" (attempt {attempt})" if attempt > 1 else ""))
            self._click(clicker, rect, cam, V.WALK_LEFT_XY)
            if self._watch_for_pillars(cam, stopping):
                return
            if stopping():
                return

    def run(self, stop=None):
        # ensure_at() focuses the game before it does anything, and this task
        # deliberately returns from it early -- so nothing had focused the
        # window, and a hover over an unfocused window draws no tooltip.  That
        # is what stopped the first live run that got this far: the familiar
        # was found and hovered, and its panel never appeared.
        _window.focus()
        cam, rect = self._camera()
        clicker = _input.Clicker()
        stopping = (lambda: bool(stop and stop()))

        # ---- get to the sanctuary -------------------------------------
        for step in self._travel_to_sanctuary(cam, rect, clicker, stopping):
            yield step
            if stopping():
                return
        frame, _ = cam.grab()
        score, which = V.sanctuary_score(frame)
        if not V.at_sanctuary(frame):
            # The score goes in the message on purpose.  This failed live once
            # with the pillars plainly on screen and the log said only that
            # they "never came into view", which cannot be told apart from
            # being in the wrong place -- a number says which it was.
            raise Blocked(
                f"could not reach the summoning sanctuary -- best landmark "
                f"match {score:.2f}, closest was {which}")
        yield Progress(f"at the sanctuary (matched {which} at {score:.2f})")

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

        # ---- select the familiar --------------------------------------
        #
        # SELECTED, not hovered.  Two live runs hovered it and its panel never
        # appeared -- best UPGRADE match 0.30, which is the score for "not on
        # screen at all" rather than for a panel drawn somewhere unexpected.
        # An upgrade in this grid is opened by clicking it, which is also what
        # the one to two second delay belongs to.
        spot = V.find_familiar(frame) or V.FAMILIAR_XY
        yield Progress(f"selecting the familiar at {spot}")
        sx = rect["left"] + cam.to_screen(spot[0])
        sy = rect["top"] + cam.to_screen(spot[1])

        btn = None
        for attempt in range(SELECT_TRIES):
            if attempt:
                yield Progress(f"panel did not open -- clicking again "
                               f"({attempt + 1} of {SELECT_TRIES})")
            clicker.click_at(sx, sy, time.perf_counter() + 0.25)
            deadline = time.perf_counter() + SELECT_WAIT_S
            while time.perf_counter() < deadline:
                if stopping():
                    return
                frame, _ = cam.grab()
                btn = V.find_upgrade_button(frame)
                if btn is not None:
                    break
                time.sleep(SELECT_POLL)
            if btn is not None:
                break
        if btn is None:
            # Never press a guessed position.  The buttons on this panel spend
            # essence that took days to earn, and the cost of doing nothing is
            # one visit against a cost window that is still open.
            seen, _ = V.upgrade_button_score(frame)
            raise Blocked(
                f"the familiar's panel did not open -- best UPGRADE match "
                f"{seen:.2f} after {SELECT_TRIES} clicks. Not pressing a "
                f"guessed position, nothing was spent")

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
