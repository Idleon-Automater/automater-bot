"""
Throwy Darts and Swishy Hoops as schedulable Tasks.

Both are thin wrappers.  The engines underneath are the working bots, copied
verbatim apart from one added `should_stop` check at the top of each main loop
-- the only change needed to make a Ctrl+C program stoppable from a button.

WHY ONE MODULE FOR TWO GAMES
----------------------------
They are not really two engines.  They share the window handling, the capture,
the click timing and the physics constants; `dartvision` imports its curve
fitting straight out of the hoops bot.  Splitting them into separate task
packages would mean either duplicating that shared half or inventing a third
package for it, so they live together and the file says why.

What differs is how tight the timing is -- hoops tolerates about +/-150 ms,
darts about +/-20 ms -- and that difference lives entirely inside the engines.
"""

import os
import sys

from core.navigate import Location
from core.streaming import EngineRun
from core.task import Blocked, Param, Progress, Result, Task
from . import prompt

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)          # the engine modules import each other flatly

# Same aliasing as the sushi port: the engines were written against flat names
# (`import gamewindow`), so point those names at their new homes in core/
# instead of editing working code.
import core.window as _window          # noqa: E402
import core.input as _input            # noqa: E402
import core.minigame as _minigame      # noqa: E402

sys.modules.setdefault("gamewindow", _window)
sys.modules.setdefault("clicker", _input)
sys.modules.setdefault("minigame", _minigame)

NAV = os.path.join(_HERE, "nav")

import minigame                        # noqa: E402
import darts_bot                       # noqa: E402
import idleon_hoops_bot as hoops       # noqa: E402


class _MinigameTask(Task):
    """Shared plumbing: acquire the window, check the right game is up, run."""

    kind = None                        # minigame.DARTS or minigame.HOOPS
    screen_name = "the minigame"

    def __init__(self, lock_window=False, at=(0, 0)):
        self.lock_window = lock_window
        self.at = at
        self._summary = None

    def _camera(self):
        import mss
        rect = _window.acquire(lock=self.lock_window,
                               x=self.at[0], y=self.at[1])
        sct = mss.mss()
        return hoops.Camera(sct, rect), rect

    def can_run(self):
        try:
            cam, _rect = self._camera()
        except RuntimeError as e:
            raise Blocked(str(e))
        frame, _ = cam.grab()
        if minigame.classify(frame, cam) != self.kind:
            raise Blocked(f"{self.screen_name} is not on screen "
                          f"({minigame.describe(frame, cam)})")

    def report(self, steps, seconds):
        return Result(ok=True,
                      summary=self._summary or f"{self.name}: finished",
                      seconds=seconds)


class DartsTask(_MinigameTask):
    name = "Throwy Darts"
    kind = minigame.DARTS
    screen_name = "Throwy Darts"
    world = 1
    description = ("Plays Throwy Darts, aiming for bullseyes and a 9-in-a-row "
                   "streak. Reads the wind and platform each throw.")
    requirements = [
        "Open Throwy Darts (World 1 town) and be on the "
        "throwing screen, not the menu",
        "Leave the game window visible and unobscured; it reads the screen",
    ]
    # How many games, and nothing else.  A game ends when the darts run out,
    # and the next one is gated by a cooldown that grows to about 15 minutes --
    # so "how many" is the only thing worth choosing, and the waiting is the
    # bot's problem rather than the user's.
    params = [
        Param("games", "Games to play", "int", default=1,
              minimum=1, maximum=50, allow_unlimited=True,
              governs_endless=True,
              help="The bot waits out the cooldown between games. "
                   "No limit = keep going until you stop it."),
    ]

    # A full darts run is a few minutes; replaced by the measured median once
    # the task has run a few times.
    nominal_seconds = 240.0

    # Winding Willows, not the World 1 town -- so this is the long route:
    # open the map, pick the world, double-click the map's own marker.  That
    # spends one of the daily teleports, which the UI says before running.
    location = Location(
        world=1,
        map_name="Winding Willows",
        via_town=False,
        map_icon=os.path.join(NAV, "map_darts.png"),
        entry_icon=os.path.join(NAV, "entry_darts.png"),
    )

    def __init__(self, games=1, **kw):
        super().__init__(**kw)
        self.games = games             # None = until stopped
        self.max_throws = None         # the engine takes one; unused

    def can_run(self):
        # More than one game means entering from the map is part of the job, so
        # requiring darts to be on screen already would refuse the very mode
        # that does not need it.
        if self.games != 1:
            try:
                self._camera()
            except RuntimeError as e:
                raise Blocked(str(e))
            return
        super().can_run()

    def run(self, stop=None):
        cam, _rect = self._camera()
        cfg = hoops.load_config()
        clicker = _input.Clicker()
        stopping = (lambda: bool(stop and stop()))

        frame, _ = cam.grab()
        on_screen = minigame.classify(frame, cam) == minigame.DARTS
        # play_endless handles one game as happily as many: it plays, exits,
        # waits out the cooldown and re-enters, stopping after `max_runs`.
        # Using it for every count keeps one code path instead of two.
        engine = EngineRun(lambda: darts_bot.play_endless(
            cam, cfg, clicker, max_runs=self.games,
            already_playing=on_screen, should_stop=stopping))

        for line in engine.lines():
            yield Progress(line)
        engine.raise_if_failed()

        r = engine.result
        if r:
            throws, bulls, best = r[0], r[1], r[2]
            runs = r[-1]
            self._summary = (f"Darts: {runs} game(s), {throws} throws, "
                             f"{bulls} bullseyes, best streak {best}")


class HoopsTask(_MinigameTask):
    name = "Swishy Hoops"
    kind = minigame.HOOPS
    screen_name = "Swishy Hoops"
    world = 1
    description = ("Plays Swishy Hoops, timing each shot against the moving "
                   "hoop. Tracks the score and stops when the lives run out.")
    requirements = [
        "Open Swishy Hoops (World 1 town) and be on the shooting screen",
        "Start from a fresh game, or set the current score so the aim is right",
        "Leave the game window visible and unobscured; it reads the screen",
    ]
    # 55 is a hard ceiling, not a preference: past it the hoop moves faster
    # than the shot can be planned, so every further throw is a coin flip that
    # costs a life.  The spinner will not go higher.
    #
    # A shot limit used to sit here too and has been removed: the score is
    # already the gate, and a second cap only gave two ways to say one thing.
    params = [
        Param("max_score", "Stop at score", "int", default=55,
              minimum=1, maximum=55,
              help="55 is the highest score worth attempting"),
        Param("games", "Games to play", "int", default=1,
              minimum=1, maximum=50, allow_unlimited=True,
              governs_endless=True,
              help="More than one needs the exit-and-wait loop, which is "
                   "built for Throwy Darts but not yet for Swishy Hoops."),
    ]

    nominal_seconds = 180.0

    # Valley of the Beans, reached the same way as darts.
    location = Location(
        world=1,
        map_name="Valley of the Beans",
        via_town=False,
        map_icon=os.path.join(NAV, "map_hoops.png"),
        entry_icon=os.path.join(NAV, "entry_hoops.png"),
    )

    def __init__(self, score=0, max_score=55, games=1, **kw):
        super().__init__(**kw)
        self.score = score
        self.max_score = max_score
        self.games = games
        self.max_shots = None          # the engine still takes one; unused

    def can_run(self):
        # Darts has the whole exit -> wait out the cooldown -> re-enter loop,
        # validated against a recording.  Hoops has none of it: no exit press,
        # no entry sprite located on the World 1 map, no cooldown read.  Saying
        # so here is better than starting a game that simply stops after one
        # round while the list waits for an "endless" task that ended.
        if self.games != 1:
            raise Blocked(
                "more than one game in a row is not built for Swishy Hoops "
                "yet -- it needs the exit, cooldown and re-enter loop that "
                "Throwy Darts has. Set Games to play to 1")
        super().can_run()

    def can_run(self):
        # The prompt covers the court, so the "is Swishy Hoops on screen?"
        # check refuses the exact state run() exists to clear.  That is not a
        # harmless refusal: the prompt stays open, and an open prompt blocks
        # the map, so every later task in the list fails to travel too.  One
        # task declining to start took the whole run down with it.
        try:
            cam, _rect = self._camera()
        except RuntimeError as e:
            raise Blocked(str(e))
        frame, _ = cam.grab()
        if prompt.find_yes(frame):
            return
        super().can_run()

    def run(self, stop=None):
        cam, rect = self._camera()
        cfg = hoops.load_config()
        stopping = (lambda: bool(stop and stop()))

        # Clicking the basketball only asks the question; the game starts when
        # the green answer is clicked.  Harmless when there is no prompt --
        # which is the case whenever the run began already on the court.
        answered = []
        prompt.confirm(cam, rect, _input.Clicker(),
                       log=lambda m: answered.append(m))
        for line in answered:
            yield Progress(line)

        engine = EngineRun(lambda: hoops.run(
            cam, cfg, score=self.score, max_shots=self.max_shots,
            max_score=self.max_score, should_stop=stopping))

        for line in engine.lines():
            yield Progress(line)
        engine.raise_if_failed()

        # Claim the points before anything else runs.  Skipped when the user
        # stopped the run: the panic key means stop touching the game, not
        # press one more button.
        if not stopping():
            claimed = []
            prompt.claim_and_exit(cam, rect, _input.Clicker(),
                                  log=lambda m: claimed.append(m))
            for line in claimed:
                yield Progress(line)

        if engine.result:
            shots, made, score = engine.result
            pct = (100.0 * made / shots) if shots else 0.0
            self._summary = (f"Hoops: {made}/{shots} made ({pct:.0f}%), "
                             f"score {score}")
