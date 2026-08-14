"""
Small persistent facts about this installation.

Not settings the user edits -- those live with each task -- but things the
program needs to remember between launches, like whether the disclaimer has
been shown before.  Kept in the user's APPDATA with the run history and saved
lists, so a fresh copy of the program starts fresh for whoever runs it.
"""

import json
import os


def _path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "IdleonAutomator")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")


def _read():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get(key, default=None):
    return _read().get(key, default)


def set(key, value):
    """Store one fact.  Never raises: losing a preference must not stop a run."""
    try:
        data = _read()
        data[key] = value
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
