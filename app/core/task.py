"""
What a task is.

A task is one automation the user can drag into a list and run: claim the
refinery, level the towers, play sushi.  This defines the contract so the UI can
show, schedule, estimate and report on any of them without knowing what they do.

THREE SHAPES, ONE CONTRACT
--------------------------
The tasks on the roadmap are not alike, and the protocol has to fit all three
without contorting any of them:

  * click sequence   cogs, chests, boats -- open a screen, click known things
  * state-gated      refinery, construction -- read the save, decide, then act
  * continuous loop  sushi, minigames -- run until a stopping condition

Sushi is the awkward one: it has no natural end, so `run()` is a generator
rather than a function.  It yields Progress as it goes, which lets the UI show a
live log and a moving estimate, and lets the scheduler stop it between steps
without killing it mid-drag.  A click sequence just yields once at the end.

WHY GENERATORS
--------------
The alternative -- a callback for progress -- means every task carries UI
plumbing, and stopping means a flag the task has to remember to check.  With a
generator the scheduler simply stops iterating: the task is suspended at a safe
point by construction, because that is where it chose to yield.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class Param:
    """
    One setting a task exposes, described well enough for the UI to draw it.

    Tasks declare these rather than the window hard-coding a form per task, so
    a new task arrives with its own settings already editable and the window
    never learns what a "max score" is.

    `maximum` is a real limit, not a hint: Swishy Hoops stops being winnable
    past 55, so the spinner will not go there.  `allow_unlimited` marks the
    settings that can be switched off entirely -- "run until I stop you" --
    which is what makes a task eligible to be the endless one at the end of a
    list.
    """

    name: str                              # attribute set on the task
    label: str                             # shown in the window
    kind: str = "int"                      # "int" | "minutes" | "bool" | "choice"
    default: Any = None
    # For kind="choice": the options offered, in the order they are shown.
    # Stored as the STRING the user picked rather than an index, so a list
    # saved today still means the same thing if the options are ever
    # reordered -- an index would silently start pointing at its neighbour.
    choices: list = field(default_factory=list)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    allow_unlimited: bool = False          # None means "no limit"
    # Whether leaving THIS setting unlimited makes the whole task endless.
    # Most unlimited settings do not: Swishy Hoops with no shot limit still
    # ends when the lives run out, and darts with no throw limit still plays
    # its run out.  Only a setting that removes the task's last stopping
    # condition earns this, because it decides whether the task may be queued
    # anywhere but last.
    governs_endless: bool = False
    unit: str = ""            # shown after the value, e.g. "s"
    help: str = ""

    def clean(self, value):
        """Coerce a value into range, or None for unlimited."""
        if value is None:
            if not self.allow_unlimited:
                return self.default
            return None
        if self.kind == "bool":
            return bool(value)
        if self.kind == "choice":
            # An unrecognised value falls back to the default rather than being
            # passed through: a saved list naming an option that no longer
            # exists should start at something valid, not carry a string no
            # task knows how to act on.
            return value if value in self.choices else self.default
        value = float(value) if self.kind == "minutes" else int(value)
        if self.minimum is not None:
            value = max(self.minimum, value)
        if self.maximum is not None:
            value = min(self.maximum, value)
        return value


@dataclass
class Progress:
    """One step of a running task, as reported to the UI."""
    message: str
    fraction: Optional[float] = None      # 0..1 where meaningful, else None
    detail: dict = field(default_factory=dict)


@dataclass
class Result:
    """What a task achieved.  Shown in the run summary."""
    ok: bool
    summary: str
    detail: dict = field(default_factory=dict)
    seconds: float = 0.0


class Blocked(Exception):
    """
    Raised by can_run() explaining why a task cannot start.

    A refused task is a normal outcome, not an error: the refinery may be on
    cooldown, the game may be on the wrong screen.  The UI shows the reason and
    moves to the next task rather than stopping the list.
    """


class Task:
    """
    Base class.  Subclasses set `name` and implement `run()`.

    Everything else has a default, so a simple click-sequence task is a few
    lines and only the awkward tasks pay for the awkward parts.
    """

    name: str = "unnamed"
    description: str = ""

    # Which world this task lives in.  Drives the colour it is drawn in and the
    # world filter; 0 means "has not said".
    world: int = 0

    # What must be true before this task will work, in the user's terms: which
    # screen is open, what has to be set up in the game first.  These are shown
    # in the UI *before* a run, which is the point -- can_run() catches the same
    # things, but only once the list is already going and only for conditions a
    # screenshot can see.  "Have a character standing at the station" is not
    # something the bot can check, so it has to be something the user is told.
    requirements: list[str] = []

    # Settings the user can change, declared for the UI to render.  Empty for a
    # task that has nothing worth adjusting.
    params: list[Param] = []

    def configure(self, values):
        """
        Apply user-chosen settings, each cleaned to its declared range.

        Unknown keys are ignored rather than raising: a saved task list may
        outlive the parameter it mentions, and refusing to load someone's whole
        list because one setting was renamed would be the wrong trade.
        """
        for p in self.params:
            if p.name in values:
                setattr(self, p.name, p.clean(values[p.name]))
        return self

    def settings(self):
        """The current value of every declared parameter."""
        return {p.name: getattr(self, p.name, p.default) for p in self.params}

    @property
    def runs_forever(self):
        """
        True when this task, as configured, has no stopping condition.

        Tasks whose endlessness is conditional override this -- darts only runs
        forever when it is BOTH endless and uncapped, and no declarative flag
        expresses that as clearly as the task saying so itself.
        """
        for p in self.params:
            if not p.governs_endless:
                continue
            v = getattr(self, p.name, p.default)
            # A tick-box says "endless" by being ON; a numeric limit says it by
            # being absent.  Both mean the same thing to a run list.
            if (v is True) if p.kind == "bool" else (v is None):
                return True
        return False

    # Rough seconds, used before any history exists.  The scheduler replaces
    # this with a measured median once the task has run a few times, because a
    # guessed constant ages badly and the user is shown these numbers.
    nominal_seconds: float = 30.0

    def can_run(self) -> None:
        """
        Raise Blocked(reason) if the task cannot start right now.

        Default: always runnable.  State-gated tasks override this -- it is
        where "the refinery is still on cooldown" belongs, so the check happens
        once, before anything is clicked.
        """
        return None

    def estimate(self, history: list[float] | None = None) -> float:
        """Seconds this run is expected to take."""
        if history:
            s = sorted(history)
            return s[len(s) // 2]
        return self.nominal_seconds

    def run(self, stop=None) -> Iterator[Progress]:
        """
        Do the work, yielding Progress as it goes.

        `stop` is a callable returning True when the user has asked to stop.
        Tasks that loop should check it where they already pause; tasks that
        just click a sequence can ignore it, since the scheduler will not
        interrupt between steps anyway.

        The final yield should carry enough detail for `report()`.
        """
        raise NotImplementedError

    def report(self, steps: list[Progress], seconds: float) -> Result:
        """
        Summarise a completed run.  Default: count the steps.

        Tasks with something better to say -- "claimed 3 salts", "merged 47
        sushi, top tier 51" -- override this.
        """
        return Result(ok=True,
                      summary=f"{self.name}: {len(steps)} step(s)",
                      seconds=seconds)


def run_task(task: Task, stop=None, on_progress=None) -> Result:
    """
    Run one task to completion, collecting progress.

    Blocked is caught and turned into a Result, not re-raised: a task refusing
    to run is information for the list, not a failure of the run.
    """
    t0 = time.perf_counter()
    try:
        task.can_run()
    except Blocked as e:
        return Result(ok=False, summary=f"{task.name}: skipped - {e}",
                      seconds=time.perf_counter() - t0)

    # A time limit is enforced here rather than inside each task, so "run sushi
    # for 30 minutes" costs the sushi task no code at all.  It works by folding
    # the deadline into the stop signal the task already honours, which means
    # the run ends at a point the task chose -- between cycles, never mid-drag.
    # Overshooting the limit by one step is deliberate: cutting a step in half
    # to hit a round number would leave the game mid-action.
    budget = None
    for p in getattr(task, "params", []):
        if p.kind == "minutes":
            minutes = getattr(task, p.name, None)
            if minutes:
                budget = t0 + float(minutes) * 60.0
            break

    def should_stop():
        if stop and stop():
            return True
        return budget is not None and time.perf_counter() >= budget

    steps: list[Progress] = []
    for step in task.run(stop=should_stop):
        steps.append(step)
        if on_progress:
            on_progress(step)
        if should_stop():
            break

    elapsed = time.perf_counter() - t0
    result = task.report(steps, elapsed)
    if budget is not None and time.perf_counter() >= budget:
        result.summary += " (time limit reached)"
    return result
