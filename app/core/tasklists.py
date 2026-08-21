"""
Named task lists, saved between sessions.

A list is an ordered sequence of (task, settings): "Swishy Hoops to 55, then
Sushi for 30 minutes, then Darts, then Sushi until I stop it".  Building one is
work, and the point of building it is to run it again tomorrow, so it is saved
under a name the user chose.

WHAT IS STORED, AND WHY IT IS ONLY THIS
---------------------------------------
Task names and setting values.  Nothing about the machine, the account, the
window, or where anything sits on screen -- so a saved list is shareable and
carries nothing personal even by accident.  Lists live in the user's APPDATA
rather than beside the program, which also means a release ships with none.

THE ENDLESS RULE
----------------
Only the last entry may be set to run forever.  Anything after it would never
start, so a list with an endless task in the middle is not a preference the
user holds -- it is a mistake, and one that looks like a hang rather than an
error.  `problems()` names it before the list is run rather than after.
"""

import json
import os


def _path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "IdleonAutomator")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "tasklists.json")


# Bumped only when the on-disk shape changes in a way older readers cannot
# understand.  Absent in files written before this existed, which is why every
# read treats a missing version as "the original shape" rather than an error.
FORMAT = 1

_unreadable = False          # see _read_all


def _read_all():
    """
    Every saved list, or {} if the file is missing or unreadable.

    A file that exists but cannot be parsed sets `_unreadable`, which stops
    save() from writing over it.  Without that, one truncated file -- a crash
    mid-write, a full disk -- read as "no lists yet", and the next save
    replaced the user's whole library with a single list.  Losing work to a
    crash is bad luck; overwriting it afterwards is our doing.
    """
    global _unreadable
    path = _path()
    if not os.path.exists(path):
        _unreadable = False
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        _unreadable = True
        return {}
    _unreadable = False
    if not isinstance(data, dict):
        return {}
    # Either shape is accepted: the original file was a bare mapping of name ->
    # entry, and the versioned one wraps that in {"version": n, "lists": {...}}.
    if "lists" in data and isinstance(data.get("lists"), dict):
        return data["lists"]
    return {k: v for k, v in data.items() if k != "version"}


def _write_all(data):
    """
    Replace the file, atomically, unless the old one could not be read.

    Written to a temporary file and moved into place, so an interrupted write
    leaves the previous file intact instead of a half-written one.  os.replace
    is atomic on Windows as well as POSIX.
    """
    if _unreadable:
        # Keep the damaged file: it is the only copy of whatever was in it, and
        # a person may be able to repair it by hand.
        path = _path()
        keep = path + ".corrupt"
        try:
            if os.path.exists(path) and not os.path.exists(keep):
                os.replace(path, keep)
        except OSError:
            raise IOError("the task list file could not be read and could not "
                          "be set aside; refusing to overwrite it")
    path = _path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": FORMAT, "lists": data}, f, indent=2)
    os.replace(tmp, path)


def names():
    """Every saved list, in the order they were created."""
    return list(_read_all().keys())


def load(name):
    """
    The entries of one saved list: [{"task": str, "params": {...}}, ...].

    Returns [] for a name that is not there, rather than raising: a list can be
    deleted while a window still shows its tab, and an empty list is a state the
    UI already handles.
    """
    entry = _read_all().get(name)
    return entry.get("entries", []) if isinstance(entry, dict) else []


def save(name, entries):
    """Store a list under a name, replacing any list already using it."""
    data = _read_all()
    data[name] = {"entries": entries}
    _write_all(data)


def rename(old, new):
    data = _read_all()
    if old in data and new and new != old:
        # Rebuilt in order rather than popped and re-added, so renaming a list
        # does not silently move it to the end of the user's tabs.
        data = {(new if k == old else k): v for k, v in data.items()}
        _write_all(data)


# What a brand-new install starts with: a working pair of lists to edit, not an
# empty window and a blank page.
#
# These are a real player's own Daily and Weekly, settings and all, rather than
# something composed to look tidy -- which is why Daily runs the minigames and
# sushi twice round. The two passes are deliberate: the second set of games
# happens after the first cooldown has expired, and the last sushi is left
# unlimited so the list ends by grinding until you stop it.
STARTER_LISTS = [
    ("Daily", [
        ("Refinery",      {"check_all_tabs": True}),
        ("Swishy Hoops",  {"games": 1, "max_score": 55}),
        ("Throwy Darts",  {"games": 1, "max_score": 250}),
        ("Sushi Station", {"max_minutes": 60.0}),
        ("Swishy Hoops",  {"games": 1, "max_score": 55}),
        ("Throwy Darts",  {"games": 1, "max_score": 240}),
        # Unlimited, and last: the endless slot a list is allowed exactly one
        # of, at the end.
        ("Sushi Station", {"max_minutes": None}),
    ]),
    ("Weekly", [
        ("Equinox",       {"dream": "Equinox Symbols"}),
        ("Swishy Hoops",  {"games": 1, "max_score": 48}),
        ("Throwy Darts",  {"games": 1, "max_score": 250}),
        ("Sushi Station", {"max_minutes": 45.0}),
    ]),
]


def seed_starters():
    """
    Create Daily and Weekly on a brand-new install.  Returns True if it did.

    Guarded by a flag rather than by "are there any lists?", because those are
    different questions.  Someone who deletes both lists on purpose has no
    lists -- and must not find them back the next time they open the program,
    nor after an update.  The flag records that the offer was made once.
    """
    from core import settings

    if settings.get("starter_lists_seeded"):
        return False
    settings.set("starter_lists_seeded", True)
    if names():
        return False              # already has lists; leave them entirely alone
    for name, entries in STARTER_LISTS:
        save(name, [{"task": t, "params": dict(p)} for t, p in entries])
    return True


def delete(name):
    data = _read_all()
    if data.pop(name, None) is not None:
        _write_all(data)


def problems(tasks):
    """
    Reasons this list would not do what the user meant.  Empty means fine.

    Takes built tasks rather than raw entries, because "runs forever" is a
    property of the settings applied to a task, not of the task itself.
    """
    issues = []
    for i, t in enumerate(tasks[:-1]):
        if getattr(t, "runs_forever", False):
            issues.append(
                f"{t.name} (#{i + 1}) is set to run forever, so nothing after "
                f"it would ever start. Give it a limit, or move it to the end.")
    return issues
