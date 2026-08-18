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
from core.streaming import EngineRun
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
    description = ("Sorts the grid, merges everything it can, refills with the "
                   "cook button, and repeats for as long as you set.")
    requirements = [
        "Have the World 7 town quick access unlocked (free route in; "
        "otherwise it must be reached through Sushi Perimeter)",
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

    # How long to wait when the board has nothing left to do, before looking
    # again.  Only reached in run-forever mode.  Fuel regenerates continuously,
    # so a few minutes is enough to have something to spend again.
    IDLE_WAIT_S = 300.0

    def __init__(self, max_minutes=30, lock_window=False):
        # This used to be `max_cycles`, which no parameter ever set -- so
        # `max_minutes` was stored as a stray attribute and the run loop, which
        # only looked at `max_cycles`, never had a time budget at all.  "Run for
        # 30 minutes" did nothing; runs ended when the board went quiet.
        self.max_minutes = max_minutes      # None = until stopped
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
        idle = 0
        started = time.monotonic()
        budget = None if self.max_minutes is None else self.max_minutes * 60.0

        while not (stop and stop()):
            if budget is not None and time.monotonic() - started >= budget:
                yield Progress(f"{self.max_minutes:.0f} minutes up - stopping")
                break
            cycles += 1

            board, _mask = sushi_bot.board_and_mask()
            if board is None:
                yield Progress("cannot read the board - stopping")
                break
            occupied = sum(1 for v in board if v >= 0)
            top = max((v for v in board if v >= 0), default=-1) + 1
            best_tier = max(best_tier, top)
            # The WHOLE board, not just how full it is.  See below.
            before_board = list(board)

            # One round of the engine's own loop, with its output routed into
            # the run log.
            #
            # This used to be a bare call, so everything the engine printed
            # went nowhere.  That hid the one line that explains a board which
            # does not change:
            #
            #   [Cycle] first drag (65 -> 68) changed NOTHING on the board.
            #           Drags are not registering - stopping rather than
            #           issuing 49 more.
            #
            # which is what the oven mitt does: with it ON, drags are accepted
            # and ignored.  From outside, that is indistinguishable from a
            # board with nothing left to merge -- and it got reported as
            # "nothing to do" against a board full of mergeable tiles.
            n_before = occupied
            engine = EngineRun(lambda: sushi_bot.cycle(
                rect, clicker, rounds=1,
                should_stop=lambda: bool(stop and stop())))
            for line in engine.lines():
                yield Progress(line)
            engine.raise_if_failed()

            board, _ = sushi_bot.board_and_mask()
            after = sum(1 for v in board if v >= 0) if board else n_before
            top = max((v for v in board if v >= 0), default=-1) + 1 if board else top
            best_tier = max(best_tier, top)

            yield Progress(
                f"cycle {cycles}: board {n_before} -> {after}, top tier {top}",
                detail={"cycle": cycles, "occupied": after, "top_tier": top})

            # PROGRESS IS THE BOARD'S CONTENTS, NOT HOW FULL IT IS.
            #
            # A cycle merges until nothing is left, then cooks the board back
            # to full -- so the occupied count ends where it started, every
            # time, no matter how much work was done.  Comparing counts made a
            # cycle of 64 merges that lifted tiers 27 through 35 report
            # "nothing changed", and then wait five minutes for fuel it did not
            # need.  One run spent 20 of its 36 minutes idling with a board
            # full of merges available.
            #
            # The tier list does change: merges leave higher tiers, cooking
            # refills the base. If that list is identical, nothing happened.
            after_board, _ = sushi_bot.board_and_mask()
            unchanged = (after_board is not None
                         and list(after_board) == before_board)

            if unchanged and cycles > 1:
                # A cycle that changed nothing means there is nothing to merge
                # and no fuel to make more with.  Whether that is "finished"
                # depends on what was asked for.
                if not self.runs_forever:
                    yield Progress("nothing changed - finished")
                    break
                # Run-forever was chosen, so this is a lull, not an ending:
                # fuel regenerates, and after a wait there will be sushi to
                # make again.  Stopping here made the setting a lie -- an
                # unlimited run ended after two cycles and eight minutes.
                idle += 1
                yield Progress(f"nothing to do - waiting "
                               f"{self.IDLE_WAIT_S / 60:.0f} min for fuel, "
                               f"then trying again (idle round {idle})")
                waited = 0.0
                # Slept in short steps so F6 still stops within a couple of
                # seconds rather than after five minutes.
                while waited < self.IDLE_WAIT_S and not (stop and stop()):
                    if budget is not None and \
                            time.monotonic() - started >= budget:
                        break
                    time.sleep(2.0)
                    waited += 2.0
                cycles -= 1        # a lull is not a cycle of work

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
