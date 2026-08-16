#!/usr/bin/env python3
"""
Exact simulation of Idleon's "Throwy Darts" minigame.

Transcribed from `distBuild/static/game/N.js`, `_event_Darts` (offset ~9316309)
and the input handler `_event_Keyhoopsdarts` (~9297353).  Engine timing is
shared with hoops, so the constants in `hoopsim.py` still hold: a fixed 10 ms
step, `G += 1.3` every 20 ms, and
`Trigg(mode, t, i) = sin|cos(i*G + 360*t)` in degrees.

--------------------------------------------------------------------------------
WHY THIS GAME IS EASIER THAN HOOPS
--------------------------------------------------------------------------------
Both the wind and the platform's horizontal position are scaled by the
difficulty counter `[160]`:

    wind    ~ [160]/([160]+40)
    plat x  ~ [160]/([160]+50)

`[160]` rises on essentially every *scoring* throw -- bullseyes included.  It
is not a miss counter, and it cannot be held at zero by playing well: measured
over a real run it climbed on 21 of 29 throws, 13 of which were bullseyes.  In
practice difficulty tracks throw count.

That sets the real target.  Nine bullseyes in a row means being accurate from
d=0 through d~10-15, not merely at d=0 -- so wind and platform travel have to be
handled, not avoided.  Only `[159]`, the streak itself, is reset by a miss.

--------------------------------------------------------------------------------
AIM ANGLE  ([155], degrees, while no dart is in flight)
--------------------------------------------------------------------------------
    amp = 38 + 15*[160]/([160]+30)
    i   = 1.5 + (2.1 + max(0, ([160]-50)/75)) * ([160]/([160]+25))
    [155] = amp * sin(i*G + 360*0.71*[145]) - 20

At difficulty 0: amp = 38, i = 1.5, so the angle sweeps -58 deg .. +18 deg at
1.5 * 65 = 97.5 deg/s -- a 3.69 s period.  Negative is upward (screen y is down).

--------------------------------------------------------------------------------
LAUNCH  (on the click step; there is NO throw animation)
--------------------------------------------------------------------------------
    [138] = round([143] + 117)                  # dart x
    [139] = round([144] + 72*sin([155]) - 56)   # dart y
    [140] = 5.2*cos([155])                      # vx
    [141] = 5.2*sin([155])                      # vy

Unlike hoops, which gave 48 steps (480 ms) between the click and the ball
leaving the hand, the dart launches immediately.  Click latency is therefore a
much larger share of the error budget here.

--------------------------------------------------------------------------------
FLIGHT, per 10 ms step
--------------------------------------------------------------------------------
    [138] += [140]
    [139] += [141]
    [141] += 0.033                      # gravity
    [140]  = max(1, [140] + [152]/600)  # wind x   (both zero at difficulty 0)
    [141] += [153]/750                  # wind y

--------------------------------------------------------------------------------
BOARD AND SCORING
--------------------------------------------------------------------------------
Hit when  916 <= [138] <= 960  and  89 <= [139] <= 495.

The board is a stack of seven horizontal bands, not a bullseye circle -- the
only `Math.sqrt` in the whole function is for the wind indicator.  `[157]` holds
the six y boundaries; the band index is the first `i` with `[139] < [157][i]`:

    band 0 or 6 -> grey,  streak reset
    band 1 or 5 -> tan,   streak reset
    band 2 or 4 -> green, streak reset
    band 3      -> RED, bullseye, [159] += 1

`[157]` is never assigned inside `_event_Darts`, so the boundaries below were
measured off a real board instead (see `read_board`).  They should be re-read
from the screen rather than trusted as constants.

One trap: a dart landing within 5 px of a boundary is *randomly* nudged 5-6 px
to one side, and within 2 px there is a 10% chance of a penalty deflection.  So
the aim point must clear both edges of the red band by more than 5 px -- see
SAFE_MARGIN.
"""

import math

from hoopsim import STEP_S, TICK_S, G_PER_TICK, Clock, PHASE_DEG_S

# ── Launch ────────────────────────────────────────────────────────────────────
DART_SPEED      = 5.2
LAUNCH_DX       = 117      # [138] = [143] + 117
LAUNCH_DY       = -56      # [139] = [144] + 72*sin(theta) - 56
LAUNCH_ARM      = 72       # the 72*sin(theta) term
GRAVITY         = 0.033

# ── Board (from the hit test in _event_Darts) ─────────────────────────────────
BOARD_X_MIN, BOARD_X_MAX = 916, 960
BOARD_Y_MIN, BOARD_Y_MAX = 89, 495
BOARD_EDGE_X             = 925   # left of this, boundary nudging applies
DIVIDER_NUDGE            = 5     # |dy| < 5 from a boundary -> randomly nudged

# Measured band boundaries ([157]).  Defaults only -- prefer read_board().
DEFAULT_BOUNDS = (162.0, 221.0, 271.0, 311.0, 365.0, 424.0)
BULLSEYE_BAND  = 3

# Keep this far clear of the red band's edges so the nudge cannot apply.
SAFE_MARGIN = 8.0

# ── Aim angle and difficulty ──────────────────────────────────────────────────
AIM_OFFSET_DEG = -20.0


def aim_params(difficulty):
    """Return (amplitude_deg, rate_deg_s) for the aim oscillation."""
    d = float(difficulty)
    amp = 38.0 + 15.0 * d / (d + 30.0)
    i = 1.5 + (2.1 + max(0.0, (d - 50.0) / 75.0)) * (d / (d + 25.0))
    return amp, i * G_PER_TICK / TICK_S


def wind(difficulty):
    """Peak wind magnitude at this difficulty; exactly zero at 0."""
    d = float(difficulty)
    return 30.0 * d / (d + 40.0)


def platform_x(difficulty):
    """Platform x.  Pinned to 300 while the difficulty is 0."""
    d = float(difficulty)
    return 300.0 - 1.5 * d      # the random term is scaled by d/(d+50)


class AimClock:
    """The aim angle over time: amp*sin(rate*t + phase) - 20, in degrees."""

    def __init__(self, phase_deg, t0, difficulty=0):
        self.phase_deg = phase_deg
        self.t0 = t0
        self.amp, self.rate = aim_params(difficulty)

    def angle(self, t):
        return (self.amp * math.sin(math.radians(
            self.phase_deg + self.rate * (t - self.t0))) + AIM_OFFSET_DEG)


class Throw:
    """Result of one simulated dart."""

    __slots__ = ("t0", "angle", "hit_y", "band", "bullseye", "margin", "steps")

    def __init__(self, t0, angle, hit_y, band, bullseye, margin, steps):
        self.t0 = t0
        self.angle = angle
        self.hit_y = hit_y          # y where it met the board, or None
        self.band = band            # 0..6, or None if it missed the board
        self.bullseye = bullseye
        self.margin = margin        # px clear of the nearest red-band edge
        self.steps = steps

    def __repr__(self):
        return (f"<Throw angle={self.angle:+.1f} y={self.hit_y} "
                f"band={self.band} margin={self.margin:.1f}>")


def band_of(y, bounds):
    """The game's band index for a dart landing at y."""
    for i, b in enumerate(bounds):
        if y < b:
            return i
    return len(bounds)


def simulate(t_click, aim, plat_y, difficulty=0, bounds=DEFAULT_BOUNDS,
             wind_x=0.0, wind_y=0.0, max_steps=600, plat_x=None):
    """
    Throw at `t_click` and follow the dart until it meets the board or is lost.

    `plat_y` is [144].  `plat_x` is [143]: pass the MEASURED value when there
    is one, because the formula below is only the deterministic half of it.
    The game adds `350 * randFloat(...) * d/(d+50)` on top, which is worth
    +/-32 px at d=5 and +/-68 px at d=12 -- and launch x sets the LENGTH of the
    flight, so an error there changes how long gravity acts and lifts or drops
    every landing regardless of when the click happened.  Falling back to the
    formula keeps old callers working; it does not make them right.
    """
    theta = aim.angle(t_click)
    th = math.radians(theta)

    px = platform_x(difficulty) if plat_x is None else plat_x
    x = round(px + LAUNCH_DX)
    y = round(plat_y + LAUNCH_ARM * math.sin(th) + LAUNCH_DY)
    vx = DART_SPEED * math.cos(th)
    vy = DART_SPEED * math.sin(th)

    for n in range(1, max_steps + 1):
        x += vx
        y += vy
        vy += GRAVITY
        vx = max(1.0, vx + wind_x / 600.0)
        vy += wind_y / 750.0

        if BOARD_X_MIN <= x <= BOARD_X_MAX and BOARD_Y_MIN <= y <= BOARD_Y_MAX:
            b = band_of(y, bounds)
            lo, hi = bounds[BULLSEYE_BAND - 1], bounds[BULLSEYE_BAND]
            margin = min(y - lo, hi - y)
            return Throw(t_click, theta, y, b, b == BULLSEYE_BAND, margin, n)

        if x > BOARD_X_MAX or y > 600:
            return Throw(t_click, theta, None, None, False, -999.0, n)

    return Throw(t_click, theta, None, None, False, -999.0, max_steps)


# ── Planner ───────────────────────────────────────────────────────────────────

# One frame at 60 fps.  SendInput blocks until the game's thread consumes the
# click, and the game polls input once per rendered frame, so the launch lands
# uniformly anywhere in this window after the event is posted -- measured live
# at 1.35-17.27 ms over 44 throws, flat across the range with a hard ceiling at
# one frame.  It is not overhead that can be optimised away; it is when the game
# notices.  A warm-up SendInput was tried and changed nothing (mean 8.9 -> 10.2).
#
# MEASURED AGAIN over 51 throws (run of 2026-08-15): min 0.66, mean 9.50,
# max 17.41 ms -- and 3 of the 51 exceeded 16.7.  Two of those three are the
# only unexplained misses of that run's low-difficulty stretch:
#
#     throw 4   send 17.32 ms   landed -25 px   MISS
#     throw 5   send 17.41 ms   landed -21 px   MISS
#     throw 14  send 16.82 ms   landed +23 px   bullseye (lucky)
#
# So one frame is the shape of the distribution but not its bound: the click
# can miss a poll and land in the next frame.  Planning to exactly 16.7 leaves
# those throws outside the guarantee, which is precisely where they landed.
# 0.018 covers the observed maximum with a little room.
#
# The cost is real -- a wider requirement means fewer valid plans, so more
# high-difficulty throws fall back to single-instant.  That is the right trade:
# the streak has to be won below d~15, where windows were +/-10-26 ms and can
# afford the extra millisecond, and above that band it is unreachable anyway.
LAUNCH_SPREAD_S = 0.018


def plan_windy(t_from, aim, plat_y, difficulty, bounds, winds,
               dt=0.002, horizon=None, spread=LAUNCH_SPREAD_S,
               min_margin=None, align=None, plat_x=None):
    """
    Plan a throw that lands in the red band for EVERY candidate wind.

    The wind's direction is readable off the HUD but its magnitude is not: the
    game re-rolls it as `30 * randFloat(0.6,1) * d/(d+40)` every throw, so only
    a range is knowable in advance.  Rather than bet on the midpoint, this
    demands a click that works across the whole range -- and returns nothing
    when no such click exists, which is the honest answer at high difficulty
    where the spread alone exceeds the red band.

    Returns (Throw, half_window_seconds) or (None, 0.0).
    """
    if horizon is None:
        _amp, rate = aim_params(difficulty)
        horizon = 360.0 / rate

    # The click is posted at `t`, but the dart leaves on whichever frame the
    # game next polls input -- anywhere in [t, t+spread].  So every candidate is
    # judged on its WORST launch across that window as well as its worst wind.
    # Sampling the two ends and the middle is enough: the landing height is
    # smooth and monotonic in the launch time over 17 ms, which is a small
    # fraction of the 2.2-3.7 s aim period, so an interior point cannot be worse
    # than both ends.
    offsets = (0.0, spread * 0.5, spread) if spread > 0 else (0.0,)
    # `min_margin=0` asks for the best shot available rather than a safe one.
    # Meaningless as a plan, but the right question when a dart is going to be
    # spent regardless -- see the burn path in darts_bot.
    if min_margin is None:
        min_margin = SAFE_MARGIN

    # `align` restricts candidates to launch instants the click can actually
    # produce.  Without it the planner picks a time to 2 ms and the click then
    # lands anywhere in the following frame, because the game polls input once
    # per redraw -- so the plan is precise about something we do not control.
    # Measured consequence over two runs: every throw whose send exceeded the
    # planner's window missed, 0 for 7, no exceptions.
    #
    # With the refresh phase known (core/frameclock.py) the reachable launch
    # times are a 16.67 ms grid, and searching only those makes the plan
    # describe what will really happen.  `spread` then covers the phase jitter
    # (sub-millisecond) rather than a whole frame.
    step = dt
    if align is not None:
        period, phase = align
        aligned = t_from - (t_from % period) + (phase % period)
        while aligned < t_from:
            aligned += period
        t_from, step = aligned, period

    best = None
    t = t_from
    while t < t_from + horizon:
        worst = None
        for off in offsets:
            for wx, wy in winds:
                th = simulate(t + off, aim, plat_y, difficulty, bounds, wx, wy,
                              plat_x=plat_x)
                if not th.bullseye:
                    worst = None
                    break
                if worst is None or th.margin < worst.margin:
                    worst = th
            if worst is None:
                break
        if worst is not None and worst.margin >= min_margin:
            if best is None or worst.margin > best[1].margin:
                best = (t, worst)
        t += step

    if best is None:
        return None, 0.0

    # timing slack: how far either side the whole wind set still lands red
    t0 = best[0]
    half = 0.0
    for step in (dt, -dt):
        k = step
        while abs(k) < 0.20:
            if all(simulate(t0 + k + off, aim, plat_y, difficulty, bounds,
                            wx, wy, plat_x=plat_x).bullseye
                   for wx, wy in winds for off in offsets):
                k += step
            else:
                break
        half = max(half, abs(k) - abs(step))
    return best[1], half


def plan(t_from, aim, plat_y, difficulty=0, bounds=DEFAULT_BOUNDS,
         dt=0.002, horizon=None):
    """
    Find the click that lands the dart deepest inside the red band.

    Unlike hoops there is no bounce to make the outcome discontinuous, so the
    landing height is smooth in the click time and the safest throw really is
    the one furthest from both edges.  What still has to be respected is the
    boundary nudge: land within 5 px of an edge and the game moves the dart
    randomly, so anything under SAFE_MARGIN is refused rather than risked.

    Returns (Throw, half_window_seconds) or (None, 0.0).
    """
    if horizon is None:
        _amp, rate = aim_params(difficulty)
        horizon = 360.0 / rate            # one full sweep of the aim

    throws = []
    t = t_from
    while t < t_from + horizon:
        throws.append(simulate(t, aim, plat_y, difficulty, bounds))
        t += dt

    safe = [th.margin >= SAFE_MARGIN for th in throws]
    if not any(safe):
        return None, 0.0

    best_i = max(range(len(throws)),
                 key=lambda k: throws[k].margin if safe[k] else -1e9)

    # The timing slack is the CONTIGUOUS run around the chosen click, not every
    # safe instant in the sweep.  The aim is a sine, so it passes through the
    # right angle twice per period; spanning both clusters reported a window of
    # +/-465 ms when the aim only takes ~97 deg/s to leave a 40 px band.
    lo = hi = best_i
    while lo > 0 and safe[lo - 1]:
        lo -= 1
    while hi + 1 < len(throws) and safe[hi + 1]:
        hi += 1
    half = (throws[hi].t0 - throws[lo].t0) / 2.0
    return throws[best_i], half
