#!/usr/bin/env python3
"""
Mouse clicks placed at a chosen wall-clock instant.

pyautogui is not used here.  Its `click()` goes through a ~10 ms internal PAUSE,
a failsafe check and a couple of layers of indirection, and it gives no way to
find out when the event was actually posted.  This wraps SendInput directly and
returns the real send time, so timing error is measurable instead of assumed.

Accuracy comes from a two-stage wait: sleep until a few milliseconds out (cheap,
lets the CPU idle), then spin on perf_counter (expensive, but bounded).  On a
normal Windows desktop that lands within about a millisecond, which is far
tighter than the ~270 ms scoring window the planner aims at.
"""

import ctypes
import ctypes.wintypes as wt
import math
import random
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32_kernel = ctypes.WinDLL("kernel32", use_last_error=True)

# Ask for 1 ms timer resolution as well, so the coarse part of the wait is not
# wildly off either.  Harmless if it fails.
try:
    ctypes.WinDLL("winmm").timeBeginPeriod(1)
except Exception:
    pass

_INPUT_MOUSE            = 0
_MOUSEEVENTF_LEFTDOWN   = 0x0002
_MOUSEEVENTF_LEFTUP     = 0x0004
_MOUSEEVENTF_MOVE       = 0x0001

# Spin for the last 50 ms rather than 3.  `time.sleep` on Windows is quantised
# to the system timer, which is 15.6 ms unless something has raised it -- so a
# sleep that lands 3 ms short of the target can overshoot it by 15-30 ms, and
# the loop then returns late with no chance to recover.  Measured live, the
# click landed 1-32 ms after its planned instant, on windows as narrow as
# +/-12 ms; every one of those overruns was a guaranteed miss.  Busy-waiting
# 50 ms costs nothing here and is immune to the granularity.
_SPIN_MARGIN_S = 0.050


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG),
                ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor_pos():
    """Where the pointer is right now, so a move can start from the truth."""
    pt = _POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return (0, 0)
    return (int(pt.x), int(pt.y))


def _mouse_event(flags):
    return _INPUT(type=_INPUT_MOUSE,
                  mi=_MOUSEINPUT(0, 0, 0, flags, 0, None))


_THREAD_PRIORITY_TIME_CRITICAL = 15
_HIGH_PRIORITY_CLASS = 0x00000080


class _Realtime:
    """
    Raise this thread's priority for the duration of a timed wait.

    Busy-waiting is not by itself enough.  Measured in isolation `sleep_until`
    lands within 0.001 ms and SendInput costs 0.09 ms, yet the live bot reported
    a mean +11.7 ms with values spread 1.8-17.4 ms.  That spread is about one
    Windows scheduling quantum, which is the tell: the spin loop is being taken
    off-core mid-wait, because the same process is running mss captures and cv2
    filtering flat out between throws.  No amount of spinning fixes a thread
    that is not running.

    Scoped, and restored in a finally, so the process does not sit at raised
    priority while it does its (much heavier, and entirely untimed) vision work.
    """

    def __enter__(self):
        self.prev = None
        try:
            h = user32_kernel.GetCurrentThread()
            self.prev = user32_kernel.GetThreadPriority(h)
            user32_kernel.SetThreadPriority(h, _THREAD_PRIORITY_TIME_CRITICAL)
        except Exception:
            self.prev = None
        return self

    def __exit__(self, *exc):
        if self.prev is not None:
            try:
                user32_kernel.SetThreadPriority(
                    user32_kernel.GetCurrentThread(), self.prev)
            except Exception:
                pass
        return False


def sleep_until(t_target):
    """Block until perf_counter() >= t_target, then return the actual time."""
    with _Realtime():
        while True:
            remaining = t_target - time.perf_counter()
            if remaining <= 0:
                return time.perf_counter()
            if remaining > _SPIN_MARGIN_S:
                time.sleep(remaining - _SPIN_MARGIN_S)


class Clicker:
    def __init__(self, hold_s=None):
        # A brief hold; the game only reacts to the press, but a zero-length
        # click is occasionally coalesced away by the compositor.
        # A brief, slightly varied hold.  A constant 12 ms on every click for
        # hours is a signature in itself.
        self.hold_s = hold_s if hold_s is not None else random.uniform(0.010, 0.030)
        self._last_pos = None
        # Rolling record of how long SendInput itself takes.  Measured live it
        # is 1-17 ms against the game window, where offline it is 0.09 ms -- the
        # call blocks on the target's input processing, and that block is real
        # delay, not measurement overhead: throws whose send took >=10 ms scored
        # 50% against 23% for those under 5 ms, because the extra lateness was
        # compensating for a click that was otherwise too early.
        self._send_hist = []
        self.expected_send_s = 0.009      # seeded with the observed live median

    def move(self, x, y, human=True):
        """
        Move the pointer to (x, y).

        Humanised by default: an eased path with a slight arc and jitter rather
        than a teleport.  A single SetCursorPos jump is not something a hand
        produces, and this issues thousands of them per session.

        The starting point comes from Windows when this Clicker has not moved
        yet.  It used to fall back to a SetCursorPos teleport instead, and
        because each Clicker tracks its own last position, EVERY new instance
        teleported once: hoops alone builds three (the prompt, the engine, the
        exit), so a single game jumped the pointer three times.  Asking the OS
        where the cursor actually is costs one call and removes the whole
        class of jump, including the first move of a session.

        Safe for the timed clicks: `click_at` moves EARLY, with the target
        instant still hundreds of milliseconds away (measured leads are
        700-3500 ms), so the approach costs nothing at the moment that matters.
        The press itself remains exactly timed.
        """
        start = self._last_pos or _cursor_pos()
        if start == (x, y):
            self._last_pos = (x, y)
            return
        if human:
            human_move(start[0], start[1], x, y)
        else:
            user32.SetCursorPos(int(x), int(y))
        self._last_pos = (x, y)

    def click(self, x, y):
        """
        Move to (x, y) and click, with no timing requirement.

        `click_at` exists for the minigames, where the press has to land inside
        a +/-12 ms window and everything around it -- the early move, the warm
        SendInput, the measured send cost -- is there to defend that window.
        Pressing a menu button needs none of that, and calling click_at with an
        invented target time to get a plain click would be obscure about why.

        Still humanised: the approach is an eased path via `move`, and the hold
        is randomised, because a constant hold on every click for hours is a
        signature by itself.
        """
        self.move(x, y)
        time.sleep(random.uniform(0.040, 0.110))    # settle, as a hand would
        down = _mouse_event(_MOUSEEVENTF_LEFTDOWN)
        user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
        # The release must happen even if the wait is interrupted, or the
        # button stays down and the desktop is left with a stuck mouse.
        try:
            time.sleep(random.uniform(0.030, 0.075))
        finally:
            up = _mouse_event(_MOUSEEVENTF_LEFTUP)
            user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))

    def double_click(self, x, y):
        """
        Two presses close enough together that the game counts them as one
        double-click.

        Calling click() twice does not do this.  Each click settles before and
        after itself, which put the presses about half a second apart -- at or
        beyond the system double-click threshold, so the game saw two separate
        clicks and the world tab never teleported anywhere.  Here the move
        happens once, up front, and the two presses follow with only the gap a
        hand would leave.
        """
        self.move(x, y)
        time.sleep(random.uniform(0.040, 0.090))
        for i in range(2):
            down = _mouse_event(_MOUSEEVENTF_LEFTDOWN)
            user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
            try:
                time.sleep(random.uniform(0.028, 0.055))
            finally:
                up = _mouse_event(_MOUSEEVENTF_LEFTUP)
                user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))
            if i == 0:
                # Well inside the default 500 ms threshold, and varied so it is
                # not the same interval every time.
                time.sleep(random.uniform(0.055, 0.105))

    def click_at(self, x, y, t_target):
        """
        Press the left button as close to `t_target` as possible.
        Returns the perf_counter() reading taken immediately after SendInput.
        """
        self.move(x, y)                   # done early so it costs nothing later

        # Warm the input path a few ms early with a zero-pixel relative move.
        # SendInput's cost is in reaching the target's input queue, and the
        # first call after an idle gap pays the most; this takes that hit
        # before the clock matters instead of during the click.  A (0,0) MOVE
        # changes nothing on screen and the game ignores it.
        sleep_until(t_target - 0.004)
        warm = _mouse_event(_MOUSEEVENTF_MOVE)
        user32.SendInput(1, ctypes.byref(warm), ctypes.sizeof(_INPUT))

        woke = sleep_until(t_target)

        # Split the send error into its two halves and keep both.
        #
        # Live the click lands a mean +8.9 ms late with ample lead (777-3220 ms
        # measured), yet every offline reproduction of the wait -- idle, under
        # cv2 load, under allocation churn with and without GC, with and without
        # a priority boost -- returns within 0.001 ms, and SendInput itself
        # costs 0.09 ms.  Five hypotheses have now been tested against that
        # +8.9 ms and all five were wrong, so the next step is not another
        # guess: it is finding out which side of this line the time goes.
        self.last_wait_err = woke - t_target

        down = _mouse_event(_MOUSEEVENTF_LEFTDOWN)
        user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
        sent = time.perf_counter()
        self.last_send_ms = (sent - woke) * 1000.0

        # Keep a median of the recent send cost so the caller can aim EARLIER
        # by exactly that much.  A median, not a mean: the distribution is
        # skewed by occasional long calls and one 17 ms outlier should not drag
        # the next throw's aim point with it.
        self._send_hist.append(sent - woke)
        if len(self._send_hist) > 9:
            self._send_hist.pop(0)
        self.expected_send_s = sorted(self._send_hist)[len(self._send_hist) // 2]

        # The release must happen even if Ctrl+C lands inside the hold, or the
        # button stays down and the desktop is left with a stuck mouse.
        try:
            time.sleep(random.uniform(0.010, 0.030))
        finally:
            up = _mouse_event(_MOUSEEVENTF_LEFTUP)
            user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))
        return sent


def measure_timer_jitter(n=200, interval=0.05):
    """Diagnostic: how well sleep_until hits its target on this machine."""
    errs = []
    t = time.perf_counter() + 0.05
    for _ in range(n):
        t += interval
        errs.append((sleep_until(t) - t) * 1000.0)
    errs.sort()
    return {"median_ms": errs[n // 2], "p95_ms": errs[int(n * 0.95)],
            "max_ms": errs[-1]}


if __name__ == "__main__":
    print("timer jitter:", measure_timer_jitter())


def press_hold(clicker, x, y, seconds):
    """
    Move to (x, y), hold the left button down for `seconds`, release.

    Some buttons auto-repeat while held -- the sushi cook button fills the
    whole grid this way -- which is both simpler and far less input than
    issuing one click per cell.

    The release is in a finally so Ctrl+C cannot leave the button stuck down.
    """
    if clicker is not None:
        clicker.move(x, y)
    else:
        # No Clicker to remember the last position, so ask Windows: this used
        # to be a bare SetCursorPos, which teleported every time hold() was
        # called without one.
        sx, sy = _cursor_pos()
        human_move(sx, sy, x, y)
    time.sleep(random.uniform(0.04, 0.10))
    down = _mouse_event(_MOUSEEVENTF_LEFTDOWN)
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
    try:
        time.sleep(seconds)
    finally:
        up = _mouse_event(_MOUSEEVENTF_LEFTUP)
        user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))
    if clicker is not None:
        clicker._last_pos = (int(x), int(y))
    time.sleep(random.uniform(0.10, 0.25))


def hold_until(clicker, x, y, done, max_seconds=90.0):
    """
    Hold the left button at (x, y) until `done()` says stop, or time runs out.

    press_hold's fixed duration cannot serve a button whose job takes an
    unknown time.  The summoning familiar is the case: roughly one press in
    five counts, so buying it out takes anywhere from a couple of seconds to
    twenty, and both guessing long (wasted essence past the cap) and guessing
    short (stopping half done) are worse than watching.

    `done()` is called in a tight loop and is expected to block for about a
    frame -- a screen grab does, at 16.67 ms on a 60 Hz display -- so there is
    no sleep here.  Anything that returns instantly should add its own, or
    this spins.

    Returns (seconds held, whether done() ended it).  The release is in a
    finally: an exception, a stop request or Ctrl+C must not leave the mouse
    button stuck down on someone's game.
    """
    if clicker is not None:
        clicker.move(x, y)
    else:
        sx, sy = _cursor_pos()
        human_move(sx, sy, x, y)
    time.sleep(random.uniform(0.04, 0.10))
    down = _mouse_event(_MOUSEEVENTF_LEFTDOWN)
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
    finished = False
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < max_seconds:
            if done():
                finished = True
                break
    finally:
        up = _mouse_event(_MOUSEEVENTF_LEFTUP)
        user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))
    if clicker is not None:
        clicker._last_pos = (int(x), int(y))
    time.sleep(random.uniform(0.10, 0.25))
    return time.perf_counter() - t0, finished


def human_move(x0, y0, x1, y1, steps=None):
    """
    Move the pointer along an eased, slightly arced path with jitter.

    Shared by drags and by the approach before a timed click, so every pointer
    movement the bot makes has the same irregular character rather than only
    the drags.
    """
    dist = max(1.0, math.hypot(x1 - x0, y1 - y0))
    if dist < 3:
        user32.SetCursorPos(int(x1), int(y1))
        return
    steps = (steps if steps is not None
         else max(10, min(40, int(dist / 12) + random.randint(0, 4))))
    dx, dy = x1 - x0, y1 - y0
    bow = random.uniform(-1, 1) * min(9.0, dist * 0.05)
    px, py = -dy / dist, dx / dist
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)
        arc = math.sin(math.pi * t) * bow
        user32.SetCursorPos(
            int(round(x0 + dx * e + px * arc + random.uniform(-3.0, 3.0))),
            int(round(y0 + dy * e + py * arc + random.uniform(-3.0, 3.0))))
        time.sleep(random.uniform(0.006, 0.013))
    user32.SetCursorPos(int(round(x1 + random.uniform(-3, 3))),
                        int(round(y1 + random.uniform(-3, 3))))


def drag(clicker, x0, y0, x1, y1, steps=None, hold=None, settle=None):
    """
    Press at (x0,y0), move to (x1,y1), release.  Screen coordinates.

    Movement is stepped rather than teleported because the game tracks the
    pointer to decide what is being dragged; a single jump from source to
    destination can read as a click on each end, which in Sushi Station is a
    merge rather than a swap.

    The path and the timings are deliberately irregular.  A perfectly linear
    path traversed in identical time steps is not something a hand produces,
    and this issues hundreds of drags in a row.  So: an ease-in-out velocity
    profile, a slight arc off the straight line, per-step jitter, and randomised
    hold and settle times.  Still fast -- the point is to avoid being
    mechanically uniform, not to be slow.
    """
    dx, dy = x1 - x0, y1 - y0
    dist = max(1.0, math.hypot(dx, dy))
    steps = (steps if steps is not None
             else max(12, min(46, int(dist / 11) + random.randint(0, 4))))
    hold = hold if hold is not None else random.uniform(0.030, 0.065)
    settle = settle if settle is not None else random.uniform(0.045, 0.095)
    # Bow the path perpendicular to the direction of travel, a few pixels.
    bow = random.uniform(-1, 1) * min(9.0, dist * 0.05)
    px, py = -dy / dist, dx / dist

    user32.SetCursorPos(int(x0), int(y0))
    time.sleep(settle)
    down = _mouse_event(_MOUSEEVENTF_LEFTDOWN)
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
    try:
        time.sleep(hold)
        for i in range(1, steps + 1):
            t = i / steps
            # ease-in-out: slow at both ends, quick through the middle
            e = t * t * (3 - 2 * t)
            arc = math.sin(math.pi * t) * bow
            jx = random.uniform(-3.0, 3.0)
            jy = random.uniform(-3.0, 3.0)
            user32.SetCursorPos(int(round(x0 + dx * e + px * arc + jx)),
                                int(round(y0 + dy * e + py * arc + jy)))
            time.sleep(random.uniform(0.006, 0.013))
        # Land NEAR the target, not exactly on it.  Snapping to the precise
        # centre after a jittered path is the tell it was meant to remove --
        # cells are 47 px wide, so a few px off is still comfortably inside.
        user32.SetCursorPos(int(round(x1 + random.uniform(-3, 3))),
                            int(round(y1 + random.uniform(-3, 3))))
        time.sleep(hold)
    finally:
        up = _mouse_event(_MOUSEEVENTF_LEFTUP)
        user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))

    # Tell the Clicker where the pointer ended up.
    #
    # drag() moves with SetCursorPos directly, so without this the Clicker's
    # cached position is stale -- and Clicker.move() skips the move when it
    # thinks it is already there.  The next click then fires wherever the drag
    # finished.  Live effect: cooking worked in cycle 1 (no drags had run yet)
    # and silently clicked into the middle of the board for every cycle after,
    # pressing 13-18 times per cycle with the board unchanged and a full tank.
    if clicker is not None:
        clicker._last_pos = (int(x1), int(y1))

    time.sleep(random.uniform(settle * 0.6, settle * 1.4))
