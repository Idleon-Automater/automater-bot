#!/usr/bin/env python3
"""
Exact simulation of Idleon's "Swishy Hoops" minigame.

Every constant and every line of logic here is transcribed from the shipped game
source, `distBuild/static/game/N.js`, behaviour `_event_Hoops` (offset ~9301406)
plus the shared helpers `_customBlock_Trigg` (~6369787) and `_event_002seconds`
(~5483208).  Nothing in this module is empirical — if the simulation disagrees
with the game, the transcription is wrong, not the tuning.

Coordinate system is the game canvas: 960 x 540, +y is down.

--------------------------------------------------------------------------------
ENGINE TIMING
--------------------------------------------------------------------------------
    a.STEP_SIZE = 10                      # ms; Engine.postUpdate does
                                          #   while (acc > STEP_SIZE) update(STEP_SIZE)
    => the game logic runs at a FIXED 100 steps/second.

    c.runPeriodically(20, _event_002seconds)
    _event_002seconds: _GenInfo[16][0] += 1.3
    => the global oscillator counter G advances 1.3 every 20 ms (every 2 steps).

    _customBlock_Trigg(mode, t, i) = sin|cos( (G + t/i*360) * i  degrees )
    => with (t=0, i=1.1) the phase is 1.1*G degrees, advancing
       1.1 * 1.3 / 0.020 = 71.5 deg/s  ->  period 360/71.5 = 5.0350 s.

    NOTE for anyone comparing against the old bot: it read the timer as
    `runPeriodically(200, ...)` and used 7.15 deg/s.  That is 10x too slow, and
    is why no amount of offset tuning ever converged.

--------------------------------------------------------------------------------
THROW ANIMATION  (state variable _GenINFO[90])
--------------------------------------------------------------------------------
Clicking sets [90] = 1 (only when [90]==0 and [103]==0 and fails<=2).  Let step
f0 be the first step in which _event_Hoops observes [90]==1.  Then:

    f0        [90] 1 -> 1.01,  player ySpeed = 0
    f1..f8    [90] += .01  (1.02 .. 1.09)      player Y snapped to platform
    f9        [90] -> 1.10, ySpeed = -30, [90] = 1.3    (snap stops: guard is [90]<1.1)
    f10..f48  [90] += .01, ySpeed += 0.6
    f48       [90] reaches 1.69  ->  BALL RELEASED

    Player Y is snapped by the tail of _event_Hoops:
        if ([90] < 1.1) player.setY([98] - 49)
    so the last snap happens on f8, using [98] sampled at f8.

    Actor motion (Engine actor update):
        moveActorBy(10/STEP_SIZE * speed * elapsed * .01)  with elapsed = STEP_SIZE
        => displacement per step = speed * 0.1 px
    Rise between the last snap (end of f8) and the release read on f48:
        sum over m=0..38 of (-30 + 0.6*m) * 0.1  =  -72.54 px

    Release (_GenINFO[91..94]):
        [91] = player.getX() + 17      # player X is re-set every step, incl. f47
        [92] = player.getY() - 97
        [93] = 3.9                     # constant horizontal speed
        [94] = 0.7 * cos(1.1*G deg) - 2.9      # sampled at f48

--------------------------------------------------------------------------------
PLATFORM / PLAYER  (tail of _event_Hoops, runs every step)
--------------------------------------------------------------------------------
    [98] = round(335 + 110 * sin(1.1*G deg))                       # platform Y
    [97] = round(70 + 50 * cos(1.1*G deg))  if score >= 20 else 70 # platform X
    player.setX([97] + 35)
    if [90] < 1.1: player.setY([98] - 49)

--------------------------------------------------------------------------------
HOOP  (runs every step, before the ball physics, skipped while [103] >= 2)
--------------------------------------------------------------------------------
    S = score (_GenINFO[99]);  f = S / (S + 40)
    i_x = 0.5 + 2.1*f     A_x = 40 + 50*f
    i_y = 0.3 + 2.4*f     A_y = 30 + 40*f
    theta = 360 * S / 7                       # constant per-score phase offset

    S >= 30 : [95] = [107] + A_x*sin(i_x*G + theta)
              [96] = [108] + A_y*cos(i_y*G + theta)
    S >= 10 : [95] = [107] + A_x*sin(i_x*G + theta)
              [96] = [108]
    else    : [95] = [107]
              [96] = [108]

    After each shot settles ([103] >= 2.5) the base is re-rolled:
        [107] = randInt(510, 675)
        [108] = randInt(150, 270) if [107] < 600 else randInt(250, 410)

--------------------------------------------------------------------------------
BALL FLIGHT  (the [103]==1 branch, one pass per step)
--------------------------------------------------------------------------------
    [94] += 0.069                                   # gravity, applied FIRST
    check = ([91] + [93] + 21, [92] + [94] + 21)    # +21 = ball sprite half-size
    for i in 0..3:  if dist(check, target_i) < 25 and not (i==2 and [94]<0): hit
        i=0  ([95]+39, [96]+113)   SCORE   (+2 if untouched, else +1)
        i=1  ([95]+78, [96]+ 90)   right rim
        i=2  ([95]+88, [96]+  4)   backboard, only while falling
        i=3  ([95]+ 4, [96]+ 90)   left rim
    if nothing hit:
        if [93]>0 and [91]+[93] > [95]+39 and [91] < [95]+40
                 and [92] > [96]-25 and [92] < [96]+100:   -> bounce off the post
        elif [92] > 600:                                   -> ball lost, fail
        else: [91] += [93] ; [92] += [94]

    Targets are tested in order and the loop breaks on the first hit, so the
    scoring circle wins over the rims whenever both are in range.  The left rim
    (i=3) sits 35 px before and 23 px above the scoring centre, which is why a
    flat trajectory clips the rim even though it "looks" on target -- the
    simulation below reproduces that exactly, so the planner can avoid it.
"""

import math

# ── Engine timing ─────────────────────────────────────────────────────────────
STEP_S          = 0.010                  # a.STEP_SIZE = 10 ms
TICK_S          = 0.020                  # runPeriodically(20, _event_002seconds)
G_PER_TICK      = 1.3
TRIGG_I         = 1.1                    # the (t=0, i=1.1) Trigg calls
PHASE_DEG_S     = TRIGG_I * G_PER_TICK / TICK_S      # 71.5 deg/s
PHASE_PERIOD_S  = 360.0 / PHASE_DEG_S                # 5.03497 s

# ── Throw animation ───────────────────────────────────────────────────────────
SNAP_STEP       = 8      # last step on which player Y is snapped to the platform
RELEASE_STEP    = 48     # step on which the ball is released
JUMP_RISE_PX    = 72.54  # sum((-30 + 0.6m) * 0.1 for m in 0..38)
PLAYER_Y_OFFSET = 49     # player.setY([98] - 49)
BALL_Y_OFFSET   = 97     # [92] = player.getY() - 97
PLAYER_X_OFFSET = 35     # player.setX([97] + 35)
BALL_X_OFFSET   = 17     # [91] = player.getX() + 17

# ── Platform ──────────────────────────────────────────────────────────────────
PLAT_Y_BASE, PLAT_Y_AMP = 335.0, 110.0
PLAT_X_BASE, PLAT_X_AMP = 70.0, 50.0
PLAT_X_MOVES_FROM_SCORE = 20

# ── Ball ──────────────────────────────────────────────────────────────────────
VX              = 3.9
GRAVITY         = 0.069
VY_BASE, VY_AMP = -2.9, 0.7
BALL_HALF       = 21     # ball sprite is drawn origin-centred; code adds +21
HIT_RADIUS      = 25
FLOOR_Y         = 600

# ── Hoop ──────────────────────────────────────────────────────────────────────
HOOP_X_MIN, HOOP_X_MAX = 510, 675
# [108] = randInt(150,270) if [107] < 600 else randInt(250,410)
HOOP_Y_MIN_SPAWN, HOOP_Y_MAX_SPAWN = 150, 410
HOOP_SPRITE_H           = 110    # measured; scoring point sits at [96]+113
HOOP_MOVES_FROM_SCORE   = 10
HOOP_Y_MOVES_FROM_SCORE = 30

# Collision targets, relative to ([95], [96]), in the order the game tests them.
TARGETS = (
    ("score",  39, 113),
    ("rim_r",  78,  90),
    ("board",  88,   4),   # only tested while the ball is falling
    ("rim_l",   4,  90),
)


def hoop_motion(score):
    """Return (i_x, A_x, i_y, A_y, theta_deg) for the hoop's oscillation."""
    f = score / (score + 40.0)
    return (0.5 + 2.1 * f, 40.0 + 50.0 * f,
            0.3 + 2.4 * f, 30.0 + 40.0 * f,
            360.0 * score / 7.0)


class Clock:
    """
    The global oscillator phase, phi(t) = 1.1 * G(t) in degrees.

    G only changes on 20 ms ticks, so phi is a staircase.  `phi0_deg` is the
    phase at wall-clock `t0`; `tick_ref` is the wall-clock time of some tick
    boundary.  If tick_ref is None the staircase is ignored and phi advances
    continuously -- worst case that costs one tick of phase, 1.43 deg, which is
    3.5 px of ball height against a 25 px scoring radius.
    """

    def __init__(self, phi0_deg, t0, tick_ref=None):
        self.phi0_deg = phi0_deg
        self.t0       = t0
        self.tick_ref = tick_ref

    def phi(self, t):
        """Phase in degrees at wall-clock time t."""
        if self.tick_ref is not None:
            n = math.floor((t - self.tick_ref) / TICK_S)
            t = self.tick_ref + n * TICK_S
        return self.phi0_deg + PHASE_DEG_S * (t - self.t0)

    def platform_y(self, t):
        return round(PLAT_Y_BASE + PLAT_Y_AMP * math.sin(math.radians(self.phi(t))))

    def platform_x(self, t, score):
        if score < PLAT_X_MOVES_FROM_SCORE:
            return PLAT_X_BASE
        return round(PLAT_X_BASE + PLAT_X_AMP * math.cos(math.radians(self.phi(t))))


class HoopClock:
    """
    Hoop position over time.  For score < 10 the hoop is static so `base` alone
    is used; above that the oscillation is driven by its own phase, which runs at
    a different rate from the platform's (i_x != i_y != 1.1) and therefore has to
    be observed separately rather than derived from the platform phase.
    """

    def __init__(self, base_x, base_y, score,
                 phase_x_deg=0.0, phase_y_deg=0.0, t0=0.0):
        self.base_x, self.base_y = base_x, base_y
        self.score = score
        self.t0 = t0
        self.i_x, self.A_x, self.i_y, self.A_y, _ = hoop_motion(score)
        # deg/s for each axis: i * (1.3 / 0.020)
        self.rate_x = self.i_x * G_PER_TICK / TICK_S
        self.rate_y = self.i_y * G_PER_TICK / TICK_S
        self.phase_x_deg = phase_x_deg
        self.phase_y_deg = phase_y_deg

    def at(self, t):
        x, y = self.base_x, self.base_y
        if self.score >= HOOP_MOVES_FROM_SCORE:
            a = math.radians(self.phase_x_deg + self.rate_x * (t - self.t0))
            x = self.base_x + self.A_x * math.sin(a)
        if self.score >= HOOP_Y_MOVES_FROM_SCORE:
            a = math.radians(self.phase_y_deg + self.rate_y * (t - self.t0))
            y = self.base_y + self.A_y * math.cos(a)
        return x, y


class Shot:
    """Result of one simulated throw."""

    __slots__ = ("t0", "outcome", "steps", "min_dist", "min_any", "clean",
                 "launch_x", "launch_y", "vy0", "points", "contacts",
                 "robustness")

    def __init__(self, t0, outcome, steps, min_dist, clean,
                 launch_x, launch_y, vy0, contacts=(), min_any=float("inf")):
        self.t0       = t0          # wall-clock time of animation step f0
        self.outcome  = outcome     # 'score' | 'lost'
        self.steps    = steps       # flight steps until the outcome
        self.min_dist = min_dist    # closest approach to the scoring centre, px
        self.min_any  = min_any     # closest approach to ANY collision target
        self.clean    = clean       # untouched -- worth 2 points instead of 1
        self.launch_x = launch_x
        self.launch_y = launch_y
        self.vy0      = vy0
        self.contacts = tuple(contacts)   # rims/board/post hit along the way
        self.points   = (2 if clean else 1) if outcome == "score" else 0
        self.robustness = None      # set by plan_score: fraction of probes made

    @property
    def scored(self):
        return self.outcome == "score"

    @property
    def direct(self):
        """Scored without touching anything -- the most robust kind of make."""
        return self.outcome == "score" and not self.contacts

    def __repr__(self):
        return (f"<Shot {self.outcome} min_dist={self.min_dist:.1f} "
                f"steps={self.steps} pts={self.points} "
                f"contacts={'/'.join(self.contacts) or '-'}>")


def simulate(t0, clock, hoop, score, max_steps=400, launch_bias=0.0):
    """
    Simulate a throw whose animation step f0 lands at wall-clock time `t0`.

    `clock` drives the platform / release velocity, `hoop` drives the target.

    `launch_bias` shifts the release height in game pixels and is the single
    escape hatch for the one constant here that is derived rather than read
    literally: JUMP_RISE_PX, the 72.54 px the player gains between the last
    platform snap and the release.  It comes from integrating the actor's
    velocity through the jump, which depends on reading Stencyl's
    `moveActorBy(10/STEP_SIZE * speed * elapsed * .01)` correctly.  A sweep of
    the planner shows shots survive about +/-10 px of error here before
    accuracy falls off, so if every shot lands consistently high or low this is
    the knob -- positive bias aims lower.  Nothing else should ever need tuning.

    Returns a Shot.
    """
    # Release conditions.  Each sample is taken at the step the game reads it:
    #   platform Y at f8 (last snap), platform X at f47 (set at the end of the
    #   previous step), release velocity at f48.
    t_snap    = t0 + SNAP_STEP * STEP_S
    t_prev    = t0 + (RELEASE_STEP - 1) * STEP_S
    t_release = t0 + RELEASE_STEP * STEP_S

    plat_y = clock.platform_y(t_snap)
    plat_x = clock.platform_x(t_prev, score)

    y = plat_y - PLAYER_Y_OFFSET - JUMP_RISE_PX - BALL_Y_OFFSET + launch_bias
    x = plat_x + PLAYER_X_OFFSET + BALL_X_OFFSET
    launch_x, launch_y = x, y

    vy = VY_AMP * math.cos(math.radians(clock.phi(t_release))) + VY_BASE
    vy0 = vy
    vx = VX

    clean    = True
    min_dist = float("inf")
    min_any  = float("inf")

    contacts = []

    for n in range(1, max_steps + 1):
        t  = t_release + n * STEP_S
        vy += GRAVITY

        cx = x + vx + BALL_HALF
        cy = y + vy + BALL_HALF
        hx, hy = hoop.at(t)

        hit = -1
        for i, (name, dx, dy) in enumerate(TARGETS):
            if name == "board" and vy < 0:
                continue                       # `!(2==i && 0>[94])`
            d = math.hypot(cx - (hx + dx), cy - (hy + dy))
            min_any = min(min_any, d)
            if name == "score":
                min_dist = min(min_dist, d)
            if d < HIT_RADIUS:
                hit = i
                break

        if hit == 0:
            return Shot(t0, "score", n, min_dist, clean,
                        launch_x, launch_y, vy0, contacts, min_any)

        if hit > 0:
            # Rim / backboard bounce.  Transcribed literally, including the
            # argument order of the game's atan2 calls (Haxe's Math.atan2 takes
            # (y, x), and the game passes dx as y and dy as x).
            tx, ty = hx + TARGETS[hit][1], hy + TARGETS[hit][2]
            a4 = 90.0 - math.degrees(math.atan2(tx - cx, ty - cy))
            a5 = math.hypot(vx, vy)
            a6 = 2.0 * a4 + 180.0 - math.degrees(math.atan2(vy, vx))
            if hit == 1 and a6 > 260.0:
                a6 = 260.0
            x  = tx + (25.0 * math.cos(math.radians(a4 + 180.0)) - BALL_HALF)
            y  = ty + (25.0 * math.sin(math.radians(a4 + 180.0)) - BALL_HALF)
            speed = max(2.0, a5)
            vx = 0.58 * math.cos(math.radians(a6)) * speed
            vy = 0.58 * math.sin(math.radians(a6)) * speed
            if a4 > 0.0 or hit == 2:
                clean = False              # `(0<DL2[4]||2==i)&&([105]=0)`
            contacts.append(TARGETS[hit][0])
            continue                       # `_DN=1; break` -- no move this step

        # The vertical post at [95]+40, spanning [96]-25 .. [96]+100.
        if (vx > 0 and x + vx > hx + 39 and x < hx + 40
                and y > hy - 25 and y < hy + 100):
            clean = False
            x  = hx + 40
            y  = y + vy
            vx = -0.84 * vx
            contacts.append("post")
            continue

        if y > FLOOR_Y:
            return Shot(t0, "lost", n, min_dist, clean,
                        launch_x, launch_y, vy0, contacts, min_any)

        x += vx
        y += vy

    return Shot(t0, "lost", max_steps, min_dist, clean,
                launch_x, launch_y, vy0, contacts, min_any)


# ── Planner ───────────────────────────────────────────────────────────────────

def scan(t_from, t_to, clock, hoop, score, dt=0.004, launch_bias=0.0):
    """Simulate a throw for every candidate f0 time in [t_from, t_to)."""
    out = []
    t = t_from
    while t < t_to:
        out.append(simulate(t, clock, hoop, score, launch_bias=launch_bias))
        t += dt
    return out


def best_window(shots, central_frac=0.0):
    """
    Pick the click time at the centre of the widest run of consecutive scoring
    candidates.

    This is the whole point of scanning rather than solving a trajectory
    equation: the exact solution sits somewhere inside a scoring window, but not
    necessarily in its middle.  Aiming at the middle means click latency and
    phase-estimate error have to eat through the full half-width before the shot
    misses, which is what makes a three-miss budget survivable.

    `central_frac` > 0 narrows the choice to the central fraction of the run and
    then takes the pass closest to the net centre, on the theory that trading a
    little timing slack buys tolerance to a wrong release height.  It does not
    work, and the parameter is kept only so the result stays reproducible.
    Measured over 120 random hoops (score 0, phase +/-0.2 deg, hoop +/-2 px):

        central_frac   nominal   bias -10px   bias +10px   latency sigma 80ms
             0.00       100.0%       82.0%        85.6%          82.0%
             0.50        99.1%       93.7%        52.3%          76.6%
             1.00        79.3%       98.2%        14.4%          62.2%

    Minimising centre-distance helps a downward shift and wrecks an upward one,
    because the closest pass is not centred within the reachable arc -- it is a
    trade between the two directions, not extra margin.  The midpoint is the
    only choice that is symmetric in release-height error, and it is also the
    best at nominal, so that is the default.  Release-height error is dealt with
    where it belongs: measured off the ball in flight (see measure_launch_bias).

    Direct makes are preferred over bank shots even when the bank shot has the
    wider window.  A bounce reflects the velocity about the contact normal, so
    it multiplies whatever error is already present -- and it is worth 1 point
    instead of 2, because any contact clears the game's `[105]` clean flag.
    Bank shots are only used when the hoop admits no direct window at all,
    which is about 4% of spawns.

    Returns (best_shot, window_half_width_seconds) or (None, 0.0).
    """
    run = _widest_run(shots, lambda s: s.direct)
    if run is None:
        run = _widest_run(shots, lambda s: s.scored)
    if run is None:
        return None, 0.0

    i, j = run
    half = (shots[j].t0 - shots[i].t0) / 2.0

    margin = int((j - i + 1) * (1.0 - central_frac) / 2.0)
    lo, hi = i + margin, j - margin
    if hi < lo:
        lo = hi = (i + j) // 2
    pick = min(range(lo, hi + 1), key=lambda k: shots[k].min_dist)
    return shots[pick], half


def _widest_run(shots, pred):
    """Longest run of consecutive candidates satisfying pred, as (i, j)."""
    best, best_len = None, 0
    i, n = 0, len(shots)
    while i < n:
        if not pred(shots[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and pred(shots[j + 1]):
            j += 1
        if j - i + 1 > best_len:
            best_len = j - i + 1
            best = (i, j)
        i = j + 1
    return best


MISS_TIMING_PROBES = (-0.10, -0.05, 0.0, 0.05, 0.10)   # seconds
MISS_BIAS_PROBES   = (-20.0, -10.0, 0.0, 10.0, 20.0)   # game px of release height

# Perturbations a real throw has to survive.  Deliberately smaller than the miss
# probes: these are the errors actually left after calibration (click latency
# jitter, a few px of hoop detection noise), not worst-case unknowns.
SHOT_TIMING_PROBES = (-0.045, -0.022, 0.0, 0.022, 0.045)
SHOT_BIAS_PROBES   = (-9.0, -4.5, 0.0, 4.5, 9.0)


def plan_score(t_from, clock, hoop, score, launch_bias=0.0, dt=0.004,
               candidates=28):
    """
    Find a click that scores and keeps scoring when the aim is slightly wrong.

    The widest scoring window is not the same as the safest throw.  A hoop can
    admit a run of click times 240 ms wide in which the ball passes 24.8 px from
    the scoring centre against a 25 px radius -- comfortable in time, one fifth
    of a pixel from failing in space.  That combination is what produced four
    identical real-world misses off a plan that looked healthy in the log, and
    no single scalar separates it from a genuinely safe throw, because with
    bounces the outcome is not smooth in the aim.

    So candidates are scored by how many perturbed re-simulations still go in,
    and only then by how much timing slack they have.  Direct makes are still
    preferred over bank shots: a bounce multiplies error and is worth 1 instead
    of 2.

    Returns (shot, half_window_seconds) or (None, 0.0).
    """
    shots = scan(t_from, t_from + PHASE_PERIOD_S, clock, hoop, score,
                 dt=dt, launch_bias=launch_bias)

    for predicate in (lambda s: s.direct, lambda s: s.scored):
        runs = _all_runs(shots, predicate)
        if not runs:
            continue

        picks = []
        for i, j in runs:
            half = (shots[j].t0 - shots[i].t0) / 2.0
            # Sample across the run rather than only its midpoint.
            n = min(7, j - i + 1)
            for k in (i + round((j - i) * q / max(n - 1, 1)) for q in range(n)):
                edge = min(shots[k].t0 - shots[i].t0, shots[j].t0 - shots[k].t0)
                picks.append((shots[k], half, edge))

        picks.sort(key=lambda p: p[2], reverse=True)
        best = None
        for cand, half, edge in picks[:candidates]:
            survived = 0
            for d_t in SHOT_TIMING_PROBES:
                for d_b in SHOT_BIAS_PROBES:
                    if simulate(cand.t0 + d_t, clock, hoop, score,
                                launch_bias=launch_bias + d_b).scored:
                        survived += 1
            key = (survived, edge)
            if best is None or key > best[0]:
                best = (key, cand, half)
            if survived == len(SHOT_TIMING_PROBES) * len(SHOT_BIAS_PROBES):
                break        # cannot do better; stop paying for more probes

        if best is not None:
            (survived, _edge), cand, half = best
            total = len(SHOT_TIMING_PROBES) * len(SHOT_BIAS_PROBES)
            cand.robustness = survived / total
            return cand, half

    return None, 0.0


def _all_runs(shots, pred):
    """Every maximal run of consecutive candidates satisfying pred."""
    runs, i, n = [], 0, len(shots)
    while i < n:
        if not pred(shots[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and pred(shots[j + 1]):
            j += 1
        runs.append((i, j))
        i = j + 1
    return runs


def plan_miss(t_from, clock, hoop, score, launch_bias=0.0, dt=0.004,
              candidates=40):
    """
    Find a click that reliably does *not* score -- used to stop at a chosen
    score without the run drifting past it.

    Choosing a candidate that merely misses is not enough, and neither is
    choosing the one that passes furthest from the net.  Both were tried:
    the midpoint of the widest 'lost' run leaked ~3% of the time under
    +/-100 ms / +/-20 px perturbation, and maximising distance from the
    scoring centre leaked ~6% -- worse, because a shot aimed well clear of the
    net can still be nudged into clipping a rim, and a rim bounce drops in.
    With bounces in play the outcome is not a smooth function of the aim, so
    no single scalar proxy is trustworthy.

    So this verifies instead of proxying: rank candidates by how far they stay
    from every part of the hoop, then re-simulate the best ones across a grid of
    timing and release-height errors and accept only one that misses in every
    case.  Misses are plentiful -- typically half of all click times lose the
    ball -- so insisting on a provably safe one costs nothing.

    Returns (shot, half_window_seconds) or (None, 0.0) if none is provably safe.
    """
    shots = scan(t_from, t_from + PHASE_PERIOD_S, clock, hoop, score,
                 dt=dt, launch_bias=launch_bias)
    lost = [s for s in shots if s.outcome == "lost"]
    if not lost:
        return None, 0.0

    lost.sort(key=lambda s: s.min_any, reverse=True)

    for cand in lost[:candidates]:
        safe = True
        for d_t in MISS_TIMING_PROBES:
            for d_b in MISS_BIAS_PROBES:
                probe = simulate(cand.t0 + d_t, clock, hoop, score,
                                 launch_bias=launch_bias + d_b)
                if probe.scored:
                    safe = False
                    break
            if not safe:
                break
        if safe:
            # Report the verified envelope, not a nominal one.
            return cand, max(MISS_TIMING_PROBES)

    return None, 0.0


def miss_window(shots):
    """Deprecated: kept so old call sites fail loudly rather than silently."""
    raise NotImplementedError(
        "use plan_miss(), which verifies the miss by re-simulation; "
        "picking from a precomputed scan cannot guarantee a miss")
