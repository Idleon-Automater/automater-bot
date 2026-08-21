"""
Fighting: go to a map and leave the character killing things there.

WHAT IT DOES
------------
    1. open the map, pick the world, double-click the destination's marker
    2. turn AUTO on, if it is not already
    3. wait for as long as you asked, or until you stop it
    4. turn AUTO off again on the way out

NO ARTWORK, FOR ANY MAP IN THE GAME
-----------------------------------
Every other travelling task in this program carries a captured picture of its
destination's marker.  This one carries none and reaches all 141 maps, because
the save already says where every marker is drawn -- see core/maps.py, which
found that out and checked it two independent ways.

So the map list is not a list somebody typed.  It is read from the game, which
means it cannot go stale, and a map added in a future update appears in the
dropdown without anyone doing anything.

WHY IT TURNS AUTO OFF AT THE END
--------------------------------
A task should hand the game back the way it found it.  Left on, the character
carries on attacking through whatever the next task in the list is doing --
which for a task that travels means swinging at the air in a town, and for one
that drags things about is a distraction the game does not need.
"""

import os
import time

import core.input as _input
import core.window as _window
from core import maps
from core.task import Blocked, Param, Progress, Result, Task

from . import vision as V

_HERE = os.path.dirname(os.path.abspath(__file__))
NAV = os.path.join(_HERE, "nav")

# Everything the dropdown offers, as "W3 - Equinox Valley".
#
# One list rather than a world dropdown that changes a map dropdown: the
# settings panel has no machinery for one choice depending on another, and a
# single list sorted by world reads the same way while needing none.


def _choices():
    out = []
    for world in range(1, maps.WORLDS + 1):
        for map_id, name in maps.maps_in(world):
            out.append(f"W{world} - {name}")
    return out


def _map_id_for(choice):
    """The map id behind a dropdown entry, or None."""
    if not choice or " - " not in choice:
        return None
    return maps.find_map(choice.split(" - ", 1)[1])


# After double-clicking a marker.  The map screen closes and the new map loads.
ARRIVE_S = 3.5

# Pressing AUTO, and checking it took.
AUTO_TRIES = 3
AUTO_WAIT_S = 1.5
AUTO_POLL = 0.2

# How often to look in on a long fight.  Nothing is being read closely -- this
# only notices AUTO switching itself off, or the game being closed.
WATCH_POLL_S = 20.0


class FightingTask(Task):
    name = "Fighting"
    # No world of its own: it goes wherever it is pointed, so it takes the
    # grey that core/worlds.py already keeps for a task that lives nowhere.
    world = 0
    description = ("Travels to a map you choose and fights there for as long "
                   "as you set.")
    requirements = [
        "Have the destination map unlocked -- the bot can select any marker, "
        "but the game refuses to teleport somewhere you have not reached",
        "Costs one teleport, unless the destination is a town",
    ]
    params = [
        Param("where", "Fight at", "choice",
              choices=_choices(), default="W1 - Blunder Hills",
              help="Every map the game draws a marker for, read from your "
                   "own save"),
        Param("max_minutes", "Fight for", "minutes", default=30,
              minimum=1, maximum=600, allow_unlimited=True,
              governs_endless=True,
              help="Blank = keep going until you stop it"),
    ]
    where = "W1 - Blunder Hills"
    max_minutes = 30

    # Travel plus however long was asked for.  The scheduler replaces this
    # with the observed median once there are runs to go on.
    nominal_seconds = 30 * 60.0

    # Its own route: ensure_at() travels to ONE fixed place per task, and this
    # task's destination is a setting.  can_run() stays permissive so the
    # shared machinery returns without travelling.
    location = None

    def __init__(self, **kw):
        self._summary = None
        self._fought = 0.0

    def estimate(self, history=None):
        if self.max_minutes is None:
            return 60 * 60.0             # endless: quote an hour so a list
        return self.max_minutes * 60.0 + 25.0     # reads as long, not instant

    @property
    def runs_forever(self):
        return self.max_minutes is None

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

    def _press_auto(self, cam, rect, clicker, want_on, stopping):
        """Set AUTO on or off.  True if it ended up that way."""
        for _ in range(AUTO_TRIES):
            frame, _ = cam.grab()
            if not V.hud_visible(frame):
                return False
            if V.auto_is_off(frame) != want_on:
                return True              # already as wanted
            clicker.click_at(rect["left"] + cam.to_screen(V.AUTO_XY[0]),
                             rect["top"] + cam.to_screen(V.AUTO_XY[1]),
                             time.perf_counter() + 0.25)
            deadline = time.perf_counter() + AUTO_WAIT_S
            while time.perf_counter() < deadline:
                if stopping():
                    return False
                frame, _ = cam.grab()
                if V.auto_is_off(frame) != want_on:
                    return True
                time.sleep(AUTO_POLL)
        return False

    def run(self, stop=None):
        # ensure_at() focuses the game and this task returns from it early, so
        # nothing else would -- and a capture of an overlapped window reads
        # whatever is covering it.
        _window.focus()
        cam, rect = self._camera()
        clicker = _input.Clicker()
        stopping = (lambda: bool(stop and stop()))

        map_id = _map_id_for(self.where)
        if map_id is None:
            raise Blocked(f"'{self.where}' is not a map this game knows about")
        spot = maps.marker_xy(map_id)
        if spot is None:
            raise Blocked(f"the game draws no map marker for {self.where}, "
                          f"so it cannot be selected from the map screen")
        world = maps.world_of(map_id)

        # ---- travel -----------------------------------------------------
        from core.navigate import Navigator
        nav = Navigator(rect, clicker)
        yield Progress(f"travelling to {self.where}")
        nav.open_map()
        nav.pick_world(world)
        yield Progress(f"double-clicking the marker at {spot}")
        # Double-click rather than select-then-TELEPORT: the player confirmed
        # both do the same thing, and one action cannot half-happen the way a
        # pair of them can.
        sx = rect["left"] + cam.to_screen(spot[0])
        sy = rect["top"] + cam.to_screen(spot[1])
        clicker.double_click(sx, sy)
        time.sleep(ARRIVE_S)
        if stopping():
            return

        # ---- start fighting ---------------------------------------------
        frame, _ = cam.grab()
        if not V.hud_visible(frame):
            raise Blocked("the quick-access bar is not on screen, so the AUTO "
                          "button cannot be found -- is the game showing a "
                          "full-screen panel?")
        if V.auto_is_on(frame):
            yield Progress("fighting is already on")
        else:
            yield Progress("turning fighting on")
            if not self._press_auto(cam, rect, clicker, True, stopping):
                raise Blocked(
                    f"could not turn fighting on -- the AUTO button still "
                    f"reads OFF after {AUTO_TRIES} presses "
                    f"(off score {V.auto_off_score(cam.grab()[0]):.2f})")

        # ---- wait --------------------------------------------------------
        budget = None if self.max_minutes is None else self.max_minutes * 60.0
        started = time.perf_counter()
        said = 0.0
        while not stopping():
            elapsed = time.perf_counter() - started
            if budget is not None and elapsed >= budget:
                break
            time.sleep(min(WATCH_POLL_S,
                           budget - elapsed if budget else WATCH_POLL_S))
            frame, _ = cam.grab()
            if not V.hud_visible(frame):
                yield Progress("lost sight of the quick-access bar - stopping")
                break
            if V.auto_is_off(frame):
                # It switched itself off -- death, a popup, a stray click.
                yield Progress("fighting switched off - turning it back on")
                if not self._press_auto(cam, rect, clicker, True, stopping):
                    yield Progress("could not turn it back on - stopping")
                    break
            elapsed = time.perf_counter() - started
            if elapsed - said >= 300:
                said = elapsed
                yield Progress(f"still fighting, {elapsed / 60:.0f} minutes in")

        self._fought = time.perf_counter() - started

        # ---- leave it as we found it -------------------------------------
        self._press_auto(cam, rect, clicker, False, lambda: False)
        mins = self._fought / 60.0
        self._summary = (f"Fighting: {mins:.0f} minute(s) at "
                         f"{self.where.split(' - ', 1)[-1]}")
        yield Progress(self._summary)

    def report(self, steps, seconds):
        return Result(ok=True,
                      summary=self._summary or "Fighting: finished",
                      detail={"seconds_fighting": round(self._fought, 1)},
                      seconds=seconds)
