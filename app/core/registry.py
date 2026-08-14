"""
Which tasks exist, and how long they have taken before.

Discovery is explicit rather than magic: a task appears here because it was
added to `_TASKS`, not because a file was found on disk.  A scanner would make
a broken or half-written task package silently change what the UI offers, and
this is a program that clicks things in a live game.

History lives in the user's APPDATA, never beside the code -- so a release
carries no run history, and a user's timings are their own.
"""

import json
import os
import statistics
import time


def _history_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "IdleonAutomator")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "history.json")


def task_factories():
    """
    Name -> a callable that builds a fresh instance.

    Factories rather than classes so a task that needs arguments to build can
    still be offered -- the caller only ever asks for a name and gets a fresh
    instance back.
    """
    from tasks.sushi import SushiTask
    from tasks.minigames import DartsTask, HoopsTask
    from tasks.refinery import RefineryTask

    out = {cls.name: cls
           for cls in (SushiTask, DartsTask, HoopsTask, RefineryTask)}
    # TEMPORARY: one pretend task, so a run can be exercised without the game.
    # Delete tasks/demo/ and these three lines together.
    try:
        from tasks.demo import DemoTask
        out[DemoTask.name] = DemoTask
    except ImportError:
        pass
    return out


# Names tasks used to have.  A saved list stores the name, so renaming a task
# would silently drop it from lists people had already built.  Kept apart from
# the real table rather than folded into it -- the first attempt put them in
# the same dict and the renamed task appeared twice in the task palette.
RENAMED = {"W3 Refinery": "Refinery"}


def available_tasks():
    """One freshly built instance of every task, at its default settings."""
    return [make() for make in task_factories().values()]


def make_task(name, params=None):
    """
    Build one task by name with the given settings.

    Every entry in a run list goes through here, so each queued entry is its
    own object.  That matters because settings live on the instance: queueing
    "Sushi for 10 minutes" and "Sushi until stopped" in one list has to produce
    two different tasks, not one task configured twice.

    Returns None for a name that no longer exists, so a saved list that
    mentions a removed task loses that entry instead of failing to load.
    """
    factories = task_factories()
    make = factories.get(name) or factories.get(RENAMED.get(name, ""))
    if make is None:
        return None
    return make().configure(params or {})


def load_history():
    try:
        with open(_history_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def record_run(name, seconds, ok):
    """Append a run's duration so future estimates get better."""
    h = load_history()
    entry = h.setdefault(name, {"runs": [], "last": None})
    entry["runs"] = (entry["runs"] + [round(seconds, 1)])[-20:]
    entry["last"] = {"at": time.time(), "seconds": round(seconds, 1), "ok": ok}
    try:
        with open(_history_path(), "w") as f:
            json.dump(h, f, indent=2)
    except Exception:
        pass                       # never let bookkeeping break a run


def estimate_for(task):
    """Seconds this task is expected to take, from history where available."""
    runs = load_history().get(task.name, {}).get("runs", [])
    return task.estimate(runs) if runs else task.nominal_seconds


def format_eta(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"
