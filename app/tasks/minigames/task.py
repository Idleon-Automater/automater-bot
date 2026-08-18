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
import time

from core.navigate import Location, Navigator
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
    description = ("Travels to Winding Willows and plays Throwy Darts, aiming "
                   "for nine bullseyes in a row. Reads the wind and platform "
                   "on every throw, and keeps playing after the trophy to use "
                   "the remaining darts.")
    requirements = [
        "Costs one teleport to reach Winding Willows",
    ]
    # How many games, and nothing else.  A game ends when the darts run out,
    # and the next one is gated by a cooldown that grows to about 15 minutes --
    # so "how many" is the only thing worth choosing, and the waiting is the
    # bot's problem rather than the user's.
    params = [
        Param("max_score", "Stop at score", "int", default=500,
              minimum=1, maximum=5000,
              advise_above=500,
              advice=("Above the leaderboard range. A score this high may "
                      "flag the account as automated -- I would advise "
                      "against it."),
              help="The nine-streak trophy is earned along the way; the game "
                   "then plays on until the darts run out"),
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
        # Measured against _dev/darts_cooldown.png: the countdown sits just
        # above the dart.  Without this, standing on the right map with the
        # game not yet ready looked exactly like standing on the wrong map,
        # and the bot travelled -- spending a teleport on the one action that
        # could never help.
        cooldown_check=prompt.cooldown_at,
    )

    def __init__(self, games=1, max_score=500, **kw):
        super().__init__(**kw)
        self.games = games             # None = until stopped
        self.max_score = max_score
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
            already_playing=on_screen, should_stop=stopping,
            max_score=self.max_score))

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
    description = ("Travels to Valley of the Beans and plays Swishy Hoops, "
                   "timing each shot against the moving hoop. Stops at your "
                   "chosen score by deliberately missing, so the run ends "
                   "exactly there.")
    requirements = [
        "Costs one teleport to reach Valley of the Beans",
    ]
    # 55 is a hard ceiling, not a preference: past it the hoop moves faster
    # than the shot can be planned, so every further throw is a coin flip that
    # costs a life.  The spinner will not go higher.
    #
    # A shot limit used to sit here too and has been removed: the score is
    # already the gate, and a second cap only gave two ways to say one thing.
    params = [
        Param("max_score", "Stop at score", "int", default=55,
              minimum=1, maximum=500,
              advise_above=55,
              advice=("Above the leaderboard range. A score this high may "
                      "flag the account as automated -- I would advise "
                      "against it."),
              help="Past 55 the hoop moves faster than a shot can be planned"),
        Param("games", "Games to play", "int", default=1,
              minimum=1, maximum=50, allow_unlimited=True,
              governs_endless=True,
              help="The bot claims the points, waits out the cooldown and "
                   "starts again. No limit = keep going until you stop it."),
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
        # There were TWO of these, and the second silently replaced the first,
        # so the guard it contained never ran once.  Merged into one.
        #
        # The prompt covers the court, so the "is Swishy Hoops on screen?"
        # check refuses the exact state run() exists to clear.  That is not a
        # harmless refusal: the prompt stays open, and an open prompt blocks
        # the map, so every later task in the list fails to travel too.  One
        # task declining to start took the whole run down with it.
        #
        # The other half -- refusing games != 1 -- is gone rather than merged,
        # because the exit-and-re-enter loop it was waiting for now exists
        # below.
        try:
            cam, _rect = self._camera()
        except RuntimeError as e:
            raise Blocked(str(e))
        frame, _ = cam.grab()
        if prompt.find_yes(frame):
            return
        # More than one game means entering from the map is part of the job, so
        # requiring hoops to be on screen already would refuse the very mode
        # that does not need it.  Same reasoning as darts.
        if self.games != 1:
            return
        super().can_run()

    # How long to keep trying to get back in between games.  The cooldown
    # grows to about 15 minutes, so this allows for it plus room to spare.
    REENTER_TIMEOUT_S = 1200.0
    REENTER_POLL_S = 20.0

    def _reenter(self, cam, rect, clicker):
        """
        Get back onto the court after exiting.  Yields log lines.

        Deliberately does NOT read the cooldown timer.  `prompt.cooldown_at`
        is calibrated against the dart entrance, and the hoop is a different
        sprite at a different offset -- reusing those numbers here would be
        guessing at geometry nobody has measured.  Trying the door instead
        needs no calibration and answers the same question: if the click opens
        something, it was ready; if nothing happens, it was not.
        """
        nav = Navigator(rect, clicker)
        waited = 0.0
        while waited <= self.REENTER_TIMEOUT_S:
            frame, _ = cam.grab()
            if minigame.classify(frame, cam) == self.kind:
                yield Progress("back on the court")
                return True
            if prompt.find_yes(frame):
                # The question is already up; answering it is the way in.
                answered = []
                prompt.confirm(cam, rect, clicker,
                               log=lambda m: answered.append(m))
                for line in answered:
                    yield Progress(line)
                continue

            seen = nav.entrance_visible(self.location)
            if seen is None:
                yield Progress("cannot see the hoop from here - stopping the "
                               "queue rather than clicking a guessed spot")
                return False
            try:
                nav.click_entry(self.location)
            except Blocked:
                pass          # on cooldown, or the click landed early
            time.sleep(2.0)

            frame, _ = cam.grab()
            if prompt.find_yes(frame) or \
                    minigame.classify(frame, cam) == self.kind:
                continue      # the loop top will finish letting us in

            if waited == 0.0:
                yield Progress("hoops is not ready yet (cooldown) - waiting")
            time.sleep(self.REENTER_POLL_S)
            waited += self.REENTER_POLL_S

        yield Progress(f"gave up waiting to re-enter after "
                       f"{self.REENTER_TIMEOUT_S / 60:.0f} minutes")
        return False

    def run(self, stop=None):
        cam, rect = self._camera()
        cfg = hoops.load_config()
        clicker = _input.Clicker()
        stopping = (lambda: bool(stop and stop()))

        played = 0
        best = 0
        while self.games is None or played < self.games:
            if stopping():
                break
            if played:
                # Between games: back out to the map and in again.  Only after
                # the first, because the first game is entered by the normal
                # travel step before run() is ever called.
                ok = yield from self._reenter(cam, rect, clicker)
                if not ok:
                    break
            played += 1
            if self.games != 1:
                yield Progress(f"--- game {played}"
                               + (f" of {self.games}" if self.games else "")
                               + " ---")

            # Clicking the basketball only asks the question; the game starts
            # when the green answer is clicked.  Harmless when there is no
            # prompt -- the case whenever the run began already on the court.
            answered = []
            prompt.confirm(cam, rect, clicker,
                           log=lambda m: answered.append(m))
            for line in answered:
                yield Progress(line)

            engine = EngineRun(lambda: hoops.run(
                cam, cfg, score=self.score, max_shots=self.max_shots,
                max_score=self.max_score, should_stop=stopping))

            for line in engine.lines():
                yield Progress(line)
            engine.raise_if_failed()
            r = engine.result
            if r:
                best = max(best, r[0] if isinstance(r, (list, tuple)) else 0)

            # Claim the points before anything else runs.  Skipped when the
            # user stopped: the panic key means stop touching the game, not
            # press one more button.
            if stopping():
                break
            claimed = []
            prompt.claim_and_exit(cam, rect, clicker,
                                  log=lambda m: claimed.append(m))
            for line in claimed:
                yield Progress(line)

        if self.games != 1:
            self._summary = (f"Swishy Hoops: {played} game(s)"
                             + (f", best score {best}" if best else ""))

        if engine.result:
            shots, made, score = engine.result
            pct = (100.0 * made / shots) if shots else 0.0
            self._summary = (f"Hoops: {made}/{shots} made ({pct:.0f}%), "
                             f"score {score}")
