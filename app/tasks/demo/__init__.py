"""
One fake task, for trying the window out without the game.

TEMPORARY -- DELETE ME
----------------------
It touches nothing: no window, no mouse, no game.  It waits five seconds and
says so while it waits, which is enough to exercise a run from end to end --
the metro line, the activity log, the run report, and whatever the program
shows when a run finishes.

To remove it: delete this folder and the demo lines in core/registry.py.
"""

import time

from core.task import Param, Progress, Result, Task


class DemoTask(Task):
    name = "Test task (5s)"
    world = 0
    description = ("A pretend task that waits five seconds and finishes. "
                   "Nothing is clicked and the game is never touched.")
    requirements = ["Nothing -- this is a test task and does not use the game"]
    params = [
        Param("run_seconds", "Wait for", "int", default=5,
              minimum=1, maximum=120, unit="s",
              help="Seconds before it finishes"),
    ]
    run_seconds = 5
    nominal_seconds = 5.0

    def run(self, stop=None):
        end = time.perf_counter() + self.run_seconds
        while time.perf_counter() < end:
            if stop and stop():
                yield Progress("stopped early")
                return
            left = max(0, int(end - time.perf_counter()))
            yield Progress(f"working... {left}s left")
            time.sleep(min(1.0, max(0.05, end - time.perf_counter())))

    def report(self, steps, seconds):
        return Result(ok=True,
                      summary=f"{self.name}: finished after {seconds:.0f}s",
                      seconds=seconds)
