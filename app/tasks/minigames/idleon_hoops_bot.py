#!/usr/bin/env python3
"""
Idleon - Swishy Hoops bot.

    pip install -r requirements.txt

    python idleon_hoops_bot.py --calibrate   # check window + detection, no clicks
    python idleon_hoops_bot.py --dry         # plan real shots, print them, never click
    python idleon_hoops_bot.py               # play

How this differs from the previous version
------------------------------------------
The old bot solved a closed-form trajectory equation and then hill-climbed a
`SCORE_OFFSET_Y` constant across hundreds of shots to cover the difference
between its model and the game.  It never converged, because three of its inputs
were wrong rather than merely imprecise:

  * the oscillator was read as 7.15 deg/s; it is 71.5 deg/s (the game's timer is
    `runPeriodically(20, ...)`, not 200 ms) -- a factor of ten,
  * the release delay was taken as 50 frames at 60 fps = 833 ms; the engine runs
    a fixed 10 ms step, so it is 48 steps = 480 ms,
  * the ball's launch height ignored the 72.5 px the player rises during the
    jump animation.

No amount of offset tuning can absorb a 10x error in phase rate, so the learned
corrections were fitting noise.  This version drops the tuner entirely: it
simulates the game's own loop step for step (see hoopsim.py, which cites N.js
line by line) and searches for a click time.  There is nothing left to learn, so
there is nothing to spend failed shots on.

The one thing it still needs from you is a one-off visual check that the
detected hoop maps onto the game's internal hoop coordinate -- run --calibrate,
nudge the crosshair onto the middle of the net, press S.  That costs zero shots.
"""

import argparse
import json
import math
import os
import sys
import time

import cv2
import mss
import numpy as np

import gamewindow
import hoopsim as H
from clicker import Clicker, sleep_until

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "hoops_config.json")

DEFAULT_CONFIG = {
    # Detected-hoop-bbox -> game [95]/[96] correction, in game pixels.  Set by
    # --calibrate.  0,0 means "the orange blob's top-left corner is exactly the
    # hoop image's origin", which is the right answer if the sprite has no
    # transparent margin.
    "hoop_dx": 0.0,
    "hoop_dy": 0.0,
    # Wall-clock delay from issuing the click to the game's first animation
    # step, in seconds.  Covers OS input queue + Chromium event dispatch + the
    # engine's frame accumulator.  Scoring windows are typically ~300 ms wide,
    # so this only has to be right to a few tens of ms.
    "click_latency_s": 0.030,
    # Release-height correction in game pixels; see hoopsim.simulate.  Positive
    # aims lower.  Leave at 0 unless every shot lands consistently high or low,
    # in which case one or two shots are enough to set it.
    "launch_y_bias": 0.0,
    # HSV bands.  --calibrate lets you click a pixel to read its HSV.
    "hoop_hsv_lo": [5, 100, 120],
    "hoop_hsv_hi": [22, 255, 255],
    "plat_hsv_lo": [12, 50, 70],
    "plat_hsv_hi": [35, 220, 210],
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[Config] {CONFIG_PATH} unreadable ({e}); using defaults")
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"[Config] saved -> {CONFIG_PATH}")


# ── Capture ───────────────────────────────────────────────────────────────────

class Camera:
    """Screen capture that returns BGR frames plus the time they were taken."""

    def __init__(self, sct, rect):
        self.sct   = sct
        self.rect  = rect
        self.scale = rect["scale"]
        self.mon   = {k: rect[k] for k in ("left", "top", "width", "height")}

    def grab(self):
        t   = time.perf_counter()
        img = np.asarray(self.sct.grab(self.mon))[:, :, :3]
        return img, t

    def to_game(self, px):
        return px / self.scale

    def to_screen(self, gx):
        return int(round(gx * self.scale))


# ── Detection ─────────────────────────────────────────────────────────────────
#
# Both detectors return a feature position in game coordinates.  For the
# platform we deliberately do NOT care what the absolute value means: the phase
# fit only uses how the value moves, so any constant sprite margin falls out
# into the fitted offset.  For the hoop the absolute value does matter, which is
# what hoop_dx / hoop_dy in the config are for.

def detect_platform(frame, cam, cfg):
    """
    Platform feature Y in game coords, or None.

    Uses the *bottom* edge of the platform blob.  The player stands on top of
    the platform and its sprite merges into the same colour band, so the top
    edge moves when the player animates; the underside does not.
    """
    lo = np.array(cfg["plat_hsv_lo"]); hi = np.array(cfg["plat_hsv_hi"])
    roi = frame[:, : frame.shape[1] // 3]
    mask = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), lo, hi)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 150:
        return None
    _, by, _, bh = cv2.boundingRect(c)
    return cam.to_game(by + bh)


def detect_hoop(frame, cam, cfg, debug=None):
    """
    Hoop origin ([95], [96]) in game coords, or None.

    The hoop sprite is the orange backboard-plus-rim assembly living in
    x in [510, 675+92].  Closing the mask merges the board and the rim into one
    blob whose bounding box top-left is the sprite origin.  Verified against a
    real frame: bbox (648, 300) 89x110, against which the source's three
    collision anchors land at [95]+4..[95]+88 horizontally, [96]+4 for the
    backboard top and [96]+90 for the rim -- all within 2 px, and [96]+113 puts
    the scoring centre in the middle of the net.  Hence hoop_dx/dy default to 0.

    Candidates are ranked by *bounding box* area, not contour area.  The hoop is
    two thin bars: 89x110 of extent but only ~1100 px of actual contour.  The
    ball is a filled 42 px disc, ~1400 px of contour -- so ranking by contour
    area picks the ball over the hoop whenever a shot is in flight.
    """
    lo = np.array(cfg["hoop_hsv_lo"]); hi = np.array(cfg["hoop_hsv_hi"])

    # The ROI has to cover the whole sprite at its lowest possible spawn.  [96]
    # runs to 410 and the sprite is ~110 px tall, so it reaches y=520 -- cutting
    # the ROI at 440 (as this did) left under 55 px of a low hoop visible, the
    # extent filter below rejected it, and detection returned nothing at all for
    # about 15% of right-side rolls.  Horizontally the ROI still stops short of
    # the EXIT button at x>=892, so that cannot be picked up.
    x0 = max(0, cam.to_screen(H.HOOP_X_MIN) - 20)
    x1 = min(frame.shape[1], cam.to_screen(H.HOOP_X_MAX + 100))
    y1 = min(frame.shape[0], cam.to_screen(H.HOOP_Y_MAX_SPAWN + 130))
    roi = frame[:y1, x0:x1]

    mask = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), lo, hi)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        if cv2.contourArea(c) < 200:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        # The sprite is ~89x110 game px; the ball is 42x42.  Requiring real
        # extent in both axes rejects the ball and any stray UI fleck.
        if bw < cam.to_screen(55) or bh < cam.to_screen(55):
            continue
        boxes.append((bw * bh, (bx, by, bw, bh)))
    if not boxes:
        return None

    _, (bx, by, bw, bh) = max(boxes)
    bx += x0

    if debug is not None:
        cv2.rectangle(debug, (bx, by), (bx + bw, by + bh), (0, 255, 0), 2)

    return (cam.to_game(bx) + cfg["hoop_dx"],
            cam.to_game(by) + cfg["hoop_dy"])


def detect_ball(frame, cam, cfg, near_game, radius_game=140):
    """
    Ball centre in game coords, or None.

    The ball shares the hoop's orange, so it is separated by shape and size: it
    is a small filled disc (the sprite is 42x42, drawn origin-centred, which is
    where the +21 in the game's collision test comes from), while the board and
    rim are elongated bars.
    """
    lo = np.array(cfg["hoop_hsv_lo"]); hi = np.array(cfg["hoop_hsv_hi"])
    r  = cam.to_screen(radius_game)
    cx, cy = cam.to_screen(near_game[0]), cam.to_screen(near_game[1])
    x0, x1 = max(0, cx - r), min(frame.shape[1], cx + r)
    y0, y1 = max(0, cy - r), min(frame.shape[0], cy + r)
    if x1 <= x0 or y1 <= y0:
        return None

    roi  = frame[y0:y1, x0:x1]
    mask = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), lo, hi)
    # The ball sprite is an orange disc crossed by dark seam lines, and it spins
    # in flight.  Without closing the mask those seams cut it into several small
    # crescents, none of which passes an area or roundness test -- which is why
    # the first live run never once reported a ball position.
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best, best_score = None, 0.0
    for c in cnts:
        a = cv2.contourArea(c)
        if not (40 <= a <= 4000):
            continue
        p = cv2.arcLength(c, True)
        if p <= 0:
            continue
        circ = 4 * math.pi * a / (p * p)
        if circ < 0.45:                 # bars score far below this
            continue
        if circ * a > best_score:
            best, best_score = c, circ * a

    if best is None:
        return None
    bx, by, bw, bh = cv2.boundingRect(best)
    return (cam.to_game(x0 + bx + bw / 2.0), cam.to_game(y0 + by + bh / 2.0))


def measure_launch_bias(cam, cfg, shot, samples=6):
    """
    Watch the ball in flight and report how far its real height differs from the
    simulation's.

    A constant release-height error shows up as a constant offset; a phase error
    shows up as an offset growing linearly with flight time, because it lands in
    the release velocity instead.  Sampling several points and fitting
    `dy(n) = bias + n * dvy` separates the two, so a bad reading here cannot be
    mistaken for a bad clock.

    Returns (bias_px, dvy_per_step, n_samples) or None.
    """
    t_release = shot.t0 + H.RELEASE_STEP * H.STEP_S
    # Sample across the first half of the flight, before the ball reaches the
    # hoop and the rim can occlude it.
    ns = np.linspace(shot.steps * 0.15, shot.steps * 0.6, samples)

    obs = []
    for n in ns:
        t_target = t_release + n * H.STEP_S
        if t_target - time.perf_counter() < 0:
            continue
        sleep_until(t_target)
        frame, t_actual = cam.grab()
        n_actual = (t_actual - t_release) / H.STEP_S

        px = shot.launch_x + H.VX * n_actual + H.BALL_HALF
        py = (shot.launch_y + n_actual * shot.vy0
              + H.GRAVITY * n_actual * (n_actual + 1) / 2 + H.BALL_HALF)
        found = detect_ball(frame, cam, cfg, (px, py))
        if found is not None:
            obs.append((n_actual, found[1] - py, found[0] - px))

    if len(obs) < 3:
        return None

    n  = np.array([o[0] for o in obs])
    dy = np.array([o[1] for o in obs])
    dx = np.array([o[2] for o in obs])

    # Horizontal position is a pure clock: Vx is a constant 3.9 px/step and
    # nothing perturbs it before the hoop, so a horizontal discrepancy can only
    # mean the ball was released at a different time than assumed.  That makes
    # dx a direct readout of the real click-to-throw latency, which is otherwise
    # a guess:
    #     dx = Vx * (L_assumed - L_true) / STEP  ->  L_true = L_assumed - dx*STEP/Vx
    dt_s = -float(np.mean(dx)) * H.STEP_S / H.VX

    # Take the timing error out of the vertical residual before reading the
    # release height off it, or a late click looks like a low throw.
    n_true = n + dt_s / H.STEP_S
    A = np.stack([np.ones_like(n_true), n_true], 1)
    (bias, dvy), *_ = np.linalg.lstsq(A, dy, rcond=None)
    return float(bias), float(dvy), dt_s, len(obs)


# ── Phase estimation ──────────────────────────────────────────────────────────
#
# [98] = round(335 + 110 * sin(phi))  with phi advancing at a known 71.5 deg/s.
# Because the rate is known, fitting the phase is a *linear* least squares:
#
#     y(t) = C + A*sin(w t) + B*cos(w t)          (w known)
#
# solved in one shot over a couple of seconds of samples.  That is far steadier
# than the old bot's per-frame `asin((y-335)/110)`, whose error blows up near
# the turning points where cos(phi) -> 0 and a single pixel of detection noise
# maps to tens of degrees of phase.
#
# The fitted amplitude is a free correctness check: it must land on 110 * scale
# in screen pixels, i.e. 110 in game pixels.  If it does not, the canvas
# rectangle is wrong and nothing downstream can work.

def fit_phase(times, values, rate_deg_s, reject_sigma=2.5, passes=2):
    """
    Fit y = C + amp * sin(rate*t + psi).
    Returns (C, amp, psi_deg_at_t_ref, rms_residual, t_ref).

    Refits after discarding samples more than `reject_sigma` residuals out.  A
    plain least-squares fit is already accurate to a few hundredths of a degree
    in the median, but its tail is bad: one frame where the detector latches
    onto the wrong blob drags the phase by degrees, and a degree of phase is
    2 px of ball height.  Two passes cut the 95th-percentile error by roughly
    half for a few hundred microseconds of extra work.
    """
    t = np.asarray(times, dtype=float)
    y = np.asarray(values, dtype=float)
    t_ref = t[0]
    w = math.radians(rate_deg_s)
    dt = t - t_ref
    M_all = np.stack([np.ones_like(dt), np.sin(w * dt), np.cos(w * dt)], 1)

    keep = np.ones(len(t), dtype=bool)
    coef = None
    for _ in range(passes):
        coef, *_ = np.linalg.lstsq(M_all[keep], y[keep], rcond=None)
        resid = y - M_all @ coef
        s = float(np.sqrt(np.mean(resid[keep] ** 2)))
        if s <= 1e-9:
            break
        new_keep = np.abs(resid) <= reject_sigma * s
        if new_keep.sum() < max(12, 0.6 * len(t)):
            break                      # refuse to throw away most of the data
        if (new_keep == keep).all():
            break
        keep = new_keep

    C, A, B = coef
    rms = float(np.sqrt(np.mean((y[keep] - M_all[keep] @ coef) ** 2)))
    return C, math.hypot(A, B), math.degrees(math.atan2(B, A)), rms, t_ref


class PlatformTracker:
    """
    Rolling window of platform observations, re-fitted on demand.

    Keeping a few seconds of history rather than re-sampling from scratch does
    two things: the fit gets a long baseline (better conditioned, less sensitive
    to any single bad frame), and it stays current, so slow drift between
    perf_counter() and the game's own timer cannot accumulate between shots.
    """

    WINDOW_S = 4.0

    def __init__(self, cam, cfg):
        self.cam, self.cfg = cam, cfg
        self.ts, self.ys = [], []

    def pump(self, seconds):
        """Capture for `seconds`, adding whatever the detector finds."""
        t_end = time.perf_counter() + seconds
        while time.perf_counter() < t_end:
            frame, t = self.cam.grab()
            y = detect_platform(frame, self.cam, self.cfg)
            if y is not None:
                self.ts.append(t); self.ys.append(y)
        self._trim()
        return len(self.ts)

    def _trim(self):
        cutoff = time.perf_counter() - self.WINDOW_S
        keep = next((i for i, t in enumerate(self.ts) if t >= cutoff), len(self.ts))
        if keep:
            del self.ts[:keep]; del self.ys[:keep]

    @staticmethod
    def fit_is_trustworthy(amp, rms):
        """
        The fitted amplitude is an end-to-end correctness check: it can only
        come out near 110 game px if the canvas rectangle, the scale and the
        platform mask are all right.  With only three misses available before a
        cooldown, a failing check must stop the bot rather than warn it.
        """
        return abs(amp - H.PLAT_Y_AMP) / H.PLAT_Y_AMP < 0.08 and rms < 6.0

    def fit(self, verbose=True):
        """Return (Clock, amplitude_px, rms_px)."""
        if len(self.ts) < 20 or (self.ts[-1] - self.ts[0]) < 0.8:
            raise RuntimeError(
                f"Only {len(self.ts)} platform samples over "
                f"{(self.ts[-1] - self.ts[0]) if self.ts else 0:.1f}s - the "
                f"platform detector is not finding the ledge.  Run --calibrate "
                f"and check the PLAT mask, or click the ledge to re-read its HSV.")

        C, amp, psi, rms, t_ref = fit_phase(self.ts, self.ys, H.PHASE_DEG_S)
        if verbose:
            err  = abs(amp - H.PLAT_Y_AMP) / H.PLAT_Y_AMP * 100
            good = self.fit_is_trustworthy(amp, rms)
            print(f"[Phase] {len(self.ts)} samples over "
                  f"{self.ts[-1] - self.ts[0]:.1f}s  amp={amp:.1f}px "
                  f"(expect 110, {err:.0f}% off)  rms={rms:.2f}px  "
                  f"[{'ok' if good else 'SUSPECT'}]")
        return H.Clock(psi, t_ref), amp, rms


def recover_score(ts, xs, ys, s_min=10, s_max=60):
    """
    Read the score off the hoop's own swing.

    The game derives both the amplitude and the rate of the hoop's oscillation
    from the score:

        i_x = 0.5 + 2.1*S/(S+40)     A_x = 40 + 50*S/(S+40)

    Both are strictly increasing in S, so fitting the observed motion at each
    candidate rate and keeping the one that explains it best inverts them.

    This exists because counting the score was the last inference left in the
    loop, and it was wrong: a bank shot scores 1 where a swish scores 2, so any
    tally drifts the moment a shot clips the rim.  That drift is not cosmetic --
    S feeds i_x, so a wrong score means the hoop is both *fitted* and
    *extrapolated* at the wrong angular rate.  Measured at a real score of 26:
    exact 100%, off by two 95.6%, off by four 76.7%.

    Recovery is exact from score 12 to about 30 and within +/-1 to about 41,
    which costs roughly a percent.  Returns (score, cost) or (None, None).
    """
    best = None
    for S in range(s_min, s_max + 1):
        i_x, A_x, i_y, A_y, _ = H.hoop_motion(S)
        _c, ax, _p, rx, _t = fit_phase(ts, xs, i_x * H.G_PER_TICK / H.TICK_S)
        cost = rx + abs(ax - A_x) * 0.6
        if S >= H.HOOP_Y_MOVES_FROM_SCORE:
            _c2, ay, _p2, ry, _t2 = fit_phase(ts, ys, i_y * H.G_PER_TICK / H.TICK_S)
            cost += ry + abs(ay - A_y) * 0.6
        if best is None or cost < best[1]:
            best = (S, cost)
    return best if best else (None, None)


def sample_hoop(cam, cfg, score, seconds=None):
    """
    Observe the hoop.  Static below score 10 (just average), otherwise recover
    the score from the motion and fit each axis at its own rate -- the hoop's
    oscillation runs at i_x/i_y times the counter rate, deliberately *not* the
    platform's 1.1x, so its phase has to be measured rather than derived.

    Returns (HoopClock, trustworthy, measured_score_or_None).  As with the
    platform, the fitted amplitude is a check rather than an output: it is known
    in advance from the score, so a fit that does not reproduce it means the
    hoop is not being tracked and the planner would aim at a position the hoop
    never occupies.
    """
    moving = score >= H.HOOP_MOVES_FROM_SCORE
    if seconds is None:
        # A moving hoop needs a long enough arc to condition the fit; a static
        # one only needs enough samples to median away detector noise.
        seconds = 1.8 if moving else 1.0

    ts, xs, ys = [], [], []
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        frame, t = cam.grab()
        h = detect_hoop(frame, cam, cfg)
        if h is not None:
            ts.append(t); xs.append(h[0]); ys.append(h[1])

    if len(ts) < 10:
        print(f"[Hoop ] only {len(ts)} samples - detection is failing")
        return None, False, None

    # Whether the hoop is swinging is an observation, not something to take on
    # trust from a counter that can drift.
    spread = float(np.percentile(xs, 95) - np.percentile(xs, 5))
    moving = spread >= 8.0

    if not moving:
        # Below score 10 the hoop is fixed -- and below 10 the score does not
        # enter the physics at all, so there is nothing to recover and nothing
        # that a wrong count could break.
        return (H.HoopClock(float(np.median(xs)), float(np.median(ys)),
                            min(score, H.HOOP_MOVES_FROM_SCORE - 1)),
                True, None)

    measured, cost = recover_score(ts, xs, ys)
    if measured is not None and measured != score:
        print(f"[Score] hoop motion says score is {measured} "
              f"(bot was counting {score}) - using the measurement")
        score = measured

    i_x, A_x, i_y, A_y, _ = H.hoop_motion(score)
    rate_x = i_x * H.G_PER_TICK / H.TICK_S
    rate_y = i_y * H.G_PER_TICK / H.TICK_S

    Cx, ax, px, rx, t_ref = fit_phase(ts, xs, rate_x)
    ok = abs(ax - A_x) / A_x < 0.20 and rx < 8.0

    if score >= H.HOOP_Y_MOVES_FROM_SCORE:
        Cy, ay, py, ry, _ = fit_phase(ts, ys, rate_y)
        # our fit is in sin form; the game drives Y with cos -> shift by -90 deg
        py -= 90.0
        ok = ok and abs(ay - A_y) / A_y < 0.20 and ry < 8.0
    else:
        Cy, ay, py, ry = float(np.median(ys)), 0.0, 0.0, 0.0

    print(f"[Hoop ] score={score}  base=({Cx:.0f},{Cy:.0f})  "
          f"ampX={ax:.0f}(expect {A_x:.0f}) ampY={ay:.0f}(expect {A_y:.0f})  "
          f"rms={rx:.1f}/{ry:.1f}px  [{'ok' if ok else 'SUSPECT'}]")
    return H.HoopClock(Cx, Cy, score, px, py, t_ref), ok, score


# ── Planning ──────────────────────────────────────────────────────────────────

def plan_shot(clock, hoop, score, t_from, launch_bias=0.0, dt=0.004, miss=False):
    """
    Search a full oscillator period for a click time.  With `miss` set, look for
    one that reliably does *not* score instead.
    Returns (shot, half_window_seconds) or (None, 0).
    """
    if miss:
        return H.plan_miss(t_from, clock, hoop, score, launch_bias=launch_bias, dt=dt)
    return H.plan_score(t_from, clock, hoop, score, launch_bias=launch_bias, dt=dt)


MISS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "misses")


def _save_miss_evidence(pending, cam, cfg):
    """
    Write the frame captured at predicted impact, annotated with where the bot
    aimed and where the ball actually was.

    Without this a miss produces no evidence at all: the log only ever contains
    what the simulation expected, so a systematic aiming error looks exactly
    like a run of bad luck.
    """
    frame = pending.get("frame")
    if frame is None:
        return
    try:
        os.makedirs(MISS_DIR, exist_ok=True)
        shot = pending["shot"]
        tx, ty = pending["target"]
        dbg = frame.copy()

        cv2.circle(dbg, (cam.to_screen(tx), cam.to_screen(ty)),
                   cam.to_screen(H.HIT_RADIUS), (0, 0, 255), 2)
        cv2.drawMarker(dbg, (cam.to_screen(tx), cam.to_screen(ty)),
                       (0, 0, 255), cv2.MARKER_CROSS, 20, 2)

        ball = detect_ball(frame, cam, cfg, (tx, ty), radius_game=220)
        if ball is not None:
            cv2.circle(dbg, (cam.to_screen(ball[0]), cam.to_screen(ball[1])),
                       cam.to_screen(21), (0, 255, 255), 2)
            dy = ball[1] - ty
            dx = ball[0] - tx
            label = f"ball off target dx={dx:+.0f} dy={dy:+.0f}px"
        else:
            label = "ball not found in frame"

        cv2.putText(dbg, label, (10, 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 255, 255), 1)
        cv2.putText(dbg, f"aimed at ({tx:.0f},{ty:.0f})  flight={shot.steps} "
                         f"steps  predicted {shot.outcome}",
                    (10, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)

        path = os.path.join(MISS_DIR, f"miss_{int(time.time())}_"
                                      f"shot{pending['n']}.png")
        cv2.imwrite(path, dbg)
        print(f"        wrote {os.path.relpath(path)}  ({label})")
    except Exception as e:
        print(f"        (could not save miss evidence: {e})")


def score_digits(frame, cam):
    """
    The 'Score: N' text box alone (white pixel-font digits on a dark sky).

    Used for change detection, which is all that is actually needed to grade a
    throw: the number going up means the ball went in.  Deliberately NOT used to
    read the value -- see read_score_tally.
    """
    y0, y1 = cam.to_screen(25), cam.to_screen(41)
    x0, x1 = 0, cam.to_screen(80)
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    # Threshold to the bright text so a scrolling starfield cannot register.
    grey = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return (grey > 170).astype(np.uint8)


def score_text_changed(before, after, min_px=6):
    """
    True if the score readout differs.  Compares thresholded text pixels and
    counts them, rather than averaging over a patch: a single changed digit is
    only ~140 px out of several thousand, which moved an earlier mean-based
    check by about 4 against a threshold of 6, so every basket past the sixth
    silently read as 'no change'.
    """
    if before is None or after is None or before.shape != after.shape:
        return None
    return int(np.count_nonzero(before != after)) >= min_px


def read_score_tally(frame, cam):
    """
    Count the basketball tally under 'Score: N'.

    UNRELIABLE ABOVE ~9 -- do not use this as the score.  It matches the score
    exactly at 0, 4 and 9, which is what it was originally validated on, but the
    row does not simply keep growing: at a real score of 13 it shows 4 icons.
    Whether it wraps or tracks reward progress is not established, so this is
    kept only as a weak cross-check at low scores.

    Returns an int, or None if the row cannot be read.

    Counting these is the third thing tried for this, and the first that is a
    measurement rather than an inference.  The simulation's own prediction was
    fiction (five reported makes in a run that made one).  The hoop re-rolling
    after a basket was falsified by a shot that scored with the hoop stationary.
    A pixel-diff of the score readout worked in testing only because the cases
    tested -- 0 vs 4 -- also changed the tally length; from 6 points on, the new
    ball lands outside the patch and a single changed digit moves the mean by
    about 4, under the threshold, so every later basket read as "no change".
    Counting discrete objects has no threshold to get wrong.
    """
    lo = np.array(cfg_hoop_lo); hi = np.array(cfg_hoop_hi)
    y0, y1 = cam.to_screen(34), cam.to_screen(62)
    roi = frame[y0:y1, 0:min(frame.shape[1], cam.to_screen(950))]
    if roi.size == 0:
        return None

    mask = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), lo, hi)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    n, _lbl, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)

    min_a = int(120 * cam.scale * cam.scale)
    balls = 0
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        if a < min_a or w < cam.to_screen(9) or h < cam.to_screen(9):
            continue
        # Adjacent balls occasionally touch and merge; a blob that is n times
        # as wide as it is tall is that many balls.
        balls += max(1, int(round(w / max(h, 1))))
    return balls


# Filled in from the config at startup so read_score stays a plain function.
cfg_hoop_lo = DEFAULT_CONFIG["hoop_hsv_lo"]
cfg_hoop_hi = DEFAULT_CONFIG["hoop_hsv_hi"]

# Bright red, for the 'Lives Left' hearts.  Red straddles the hue wrap-around,
# so it takes two bands.
HEART_LO_A, HEART_HI_A = np.array([  0, 140, 110]), np.array([  6, 255, 255])
HEART_LO_B, HEART_HI_B = np.array([172, 140, 110]), np.array([179, 255, 255])


def read_lives(frame, cam):
    """
    Lives remaining, counted off the hearts under 'Lives Left' (top right).

    Same reasoning as read_score: the game already displays this, so there is no
    reason to model when a miss does or does not cost a life.  The rules are
    genuinely awkward -- misses are free before the first basket, and free again
    below 5 points while a shop upgrade is unbought -- and getting them wrong in
    either direction is expensive.  Reading the hearts sidesteps all of it.
    """
    y0, y1 = cam.to_screen(44), cam.to_screen(68)
    x0, x1 = cam.to_screen(830), min(frame.shape[1], cam.to_screen(945))
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(cv2.inRange(hsv, HEART_LO_A, HEART_HI_A),
                          cv2.inRange(hsv, HEART_LO_B, HEART_HI_B))
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    n, _l, stats, _c = cv2.connectedComponentsWithStats(mask, 8)

    min_a = int(40 * cam.scale * cam.scale)
    hearts = 0
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < min_a:
            continue
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        if w < cam.to_screen(7) or h < cam.to_screen(7):
            continue
        hearts += max(1, int(round(w / max(h, 1))))
    return hearts




def describe(shot, half, miss=False):
    if shot is None:
        return ("no clean-miss window in this cycle" if miss
                else "no scoring window in this cycle")
    if miss:
        head = (f"verified miss, clears the hoop by {shot.min_any:.0f}px")
    elif shot.direct:
        head = "swish +2"
    else:
        head = f"bank shot off {'/'.join(shot.contacts)} +{shot.points}"
    rob = ("" if shot.robustness is None
           else f"  robust={shot.robustness * 100:.0f}%")
    return (f"{head}  flight={shot.steps} steps ({shot.steps * H.STEP_S:.2f}s)  "
            f"tolerance=+/-{half * 1000:.0f}ms{rob}")


# ── Calibration UI ────────────────────────────────────────────────────────────

def run_calibrate(cam, cfg):
    """
    Live overlay.  Verifies, in order: the canvas rectangle, the two masks, the
    hoop origin, and the phase fit.  Nothing here clicks in the game, so it
    costs no shots.
    """
    print(__doc__.split("How this differs")[0])
    print("[Calibrate]  arrows = nudge hoop origin   S = save   C = re-fit phase")
    print("             click a pixel to print its HSV   Q = quit\n")

    WIN = "Hoops calibration"
    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    state = {"frame": None}

    def on_mouse(ev, x, y, *_):
        if ev != cv2.EVENT_LBUTTONDOWN or state["frame"] is None:
            return
        f = state["frame"]
        if y >= f.shape[0] or x >= f.shape[1]:
            return
        h, s, v = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)[y, x]
        print(f"  HSV at ({x},{y}) = ({h}, {s}, {v})   "
              f"game=({cam.to_game(x):.0f}, {cam.to_game(y):.0f})")

    cv2.setMouseCallback(WIN, on_mouse)

    clock = None
    while True:
        frame, _ = cam.grab()
        state["frame"] = frame
        dbg = frame.copy()

        hoop = detect_hoop(frame, cam, cfg, debug=dbg)
        if hoop:
            hx, hy = hoop
            # The scoring target the game actually tests, [95]+39 / [96]+113.
            sx = cam.to_screen(hx + 39)
            sy = cam.to_screen(hy + 113)
            r  = cam.to_screen(H.HIT_RADIUS)
            cv2.circle(dbg, (sx, sy), r, (0, 0, 255), 2)
            cv2.drawMarker(dbg, (sx, sy), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
            for name, dx, dy in H.TARGETS[1:]:
                cv2.circle(dbg, (cam.to_screen(hx + dx), cam.to_screen(hy + dy)),
                           r, (255, 160, 0), 1)
            cv2.putText(dbg, f"hoop=({hx:.0f},{hy:.0f}) dx={cfg['hoop_dx']:+.0f} "
                             f"dy={cfg['hoop_dy']:+.0f}",
                        (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        else:
            cv2.putText(dbg, "HOOP NOT FOUND", (10, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        py = detect_platform(frame, cam, cfg)
        if py is not None:
            s = cam.to_screen(py)
            cv2.line(dbg, (0, s), (frame.shape[1] // 3, s), (0, 255, 255), 2)
            cv2.putText(dbg, f"platform y={py:.0f}", (10, max(38, s - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        else:
            cv2.putText(dbg, "PLATFORM NOT FOUND", (10, 44),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)

        cv2.putText(dbg,
                    "red circle must sit on the middle of the net; "
                    "orange circles on rims + backboard corner",
                    (10, dbg.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX,
                    0.4, (220, 220, 220), 1)

        cv2.imshow(WIN, dbg)
        k = cv2.waitKey(30) & 0xFF
        if   k == ord('q'): break
        elif k == ord('s'): save_config(cfg)
        elif k == ord('c'):
            try:
                clock, amp, rms = sample_platform(cam, cfg)
                print(f"        phase now = {clock.phi(time.perf_counter()):.1f} deg")
            except RuntimeError as e:
                print(f"  {e}")
        elif k == 82 or k == ord('w'): cfg["hoop_dy"] -= 1
        elif k == 84 or k == ord('x'): cfg["hoop_dy"] += 1
        elif k == 81 or k == ord('a'): cfg["hoop_dx"] -= 1
        elif k == 83 or k == ord('d'): cfg["hoop_dx"] += 1

    cv2.destroyAllWindows()


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(cam, cfg, dry=False, score=0, max_shots=None, auto_bias=True,
        max_score=None, lives=3, should_stop=None):
    global cfg_hoop_lo, cfg_hoop_hi
    cfg_hoop_lo, cfg_hoop_hi = cfg["hoop_hsv_lo"], cfg["hoop_hsv_hi"]
    clicker = Clicker()
    bias_hist = []
    lat_hist  = []
    extra_settle = 0.0   # grows when the game ignores a click
    seen_hearts  = False # hearts are absent until the run actually starts
    pending = None   # last throw, awaiting the next hoop reading to grade it
    # Centre of the canvas: any click that is not on the exit button (game
    # coords 892..960 x 500..540) triggers a throw.
    cx = cam.rect["left"] + cam.rect["width"] // 2
    cy = cam.rect["top"]  + cam.rect["height"] // 2

    plat = PlatformTracker(cam, cfg)
    bias = cfg["launch_y_bias"]
    print(f"\n[Bot] {'DRY RUN - no clicks will be sent' if dry else 'playing'}   "
          f"score={score}  launch_y_bias={bias:+.0f}px  "
          f"latency={cfg['click_latency_s'] * 1000:.0f}ms")
    if max_score is not None:
        print(f"[Bot] stopping at score {max_score}: from there it will throw "
              f"deliberate misses until the {lives} lives run out, which ends "
              f"the run at exactly that score.")
    print("[Bot] Ctrl+C to stop.\n")

    plat.pump(2.0)
    shots = made = 0
    stalls = 0          # consecutive cycles that produced no shot

    while max_shots is None or shots < max_shots:
        # Between shots, for the same reason as darts: the ball is mid-flight
        # on a timer this loop owns, and stopping there throws the shot away.
        if should_stop is not None and should_stop():
            print("[Bot] stopped")
            break
        if stalls >= 8:
            print("[Bot] 8 cycles without a usable shot - stopping rather than "
                  "spinning.  Check --calibrate.")
            break
        plat.pump(1.2)
        try:
            clock, amp, rms = plat.fit()
        except RuntimeError as e:
            print(f"[Phase] {e}\n"
                  f"        (is the game on the Swishy Hoops screen?)  Retrying.\n")
            plat.pump(1.5)
            stalls += 1
            continue

        if not plat.fit_is_trustworthy(amp, rms):
            print("        The platform is not oscillating the way the game says "
                  "it must (335 +/- 110 px).\n"
                  "        Either the game is not on the Swishy Hoops screen, or "
                  "the platform mask is picking up\n"
                  "        the wrong thing.  Refusing to shoot -- run --calibrate "
                  "and click the ledge to re-read\n"
                  "        its colour.  Retrying in 2s.\n")
            plat.ts.clear(); plat.ys.clear()
            plat.pump(2.0)
            stalls += 1
            continue

        # Read the HUD.  Score and lives both come straight off the screen, so
        # nothing downstream has to model when a basket counts or when a miss
        # costs a life -- every attempt at inferring those has been wrong.
        hud_frame, _ = cam.grab()
        hud_digits = score_digits(hud_frame, cam)
        hud_lives  = read_lives(hud_frame, cam)
        tally      = read_score_tally(hud_frame, cam)
        if hud_lives:
            seen_hearts = True
        # The tally only agrees with the score while it is small, so trust it
        # to seed the count and never to override a running one.
        if pending is None and tally is not None and tally <= 9 and score == 0:
            score = tally

        hoop, hoop_ok, hoop_score = sample_hoop(cam, cfg, score)
        if hoop_score is not None:
            score = hoop_score

        # Grade the previous throw against the HUD.
        if pending is not None:
            gained = score_text_changed(pending["digits_before"], hud_digits)
            lost   = (pending["lives_before"] - hud_lives) if hud_lives is not None else None

            if gained:
                made += 1
                score += pending["points"]
                print(f"        -> SCORED (score readout changed).  "
                      f"score now ~{score}")
            elif lost:
                print(f"        -> MISSED.  lives {hud_lives}/{pending['lives_before']}")
                _save_miss_evidence(pending, cam, cfg)
            else:
                # Neither counter moved: the game never accepted the click.
                # `[90]` (throw animation) or `[103]` (ball in play) was still
                # non-zero, so nothing was thrown and nothing was lost.  This is
                # not a miss and must not be charged as one.
                shots -= 1
                extra_settle = min(extra_settle + 1.0, 5.0)
                print(f"        -> NO THROW - the game ignored the click "
                      f"(score and lives both unchanged).")
                print(f"        Not a shot and not a life.  Waiting "
                      f"{2.7 + extra_settle:.1f}s before the next one.")
            print(f"        {made}/{shots} makes  |  score {score}"
                  f"{f'  lives {hud_lives}' if hud_lives is not None else ''}\n")
            pending = None

            # Only trust a zero once hearts have actually been seen: before the
            # first throw the game shows none, and stopping on that would end
            # the run before it began.
            if hud_lives == 0 and seen_hearts:
                print(f"[Bot] Out of lives at score {score}.  The game will not "
                      f"accept another throw - hit EXIT in-game to bank it.")
                break

        if not hoop_ok:
            print(f"        The hoop is not behaving the way score={score} "
                  f"predicts, so its position at impact cannot be trusted.\n"
                  f"        Check that --score matches the number on screen. "
                  f"Refusing to shoot; retrying.\n")
            plat.pump(1.0)
            stalls += 1
            continue

        capping = max_score is not None and score >= max_score

        # Plan from far enough in the future that the search cannot expire the
        # window it just found.  Simulating the bounces costs ~0.28 s worst
        # case, and the click still has to be issued after that.
        now = time.perf_counter()
        shot, half = plan_shot(clock, hoop, score, now + 0.8,
                               launch_bias=bias, miss=capping)

        hx, hy = hoop.at(now)
        tag = "  [CAPPING - throwing away]" if capping else ""
        print(f"[Shot {shots + 1}] score={score}"
              f"{f'/{max_score}' if max_score is not None else ''}  "
              f"lives {hud_lives if hud_lives is not None else '?'}  "
              f"hoop=({hx:.0f},{hy:.0f})"
              f"{tag}  {describe(shot, half, miss=capping)}")

        if shot is None or half < 0.020:
            # About 4% of hoop spawns have no clean-swish window at all -- the
            # ball's reachable arc just misses by 2-12 px, and only a rim or
            # backboard bounce can score.  The hoop is only re-rolled after a
            # shot completes, so waiting cannot help; better to hold than to
            # spend one of three lives on a coin flip.
            if capping:
                # A miss is only accepted if re-simulating it under +/-100 ms of
                # timing error and +/-20 px of aim error never scores.  About
                # one hoop in eight admits no such throw, and the hoop does not
                # re-roll until a shot completes, so waiting cannot help.
                print(f"        No throw is provably safe for this hoop, and "
                      f"guessing could push you past {max_score}.\n"
                      f"        Hit EXIT in-game to bank {score} points.")
            else:
                print("        no usable window - holding.  Take this one "
                      "manually, or Ctrl+C.")
            plat.pump(1.0)
            stalls += 1
            continue

        stalls = 0
        t_click = shot.t0 - cfg["click_latency_s"]
        if t_click <= time.perf_counter():
            # Planning overran its own lead time.  Never fire late -- a late
            # click is a miss, and misses are the scarce resource here.
            print("        planning overran the window; re-planning")
            stalls += 1
            continue

        if dry:
            shots += 1
            print(f"        would click in {t_click - now:+.3f}s\n")
            plat.pump(max(0.0, shot.t0 - time.perf_counter()))
            continue

        if not gamewindow.point_belongs_to(cam.rect["hwnd"], cx, cy):
            print(f"        Something is covering the game at ({cx},{cy}) - the "
                  f"click would go to that window instead, not to the game.")
            print(f"        Bring Idleon to the front (or move the other window) "
                  f"- not throwing.")
            plat.pump(1.5)
            stalls += 1
            continue

        # Cross-check the model against the screen just before committing.
        # The plan extrapolates the hoop up to ~2 s ahead; if the fit was wrong
        # the error shows up here, and a life is far more expensive than a
        # re-plan.  Done 0.25 s out so it cannot disturb the click timing.
        if t_click - time.perf_counter() > 0.35:
            sleep_until(t_click - 0.25)
            vf, vt = cam.grab()
            seen = detect_hoop(vf, cam, cfg)
            if seen is not None:
                want = hoop.at(vt)
                off = math.hypot(seen[0] - want[0], seen[1] - want[1])
                if off > 10.0:
                    print(f"        Hoop is {off:.0f}px from where the model "
                          f"says it should be right now "
                          f"(saw {seen[0]:.0f},{seen[1]:.0f}, "
                          f"expected {want[0]:.0f},{want[1]:.0f}).")
                    print(f"        The fit has drifted - re-planning instead "
                          f"of spending a life.")
                    stalls += 1
                    continue

        sent = clicker.click_at(cx, cy, t_click)
        shots += 1
        print(f"        clicked ({(sent - t_click) * 1000:+.2f}ms off plan)")

        # Watch the ball on the way to the hoop.  This is free -- the shot is
        # already in the air -- and it turns the one derived constant in the
        # model into a measured one after a single throw, which matters when
        # you only get three misses.
        m = measure_launch_bias(cam, cfg, shot)
        if m is None:
            print("        [ball not tracked in flight - cannot measure aim; "
                  "see misses/ if this throw turns out to have missed]")
        if m is not None:
            b, dvy, dt_s, n = m
            bias_hist.append(b)
            lat_hist.append(dt_s)
            avg = sum(bias_hist) / len(bias_hist)
            print(f"        ball tracked over {n} samples: height {b:+.1f}px vs "
                  f"model, timing {dt_s * 1000:+.0f}ms")
            if abs(dvy) > 0.02:
                print(f"        (dVy={dvy:+.3f}/step - that is a clock error, "
                      f"not a height error; check the phase fit)")

            lat_avg = sum(lat_hist) / len(lat_hist)
            if auto_bias and len(lat_hist) >= 2 and abs(lat_avg) > 0.012:
                # Clamped for the same reason as the bias: a feedback loop that
                # can take a big step on two samples can run away, and this one
                # has no independent check on its sign.
                step = max(-0.030, min(0.030, lat_avg))
                cfg["click_latency_s"] = max(0.0, min(0.200,
                                             cfg["click_latency_s"] + step))
                lat_hist.clear()
                print(f"        -> click_latency now "
                      f"{cfg['click_latency_s'] * 1000:.0f}ms "
                      f"(save it in hoops_config.json to keep it)")
            # Only a constant offset can be corrected by a constant.  A large
            # dVy means the discrepancy grows through the flight, so the model
            # is wrong about the release *velocity*, and shifting the release
            # height just trades one error for another.
            steady = abs(dvy) <= 0.05
            if auto_bias and len(bias_hist) >= 2 and abs(avg) > 3.0 and steady:
                # The residual is measured with the current bias already
                # applied, so this is a plain Newton step -- and it must ADD.
                # `b` is (observed - predicted) and positive means the ball flew
                # below the model; `simulate` adds launch_bias to the release y,
                # where larger y is lower.  So closing the gap means raising the
                # bias.  This subtracted instead, which doubled the error on
                # every correction: a run measuring +40 px drove the bias to
                # -80 and turned four straight makes into three straight misses.
                step = max(-15.0, min(15.0, avg))
                bias = max(-40.0, min(40.0, bias + step))
                bias_hist.clear()
                print(f"        -> launch_y_bias now {bias:+.1f}px "
                      f"(pass --bias {bias:+.0f} next run, or save it in "
                      f"hoops_config.json)")
            elif auto_bias and len(bias_hist) >= 2 and abs(avg) > 3.0:
                bias_hist.clear()
                print(f"        (not correcting: dVy={dvy:+.3f} says this is a "
                      f"velocity error, not a constant height offset)")

        # Grab the frame at the instant the ball should be passing through the
        # net.  Kept only if the throw turns out to have missed, in which case
        # it shows the ball and the aim point side by side -- which is the one
        # thing a log of predictions can never tell you.
        t_impact = shot.t0 + (H.RELEASE_STEP + shot.steps) * H.STEP_S
        hx_i, hy_i = hoop.at(t_impact)
        shot_target_x = hx_i + H.TARGETS[0][1]
        shot_target_y = hy_i + H.TARGETS[0][2]
        impact_frame = None
        if t_impact - time.perf_counter() > 0:
            sleep_until(t_impact)
            impact_frame, _ = cam.grab()

        # Then the ~2.5 s the game spends settling the ball and re-rolling the
        # hoop ([103] counts up to 2.5).  Keep sampling the platform through it
        # so the next fit is already warm.
        plat.pump(2.7 + extra_settle)

        # The result is not known yet.  Park it; the next loop's hoop reading
        # is what decides, and inventing an answer here is what went wrong
        # before.
        pending = {
            "digits_before": hud_digits,
            "lives_before":  hud_lives,
            "hoop_base": (hoop.base_x, hoop.base_y),
            "points":    shot.points,
            "shot":      shot,
            "frame":     impact_frame,
            "target":    (shot_target_x, shot_target_y),
            "n":         shots,
        }
        print(f"        predicted {shot.outcome} (+{shot.points}) - "
              f"awaiting confirmation")

    if pending is not None:
        print(f"[Bot] last throw's result unconfirmed (stopped before the next "
              f"hoop reading)")
    print(f"[Bot] {made}/{shots} confirmed makes, final score {score}")
    return shots, made, score


def main():
    ap = argparse.ArgumentParser(description="Idleon Swishy Hoops bot")
    ap.add_argument("--calibrate", action="store_true",
                    help="live overlay to verify detection; sends no clicks")
    ap.add_argument("--dry", action="store_true",
                    help="plan real shots and print them, but never click")
    ap.add_argument("--score", type=int, default=0,
                    help="current in-game score (affects hoop/platform motion)")
    ap.add_argument("--max-score", type=int, default=None, metavar="N",
                    help="stop scoring at N: from there the bot throws "
                         "deliberate clean misses until its lives run out, "
                         "ending the run at exactly N")
    ap.add_argument("--lives", type=int, default=3,
                    help="misses allowed before the game locks you out "
                         "(default 3; misses before your first score are free)")
    ap.add_argument("--shots", type=int, default=None, help="stop after N shots")
    ap.add_argument("--bias", type=float, default=None, metavar="PX",
                    help="override launch_y_bias for this run; positive aims "
                         "lower.  Use if shots miss consistently high or low.")
    ap.add_argument("--latency", type=float, default=None, metavar="MS",
                    help="override click_latency_s for this run")
    ap.add_argument("--no-auto-bias", action="store_true",
                    help="report the measured launch-height error but do not "
                         "correct for it automatically")
    ap.add_argument("--no-lock", action="store_true",
                    help="do not move the game window")
    ap.add_argument("--at", default="0,0", metavar="X,Y",
                    help="screen position to park the window at (default 0,0)")
    ap.add_argument("--list-windows", action="store_true",
                    help="print every visible top-level window and exit")
    args = ap.parse_args()

    if args.list_windows:
        gamewindow.set_dpi_aware()
        for c in gamewindow.enumerate_candidates():
            print(f"{c['exe']:<30} {c['cls']:<24} {c['rect']}  {c['title']!r}")
        return 0

    cfg = load_config()
    if args.bias is not None:
        cfg["launch_y_bias"] = args.bias
    if args.latency is not None:
        cfg["click_latency_s"] = args.latency / 1000.0
    x, y = (int(v) for v in args.at.split(","))

    try:
        rect = gamewindow.acquire(lock=not args.no_lock, x=x, y=y)
    except RuntimeError as e:
        print(f"[Window] {e}")
        return 1

    print(f"[Window] {rect['exe']}  {rect['title']!r}")
    print(f"[Window] canvas at ({rect['left']},{rect['top']}) "
          f"{rect['width']}x{rect['height']}  scale={rect['scale']:.4f}  "
          f"dpi={rect['dpi_mode']}")
    if abs(rect["scale"] - 1.0) > 0.001:
        print(f"         (canvas is scaled {rect['scale']:.3f}x; detection works "
              f"but is slightly coarser than at 1:1)")

    with mss.mss() as sct:
        cam = Camera(sct, rect)
        if args.calibrate:
            run_calibrate(cam, cfg)
            return 0
        try:
            run(cam, cfg, dry=args.dry, score=args.score, max_shots=args.shots,
                auto_bias=not args.no_auto_bias,
                max_score=args.max_score, lives=args.lives)
        except KeyboardInterrupt:
            print("\n[Bot] stopped")
        finally:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
