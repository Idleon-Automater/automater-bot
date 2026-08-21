"""
Read values out of the game's own save file.

The bot already does this for the sushi board (tasks/sushi/sushi_watch.py).
This is the general version, and it exists for one reason: to answer "is this
task on cooldown?" WITHOUT travelling to look.

The screen can only be read where the character is standing, so a cooldown
found that way has already cost a teleport and a map screen -- which is the
expensive part of a task that then does nothing.  The save is on disk and
answers in milliseconds from anywhere.

WHAT IS AND IS NOT IN HERE
--------------------------
Not everything the game knows is in the save.  Anything account-wide and
shared -- companions especially -- is fetched from Lava's servers at runtime
(the client calls getFreeCompanionRemainingTime and friends) and never lands
on disk.  Those cannot be answered from here at any price, and this module
says so rather than guessing.

STALENESS
---------
The save flushes every ~175 s, so every value here can be that far behind.
That is fatal for a sushi board mid-merge and irrelevant for a week-long
cooldown, which is the only thing this is used for.
"""

import glob
import os
import re
import shutil
import tempfile
import time

SAVE_DIR = os.path.join(os.environ.get("APPDATA", ""),
                        "legends-of-idleon", "Local Storage", "leveldb")

WEEK_S = 604800.0
DAY_S = 86400.0


# The save is ~5 MB across several files and every read copies them all, so a
# caller that polls -- and the run list polls, to keep a countdown fresh --
# must not pay that each time.  Cached briefly: the game only flushes every
# ~175 s, so anything shorter than that is re-reading the same bytes.
_CACHE_S = 30.0
_cache = {"at": 0.0, "text": None}


def _newest_text(save_dir=SAVE_DIR, max_age=_CACHE_S):
    """The largest readable save blob as ascii, or None."""
    now = time.time()
    if max_age and _cache["text"] is not None and now - _cache["at"] < max_age:
        return _cache["text"]
    text = _read_newest(save_dir)
    _cache["at"] = now
    _cache["text"] = text
    return text


def _read_newest(save_dir=SAVE_DIR):
    files = []
    for pat in ("*.ldb", "*.log"):
        files += glob.glob(os.path.join(save_dir, pat))
    if not files:
        return None
    files.sort(key=os.path.getmtime)
    tmp = tempfile.mkdtemp(prefix="idleon_save_")
    best = None
    try:
        for f in files:
            dst = os.path.join(tmp, os.path.basename(f))
            try:
                shutil.copy2(f, dst)
                text = open(dst, "rb").read().decode("ascii", "ignore")
            except OSError:
                continue
            if best is None or len(text) > len(best):
                best = text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return best


def raw(key, text=None, window=4000):
    """The raw text following the LAST `y<len>:<key>` marker, or None."""
    text = _newest_text() if text is None else text
    if not text:
        return None
    marker = "y%d:%s" % (len(key), key)
    i = text.rfind(marker)
    if i < 0:
        return None
    return text[i + len(marker):i + len(marker) + window]


# `z` is integer zero in this format and leaving it out of the pattern does not
# yield a zero, it yields nothing -- which silently shifts every later index.
# That bug cost the sushi reader a 120-long array parsed as 60; see
# tasks/sushi/sushi_watch.py.
_TOKEN = re.compile(r"(a|h|z|i-?\d+|d-?[\d.eE+-]+|R\d+|y\d+)")


def values(key, text=None, window=20000):
    """
    `key`'s array as nested lists, or None.

    Only arrays: an object (`b`) is a different shape and the callers that
    need one -- TimeAway is the only one so far -- read it by name instead.
    """
    body = raw(key, text, window)
    if body is None:
        return None
    stack, cur, started = [], None, False
    for t in _TOKEN.findall(body):
        if t == "a":
            new = []
            if cur is not None:
                cur.append(new)
                stack.append(cur)
            cur, started = new, True
        elif t == "h":
            if not started:
                break
            if stack:
                cur = stack.pop()
            else:
                return cur
        elif t[0] == "y":
            break                      # ran into the next key
        elif cur is not None:
            if t == "z":
                cur.append(0)
            elif t[0] == "i":
                cur.append(int(t[1:]))
            elif t[0] == "d":
                cur.append(float(t[1:]))
            else:
                cur.append(t)          # a string reference; kept as-is
    return cur


def time_away(text=None):
    """
    TimeAway as {name: number}.  Mostly unix seconds, but not all of them --
    ShopRestock and PostOfficeRefresh are seconds REMAINING, not a moment.
    """
    body = raw("TimeAway", text, window=3000)
    if body is None:
        return {}
    out = {}
    # Letters only.  Allowing digits let the name run on into the NEXT key and
    # its inline data -- "Meals" came back as "Mealsaai114i114i114..." -- and
    # every real field here is a plain word: GlobalTime, ShopRestock, BookLib.
    for name, val in re.findall(r"y\d+:([A-Za-z_]+)(d-?[\d.eE+-]+|z)", body):
        out[name] = 0.0 if val == "z" else float(val[1:])
    return out


def weekly_reset_seconds(text=None):
    """
    Seconds until the game's weekly rollover, or None.

    The client draws this as 604800 - (GlobalTime mod 604800): the week is a
    fixed grid on unix time, not something that starts when a player does.
    """
    ta = time_away(text)
    gt = ta.get("GlobalTime")
    if not gt:
        return None
    return WEEK_S - (gt % WEEK_S)


def summoning_cost_reset_seconds(text=None):
    """
    Seconds until the Summoning upgrade costs reset, or None.

    Straight from the client, which draws the countdown as

        timeDisp2(TimeAway.ShopRestock + 86400 * Summon[3][3])

    -- ShopRestock being the seconds left in the current day and Summon[3][3]
    the whole days after it.  Checked against the game's own display: the save
    gave 2d 0h 57m against a screenshot reading 2d 1h 3m taken six minutes
    earlier.
    """
    text = _newest_text() if text is None else text
    ta = time_away(text)
    shop = ta.get("ShopRestock")
    summon = values("Summon", text)
    if shop is None or not summon or len(summon) < 4 or len(summon[3]) < 4:
        return None
    days = summon[3][3]
    if not isinstance(days, (int, float)):
        return None
    return float(shop) + DAY_S * float(days)


# Which entry of Summon[0] is the familiar's level, and how high it goes.
#
# Found by watching rather than by guessing: the whole save was snapshotted,
# one familiar level was bought in-game, and the save snapshotted again.
# Summon[0][2] went 0 -> 1 and nothing else in that array moved -- which also
# matches the game showing "Familiar Lv.0/25" beforehand.
#
# One observation, so treat it as strong rather than certain.  It is used only
# to SKIP a task, and the cost of being wrong is a wasted trip, not a wrong
# action: anything acting on this should still read the level off the screen
# once it has arrived.
FAMILIAR_INDEX = 2
FAMILIAR_MAX = 25


def summoning_familiar_level(text=None):
    """The familiar's level out of FAMILIAR_MAX, or None if unreadable."""
    summon = values("Summon", text)
    if not summon or not summon[0] or len(summon[0]) <= FAMILIAR_INDEX:
        return None
    lv = summon[0][FAMILIAR_INDEX]
    return lv if isinstance(lv, (int, float)) else None


def summoning_familiar_skip_reason(text=None):
    """
    Why the familiar task would do nothing, or None if it is worth running.

    THE LEVEL DECIDES.  THE COUNTDOWN ONLY EXPLAINS.
    -----------------------------------------------
    The first version had this backwards and greyed the task out whenever the
    cost countdown was running -- which is nearly always, and which is not a
    lockout.  The game's own panel says "Lv.1/25" and "COST: 1" and "Cost
    resets in 1days 4hr" all at once: the levels are buyable right now, at the
    cost shown, and the countdown says when that cost goes back to its floor.
    So a running countdown is no reason to skip anything.

    Only a maxed familiar is.  When it is maxed the countdown becomes the
    useful thing to say, because it is when there will be something to buy
    again -- so it is reported as the wait rather than as the reason.

    Confirmed by the player, and it is what makes this a weekly task rather
    than a one-off: at the reset the familiar drops back to level 0 AND the
    cost returns to its floor.  So "maxed" is never permanent and the
    countdown really is the time until there is work again -- which is the
    only reading under which putting this in a daily list makes sense.

    Unreadable answers "worth running": a wasted trip is a better failure than
    silently never running a task the user asked for, which is exactly the
    failure this function shipped with.
    """
    text = _newest_text() if text is None else text
    lv = summoning_familiar_level(text)
    if lv is None or lv < FAMILIAR_MAX:
        return None
    left = summoning_cost_reset_seconds(text)
    if left is not None and left > 0:
        # "resets", not "costs reset": the level goes back to 0 as well, so
        # this is when the whole task becomes available again rather than just
        # when it gets cheaper.
        return f"maxed - resets in {describe(left)}"
    return f"maxed at {int(lv)}/{FAMILIAR_MAX}"


def describe(seconds):
    """'2d 1h 3m' for a countdown, for logs and the UI."""
    if seconds is None:
        return "unknown"
    if seconds <= 0:
        return "ready"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"
