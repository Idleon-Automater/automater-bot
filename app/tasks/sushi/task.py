"""
Sushi Station as a schedulable Task.

This is a THIN wrapper.  The engine underneath is the working bot, copied
verbatim: sort and compact, merge runs of three (pairs at the top tiers where
nothing cascades), hold the cook button, repeat.  Nothing about how it plays is
changed here -- the port exists to prove the protocol fits a real automation,
and changing both at once would make any failure impossible to attribute.

WHAT THE WRAPPER ADDS
---------------------
  * can_run()  refuses before touching anything if the station is not on screen
  * run()      yields one Progress per cycle, so the UI has a live log and the
               scheduler can stop between cycles rather than mid-drag
  * report()   says what was achieved in the terms the user cares about --
               top tier reached, merges made -- not "N steps"

The engine's own loop is `cycle()`, which runs to completion.  This drives it
one round at a time instead, so that a user pressing Stop is never waiting for
an unbounded loop to notice.
"""

import os
import sys
import time

from core.navigate import Location
from core.task import Blocked, Param, Progress, Result, Task

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)          # the engine modules import each other flatly

# The engine modules were written against flat names (`import gamewindow`).
# Alias them to their new homes in core/ rather than editing the engine: the
# whole point of this port is that the automation is unchanged, so any bug
# found here belongs to the wrapper, not to code that was already working.
import core.window as _window           # noqa: E402
import core.input as _input             # noqa: E402
import core.minigame as _minigame       # noqa: E402

sys.modules.setdefault("gamewindow", _window)
sys.modules.setdefault("clicker", _input)
sys.modules.setdefault("minigame", _minigame)

import sushi_bot                        # noqa: E402
import sushisim as M                    # noqa: E402
import sushivision as V                 # noqa: E402


class SushiTask(Task):
    name = "Sushi Station"
    world = 7
    description = ("Sorts the grid, merges what can be merged, refills with the "
                   "cook button, and repeats until nothing is left to do.")
    requirements = [
        "Have the World 7 town teleport unlocked (it is the free route in; "
        "without it the station has to be reached through Sushi Perimeter)",
        "Turn the oven mitt OFF -- while it is on, drags do not register and "
        "the bot will appear to do nothing",
        "Set the cook button to the tier you want made; a lower tier is "
        "cheaper in fuel and runs longer",
    ]
    # Sushi has no natural end -- it can always merge and cook again -- so the
    # only sensible limit is time.  Left blank it runs until stopped, which is
    # what makes it a candidate for the endless slot at the end of a list.
    params = [
        Param("max_minutes", "Run for", "minutes", default=30,
              minimum=1, maximum=600, allow_unlimited=True,
              governs_endless=True,
              help="Blank = keep going until you stop it"),
    ]
    max_minutes = 30

    # A cycle is dozens of drags; measured runs sit around a minute or two, but
    # the scheduler replaces this with the observed median after a few runs.
    nominal_seconds = 90.0

    # Reached through the World 7 town rather than through Sushi Perimeter:
    # the town teleport is free, and the station's entrance is right there, so
    # the route never spends the daily allowance and never has to pick one
    # marker out of the twenty-five on the map view.  Sushi Perimeter is only
    # needed by someone whose town shortcut is not unlocked yet.
    location = Location(
        world=7,
        map_name="World 7 Town",
        via_town=True,
        entry_icon=os.path.join(_HERE, "nav", "entry_bubble.png"),
        popup_icon=os.path.join(_HERE, "nav", "popup_sushi.png"),
    )

    def __init__(self, max_cycles=0, lock_window=False):
        self.max_cycles = max_cycles        # 0 = until nothing is left
        self.lock_window = lock_window

    def can_run(self):
        frame, _scale = V.grab_station()
        if frame is None:
            raise Blocked("the Sushi Station is not on screen")
        if not V.load_tiers() and not V.load_digit_masks():
            raise Blocked("no tier templates or digit masks available")
        # is_station() alone said yes on a screen that was not the station, and
        # the run then "succeeded" against an empty board.  The grid is what
        # the task actually needs, so require that a board can be read from it.
        board, _mask = sushi_bot.board_and_mask()
        if board is None or not any(v >= 0 for v in board):
            raise Blocked("the Sushi Station looks open but no sushi grid "
                          "could be read -- is the station really on screen?")

    def run(self, stop=None):
        import gamewindow
        from clicker import Clicker

        rect = gamewindow.acquire(lock=self.lock_window, x=0, y=0)
        clicker = Clicker()

        best_tier = 0
        cycles = 0
        while not (stop and stop()):
            if self.max_cycles and cycles >= self.max_cycles:
                break
            cycles += 1

            board, _mask = sushi_bot.board_and_mask()
            if board is None:
                yield Progress("cannot read the board - stopping")
                break
            occupied = sum(1 for v in board if v >= 0)
            top = max((v for v in board if v >= 0), default=-1) + 1
            best_tier = max(best_tier, top)

            # One round of the engine's own loop.
            n_before = occupied
            sushi_bot.cycle(rect, clicker, rounds=1,
                            should_stop=lambda: bool(stop and stop()))

            board, _ = sushi_bot.board_and_mask()
            after = sum(1 for v in board if v >= 0) if board else n_before
            top = max((v for v in board if v >= 0), default=-1) + 1 if board else top
            best_tier = max(best_tier, top)

            yield Progress(
                f"cycle {cycles}: board {n_before} -> {after}, top tier {top}",
                detail={"cycle": cycles, "occupied": after, "top_tier": top})

            if after == n_before and cycles > 1:
                yield Progress("nothing changed - finished")
                break

        self._best_tier = best_tier
        self._cycles = cycles

    def report(self, steps, seconds):
        top = getattr(self, "_best_tier", 0)
        cycles = getattr(self, "_cycles", len(steps))
        return Result(
            ok=True,
            summary=f"Sushi: {cycles} cycle(s), top tier {top}",
            detail={"cycles": cycles, "top_tier": top},
            seconds=seconds)
