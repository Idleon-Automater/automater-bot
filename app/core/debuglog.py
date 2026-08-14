"""
A plain text trace of what the window did, for bugs that only happen by hand.

Some failures cannot be reproduced in a test: a real mouse drag runs a nested
event loop inside Qt and takes paths that constructed events never touch.  Two
fixes have already been shipped for the "dropping a task blanks the list" bug
on the strength of reasoning alone, and both missed -- so this records what
actually happens instead.

Writes to the user's APPDATA next to the run history.  Off unless switched on,
because a log nobody reads is just a file that grows.
"""

import os
import time

ENABLED = True          # set False once the drag bug is understood


def _path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "IdleonAutomator")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "debug.log")


def log(where, message="", **facts):
    """
    Record one moment, with whatever numbers make it interpretable.

    Never raises: a broken logger must not become the bug being investigated.
    """
    if not ENABLED:
        return
    try:
        bits = " ".join(f"{k}={v}" for k, v in facts.items())
        line = (f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} "
                f"{where:22} {message} {bits}\n")
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def start_session(note=""):
    log("session", f"--- started {note} ---")
