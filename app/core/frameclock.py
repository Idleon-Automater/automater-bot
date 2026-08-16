"""
Where we are in the display's 60 Hz cycle, from capture timestamps alone.

WHY THIS EXISTS
---------------
The binding constraint on darts is not aim, wind or platform reading -- all
three are now good enough.  It is WHEN THE GAME NOTICES THE CLICK.

SendInput blocks until the game's thread consumes the click, and the game polls
input once per rendered frame.  Measured over 51 throws: 0.66 to 17.41 ms,
flat across the range.  That is not overhead to optimise away; it is a uniform
wait for the next poll.  And it is fatal, because it is the same size as the
whole aiming window -- across two runs, every throw whose send exceeded the
planner's window missed, 0 for 7, with no exceptions.

THE OBSERVATION THIS IS BUILT ON
--------------------------------
Screen capture cannot sample faster than the display refreshes: mss returns a
frame every 16.67 ms regardless of how small the region is.  That looked like
the end of the idea -- until the timestamps turned out to be phase-LOCKED to
the refresh rather than merely rate-limited:

    phase concentration R : 0.984      (1.0 = perfect lock, 0 = uniform)
    phase sd              : 0.47 ms
    p5..p95               : -0.91 .. +0.43 ms
    dropped grabs         : 0 of 399

So a grab timestamp is a free, sub-millisecond reading of the refresh phase.
Posting every click at the SAME phase makes the wait-for-poll constant instead
of uniform -- and a constant is just latency, which the planner already
subtracts.  The 17 ms spread collapses to its jitter.

WHAT THIS DOES NOT KNOW
-----------------------
The game's poll phase relative to ours.  It does not need to: holding OUR phase
fixed holds the offset between the two fixed as well, whatever it happens to
be.  The remaining constant is absorbed by the existing latency calibration,
which is measured from where darts actually land.
"""

import math
import time

# 60 Hz.  Measured at 16.669 ms mean over 400 grabs, sd 0.581.
PERIOD = 1.0 / 60.0

# Below this concentration the timestamps are not usefully locked and callers
# should fall back to unquantised timing rather than trust a phase.
MIN_LOCK = 0.80

# Samples kept for the circular mean.  A couple of seconds' worth: long enough
# to average out grab jitter, short enough to follow slow clock drift.
WINDOW = 120


class FrameClock:
    """
    Tracks the refresh phase from timestamps handed to `observe`.

    Phase is averaged on the circle, not on the raw modulo.  A phase sitting
    near the wrap point produces samples at both 0.1 ms and 16.6 ms, whose
    arithmetic mean is 8 ms -- the opposite side of the cycle, and exactly
    wrong.  The circular mean has no such seam.
    """

    __slots__ = ("_sin", "_cos", "_n")

    def __init__(self):
        self._sin = []
        self._cos = []
        self._n = 0

    def observe(self, t):
        """Record a capture timestamp."""
        a = (t % PERIOD) / PERIOD * 2.0 * math.pi
        self._sin.append(math.sin(a))
        self._cos.append(math.cos(a))
        if len(self._sin) > WINDOW:
            del self._sin[0]
            del self._cos[0]
        self._n += 1

    @property
    def samples(self):
        return len(self._sin)

    @property
    def lock(self):
        """0..1 concentration.  1.0 means every sample shares a phase."""
        n = len(self._sin)
        if n < 8:
            return 0.0
        s = sum(self._sin) / n
        c = sum(self._cos) / n
        return math.hypot(s, c)

    @property
    def locked(self):
        return self.lock >= MIN_LOCK

    def phase(self):
        """Mean phase, in seconds into the cycle, or None if not locked."""
        if not self.locked:
            return None
        n = len(self._sin)
        a = math.atan2(sum(self._sin) / n, sum(self._cos) / n)
        return (a / (2.0 * math.pi)) % 1.0 * PERIOD

    def next_slot(self, after, offset=0.0):
        """
        The first time at or after `after` that sits at the tracked phase.

        `offset` shifts the target within the cycle, so a caller can aim just
        before or just after the boundary without this class having to know
        which side the game polls on.

        Returns None when not locked, which callers must treat as "time it the
        old way" rather than as an error -- an unlocked clock is a reason to
        stop quantising, not a reason to stop throwing.
        """
        ph = self.phase()
        if ph is None:
            return None
        target = (ph + offset) % PERIOD
        base = after - (after % PERIOD)
        t = base + target
        while t < after:
            t += PERIOD
        return t

    def snap(self, t, offset=0.0):
        """
        The phase slot NEAREST `t`, which may be slightly before it.

        Used where moving the click a whole frame later would cost more than
        moving it half a frame either way -- the aim sweeps continuously, so
        the nearest slot is the smallest change to the plan.
        """
        ph = self.phase()
        if ph is None:
            return None
        nxt = self.next_slot(t, offset)
        prev = nxt - PERIOD
        return prev if (t - prev) < (nxt - t) else nxt
