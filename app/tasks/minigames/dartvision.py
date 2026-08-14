#!/usr/bin/env python3
"""
Screen reading for Throwy Darts.

Three things have to come off the screen: the board's band boundaries, the
platform height `[144]`, and the phase of the aim oscillation.

The aim is the interesting one.  The dart the player holds pivots about the
hand, so its tip traces a circle; fitting that circle gives the pivot, and the
angle to the tip then tracks the aim.  That angle is not the aim angle exactly --
the rightmost lit pixel sits off the dart's axis, which showed up as a constant
5.4 deg bias when measured against the game's own model -- but it does not need
to be.  Fitting it at the rate the source dictates recovers:

    amplitude 38.2 deg   (source says 38)
    residual   0.47 deg

so the amplitude and rate confirm the model, and the *phase* is what gets used.
Amplitude and offset are then taken from the source rather than from the
measurement, which is what makes the constant bias harmless.  This is the same
arrangement as the platform in hoops, where a sprite-margin offset falls out of
the fit and only the phase is consumed.
"""

import json
import math
import os
import time

import cv2
import numpy as np

import dartsim as D
from idleon_hoops_bot import fit_phase

# ── Board ─────────────────────────────────────────────────────────────────────

def read_board(frame, cam):
    """
    The six band boundaries, measured from colour transitions down the board.

    Returns a 6-tuple of game-y values, or None.  Preferred over
    dartsim.DEFAULT_BOUNDS: the constants there were measured off one board and
    `[157]` is never assigned inside `_event_Darts`, so there is no source of
    truth to fall back on other than the screen.
    """
    x0, x1 = cam.to_screen(920), cam.to_screen(955)
    y0, y1 = cam.to_screen(D.BOARD_Y_MIN), cam.to_screen(D.BOARD_Y_MAX)
    col = frame[y0:y1, x0:x1]
    if col.size == 0:
        return None

    med = np.median(col, axis=1).astype(int)
    # The bands are wide blocks of flat colour; the dividers are thin light
    # lines.  Smooth first so the pixel-art dithering inside a band does not
    # register as an edge.
    k = max(3, int(round(5 * cam.scale)) | 1)
    sm = cv2.GaussianBlur(med.astype(np.float32), (1, k), 0)
    d = np.abs(np.diff(sm, axis=0)).sum(axis=1)

    edges = []
    thresh = max(18.0, float(np.percentile(d, 96)))
    for i, v in enumerate(d):
        y = cam.to_game(i) + D.BOARD_Y_MIN
        if v >= thresh and (not edges or y - edges[-1] > 12):
            edges.append(y)

    # Seven bands means six interior boundaries.  Anything else means the board
    # was not read cleanly and guessing would be worse than saying so.
    if len(edges) != 6:
        return None
    return tuple(edges)


def red_band(frame, cam):
    """
    Locate the red centre directly, as a cross-check on read_board.
    Returns (y_top, y_bottom) or None.
    """
    x0, x1 = cam.to_screen(920), cam.to_screen(955)
    y0, y1 = cam.to_screen(D.BOARD_Y_MIN), cam.to_screen(D.BOARD_Y_MAX)
    col = frame[y0:y1, x0:x1]
    if col.size == 0:
        return None
    hsv = cv2.cvtColor(col, cv2.COLOR_BGR2HSV)
    m = cv2.bitwise_or(cv2.inRange(hsv, np.array([0, 120, 90]),
                                   np.array([8, 255, 255])),
                       cv2.inRange(hsv, np.array([172, 120, 90]),
                                   np.array([179, 255, 255])))
    rows = np.nonzero(m.sum(axis=1) > m.shape[1] * 0.4)[0]
    if len(rows) < 5:
        return None
    return (cam.to_game(rows.min()) + D.BOARD_Y_MIN,
            cam.to_game(rows.max()) + D.BOARD_Y_MIN)


# ── Aim ───────────────────────────────────────────────────────────────────────

# The held dart is near-white against brown planks, so a plain lightness gate
# separates it.
#
# The vertical span has to cover every platform height, not just the one that
# happened to be on screen when this was written.  `[144]` is re-rolled to
# randInt(280,400) after *every* throw, which puts the pivot anywhere in
# 224..344 and the tip anywhere in ~160..408.  A window of 150..350 clipped the
# tip whenever the platform rolled low, and a clipped tip wrecks the circle fit:
# the fitted amplitude came back as 85-180 deg against the true 38, and the
# trustworthiness gate then refused every throw for the rest of the run.
#
# It cannot simply be opened wide, though -- bright pixels also live in the HUD
# strip along the top and at the very bottom edge.  Measured within the player's
# x band, the only bright groups are y 34..42 (HUD), the player and dart, and a
# single row at 539.  So the window spans the full tip range and stops short of
# both.
# x has to cover the platform's full travel, not just where it sits at
# difficulty 0.  `[143] = clamp(40, 550, 300 - 1.5*d + 350*rand*(d/(d+50)))`, so
# by d=14 the player can be anywhere from ~200 to ~350 -- a window of 340..480
# simply had no player in it, and every cycle reported "cannot fit the aim"
# while the dart sat plainly visible at x~290.
#
# Widening is safe here because dart_angle filters on elongation: the dart is a
# long thin blob and the player's body is not.  The cap at 540 keeps darts
# already stuck in the board (x >= 850) out of it.
_TIP_ROI = (100, 500, 150, 540)      # y0, y1, x0, x1 in game coordinates
_TIP_LO = np.array([0, 0, 150])
_TIP_HI = np.array([179, 60, 255])


def dart_tip(frame, cam):
    """Rightmost bright pixel of the held dart, in game coords, or None."""
    y0, y1, x0, x1 = _TIP_ROI
    roi = frame[cam.to_screen(y0):cam.to_screen(y1),
                cam.to_screen(x0):cam.to_screen(x1)]
    if roi.size == 0:
        return None
    m = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), _TIP_LO, _TIP_HI)
    ys, xs = np.nonzero(m)
    if len(xs) < 20:
        return None
    i = int(np.argmax(xs))
    return (cam.to_game(xs[i]) + x0, cam.to_game(ys[i]) + y0)


def platform_y(frame, cam):
    """
    The platform's top edge, which is where the game places `[144]`.

    Measured rather than derived.  `[144]` can also be inferred from the dart's
    pivot -- the launch point rotates about the same centre, at `[144] - 56` --
    but that route runs through a circle fit on a tip whose pixel sits off the
    dart's axis, and it came out at 406 on a board where the game only ever
    rolls 280..400.  The plank's edge is a direct reading of the sprite the game
    positions at `[144]`, and it landed inside the legal range.

    Returns a game y, or None.
    """
    # Same trap as the aim ROI: the platform slides with difficulty, so a fixed
    # x window of 300..420 simply had no plank in it once `[143]` drifted, and
    # the bot refused to throw while the plank was plainly on screen at x~250.
    y0, y1 = cam.to_screen(240), cam.to_screen(500)
    x0, x1 = cam.to_screen(150), cam.to_screen(570)
    band = frame[y0:y1, x0:x1].astype(int)
    if band.size == 0:
        return None

    # With a wide window the plank no longer fills the row, so a median over it
    # is dominated by wall.  Score each row by how many of its pixels are
    # plank-dark instead, and take the first row where a wide run appears.
    dark = (band.sum(axis=2) < band.sum(axis=2).mean() * 0.86)
    runs = dark.sum(axis=1)
    need = cam.to_screen(60)          # the plank is ~100 px wide
    cand = [i for i, v in enumerate(runs) if v >= need]
    if cand:
        y = cam.to_game(cand[0]) + 240
        if PLAT_Y_MIN <= y <= PLAT_Y_MAX:
            return y

    rowmed = np.median(band, axis=1)
    d = np.abs(np.diff(rowmed, axis=0)).sum(axis=1)
    # The plank is darker than the plank wall behind it, so its top edge is a
    # step down in brightness -- direction matters, or the player's own outline
    # wins.
    darker = rowmed[1:].sum(axis=1) < rowmed[:-1].sum(axis=1)
    cand = [i for i, (v, dk) in enumerate(zip(d, darker)) if v > 45 and dk]
    if not cand:
        return None

    y = cam.to_game(cand[0]) + 240
    if not (PLAT_Y_MIN <= y <= PLAT_Y_MAX):
        return None
    return y


def platform_xy(frame, cam):
    """
    The platform's top edge AND its horizontal centre, as (x, y) in game
    coordinates.  Either may be None.

    `[143]` was previously taken from `dartsim.platform_x(difficulty)`, i.e.
    `300 - 1.5*d`, on the reading that the random term is scaled by d/(d+50).
    Measured against the screen that is wrong by about +60 px: the plank sits
    near x=370, and the formula not only starts 60 px left of it but then walks
    the wrong way as difficulty climbs.

    It matters because the launch x is `[143] + 117`, so an error here changes
    the LENGTH of the flight, and a shorter flight means less time under
    gravity.  60 px of it lifts every landing by ~20 px -- constant, unrelated
    to when the click happened, and identical in sign whichever way the aim was
    sweeping.  That is exactly the residual that survived the timing fix.

    So measure it, the same way plat_y already is.
    """
    y0, y1 = cam.to_screen(240), cam.to_screen(500)
    x0, x1 = cam.to_screen(150), cam.to_screen(570)
    band = frame[y0:y1, x0:x1].astype(int)
    if band.size == 0:
        return None, None

    dark = (band.sum(axis=2) < band.sum(axis=2).mean() * 0.86)
    runs = dark.sum(axis=1)
    need = cam.to_screen(60)
    cand = [i for i, v in enumerate(runs) if v >= need]
    if not cand:
        return None, platform_y(frame, cam)

    row = cand[0]
    y = cam.to_game(row) + 240
    if not (PLAT_Y_MIN <= y <= PLAT_Y_MAX):
        return None, platform_y(frame, cam)

    # Take the widest contiguous dark run on the plank's top row, and use its
    # centre.  Isolated dark pixels from the player's outline sit outside it.
    cols = np.nonzero(dark[row])[0]
    best_a = best_b = None
    a = cols[0]
    for p, q in zip(cols, list(cols[1:]) + [cols[-1] + 99]):
        if q - p > 1:
            if best_a is None or (p - a) > (best_b - best_a):
                best_a, best_b = a, p
            a = q
    if best_a is None:
        return None, y
    cx = cam.to_game((best_a + best_b) / 2.0) + 150
    return cx, y


def fit_circle(points):
    """Algebraic circle fit.  Returns (cx, cy, r)."""
    P = np.asarray(points, dtype=float)
    A = np.c_[2 * P[:, 0], 2 * P[:, 1], np.ones(len(P))]
    b = (P ** 2).sum(axis=1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0], sol[1]
    return cx, cy, math.sqrt(max(0.0, sol[2] + cx * cx + cy * cy))


ARM_MIN, ARM_MAX = 48.0, 88.0     # plausible dart-arm radius, px

# `[144] = randInt(280,400) + clamp(-120, 120, ...)`.  Only the base range was
# used at first, so any platform outside 270..410 had its reading discarded --
# and a low platform then produced "cannot see the platform" eight cycles in a
# row with the plank plainly visible on screen.
PLAT_Y_MIN, PLAT_Y_MAX = 155.0, 525.0

# Aim measurement, second attempt.  The first took the rightmost bright pixel as
# the dart's tip and treated the angle to it as the aim angle.  That is wrong in
# a way that matters: the dart is a rotated rectangle with width, so its
# rightmost pixel walks along a corner as it turns.  The resulting error is
# angle-dependent, not the constant bias assumed, and ~20 deg of it reads as
# ~200 ms of apparent click latency -- which is why every latency estimate
# disagreed with the last (105, 168, -31, -231 ms).
#
# Measuring the blob's ORIENTATION instead is invariant to sprite width.
# Against a real 90 s recording it recovers -59.1..+21.8 deg where the source
# says -58..+18, and fitting it gives amplitude and offset that match the
# source's 38 deg / -20 deg with residuals of 0.3-0.4 deg.
_DART_ELONGATION = 2.5      # the held dart is long and thin; the player is not


def dart_angle(frame, cam):
    """
    Orientation of the held dart in degrees, or None.

    Positive is downward (screen y grows down), matching the game's `[155]`.
    """
    y0, y1, x0, x1 = _TIP_ROI
    roi = frame[cam.to_screen(y0):cam.to_screen(y1),
                cam.to_screen(x0):cam.to_screen(x1)]
    if roi.size == 0:
        return None
    m = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), _TIP_LO, _TIP_HI)
    n, lbl, stats, _c = cv2.connectedComponentsWithStats(m, 8)

    best = None
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 60:
            continue
        ys, xs = np.nonzero(lbl == i)
        if len(xs) < 60:
            continue
        pts = np.stack([xs, ys]).astype(float)
        pts -= pts.mean(axis=1, keepdims=True)
        w, v = np.linalg.eigh(np.cov(pts))
        if math.sqrt(max(w[1], 1e-9) / max(w[0], 1e-9)) < _DART_ELONGATION:
            continue                     # too round to be the dart
        ang = math.degrees(math.atan2(v[1, 1], v[0, 1]))
        ang = (ang + 90) % 180 - 90      # fold to -90..90
        cx, cy = xs.mean(), ys.mean()
        if best is None or cx > best[0]:  # the dart is right of the player
            best = (cx, ang, cam.to_game(cx) + x0, cam.to_game(cy) + y0)
    return None if best is None else (best[1], best[2], best[3])


def fit_aim_angle(times, angles, centroids=None, d_max=60):
    """
    Fit the aim directly in angle space, and recover the difficulty.

    Returns (AimClock, difficulty, amplitude, offset, rms_deg) or None.

    Because the angle is now measured rather than inferred, both the amplitude
    and the offset are *predictions* of the source (38 deg and -20 deg at
    difficulty 0) rather than free parameters -- so they serve as the
    correctness check, exactly as the platform's 110 px amplitude does in hoops.
    """
    T = np.asarray(times, dtype=float)
    G = np.asarray(angles, dtype=float)
    ok = ~np.isnan(G)
    if ok.sum() < 60:
        return None
    T, G = T[ok], G[ok]

    # d_max must cover the whole run, not a plausible-looking prefix.  Capped
    # at 25 it pinned every fit at the ceiling once the score passed ~150, the
    # residual climbed to 5 deg, and the bot refused every throw permanently --
    # restarting did not help because the game state, not the process, was out
    # of range.
    best = None
    for d in range(0, d_max + 1):
        amp_m, rate = D.aim_params(d)
        C, amp, psi, rms, t_ref = fit_phase(T, G, rate)
        cost = abs(amp - amp_m) + abs(C - D.AIM_OFFSET_DEG) + rms
        if best is None or cost < best[0]:
            best = (cost, d, amp, C, rms, psi, t_ref)
    _c, d, amp, C, rms, psi, t_ref = best
    clock = D.AimClock(psi, t_ref, d)

    # The platform falls out of the same observation.  The dart pivots about the
    # hand, so its centroid is `pivot + r*(cos th, sin th)`; with th now measured
    # rather than inferred, pivot_y and r are a two-parameter linear solve.  The
    # game places the launch point about that same pivot at `[144] - 56`.
    #
    # This replaces reading the plank off the screen, which had the same fixed-x
    # flaw as the aim window and silently returned nothing once the platform
    # slid with difficulty.
    plat_y = None
    if centroids is not None and len(centroids) == len(angles):
        cy = np.asarray([c[1] for c in centroids], dtype=float)[ok]
        sn = np.sin(np.radians(G))
        M = np.stack([np.ones_like(sn), sn], axis=1)
        coef, *_ = np.linalg.lstsq(M, cy, rcond=None)
        if 20.0 <= coef[1] <= 90.0:            # plausible pivot-to-centroid arm
            cand = coef[0] + 56.0
            if PLAT_Y_MIN <= cand <= PLAT_Y_MAX:
                plat_y = float(cand)
    return clock, d, amp, C, rms, plat_y


def aim_angle_is_trustworthy(difficulty, amp, offset, rms):
    """Amplitude and offset are known in advance; they are the check."""
    amp_m, _rate = D.aim_params(difficulty)
    # amp==0 with rms==0 is a *constant* angle series -- a frozen or ended
    # game, not a good fit.  It slipped through as "trustworthy-adjacent" and
    # produced offsets of 89.9 and -89.8 deg.
    if amp < 5.0:
        return False
    return (rms < 2.0 and abs(amp - amp_m) / amp_m < 0.10
            and abs(offset - D.AIM_OFFSET_DEG) < 6.0)



def fit_aim_direct(times, tips, difficulty=None, d_max=30, phi_step=1.0):
    """
    Recover the aim phase, and the difficulty, by fitting the game's own model
    to the dart tip's height.

        tip_y(t) = pivot_y + R * sin(theta(t))
        theta(t) = amp(d) * sin(rate(d)*t + phi) + AIM_OFFSET

    Grid over d and phi; `pivot_y` and `R` fall out of a linear solve.

    Two things this replaces, both of which failed on real data:

    1. Fitting a circle to the tip and reading angles off it.  Over a 2.2 s
       window the aim only covers part of its sweep, so the circle is
       ill-conditioned and the fitted centre wanders; every angle taken from it
       is then wrong.  Windows that produced amplitudes of 86-263 deg turned out
       to contain perfectly clean arcs -- the detector was fine, the estimator
       was not.

    2. Assuming difficulty 0.  `[160]` sets the sweep's amplitude *and its
       rate*, and it belongs to the game's session, not to the bot process --
       it survives the bot restarting.  Fitting at 97.5 deg/s when the game had
       moved to 127 deg/s gave a fitted arm radius of ~0, i.e. no correlation
       at all.

    Both amp and rate increase monotonically with `[160]`, so searching it
    inverts cleanly -- the same trick that recovers the score from the hoop's
    swing in hoops.

    Returns (AimClock, difficulty, platform_y, arm_R, rms_px) or None.
    """
    if len(tips) < 40:
        return None
    T = np.asarray(times, dtype=float)
    Xt = np.asarray([p[0] for p in tips], dtype=float)
    Yt = np.asarray([p[1] for p in tips], dtype=float)

    # Keep only detections that could be the HELD dart.  Its tip sweeps a ~30 px
    # band in x about the player's hand; a thrown dart crossing the gap to the
    # board is brighter and further right, and being "rightmost" it wins.  That
    # contamination pushed the observed x span to 137 px where the physical
    # limit is ~30, and it is why fits looked broken while the arcs themselves
    # were clean.  Centring on the median rather than a fixed band keeps this
    # working as the platform's x drifts with difficulty.
    xc = float(np.median(Xt))
    keep = np.abs(Xt - xc) <= 28.0
    if keep.sum() < 40:
        return None
    T, Yt = T[keep], Yt[keep]

    # A 64 px arm over the sweep cannot span more than ~75 px vertically.
    if float(Yt.max() - Yt.min()) > 85.0:
        return None

    t0 = T[0]
    dt = T - t0

    def _score(d, phis):
        """Best (rms, phi, pivot_y, R) over a vector of candidate phases.

        The two free parameters are linear, so the regression is closed-form --
        `lstsq` per phase made this 22,000 solves per fit and hung the live
        loop.  Phases are evaluated as one broadcast batch.
        """
        amp, rate = D.aim_params(d)
        ang = np.radians(amp * np.sin(np.radians(phis[:, None]
                                                + rate * dt[None, :]))
                         + D.AIM_OFFSET_DEG)
        sn = np.sin(ang)                                   # (n_phi, n_samples)
        sm = sn.mean(axis=1, keepdims=True)
        ym = Yt.mean()
        cov = ((sn - sm) * (Yt - ym)).mean(axis=1)
        var = ((sn - sm) ** 2).mean(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            R = np.where(var > 1e-9, cov / var, 0.0)
        a = ym - R * sm[:, 0]
        resid = Yt[None, :] - (a[:, None] + R[:, None] * sn)
        rms = np.sqrt((resid ** 2).mean(axis=1))
        rms = np.where((R >= ARM_MIN) & (R <= ARM_MAX), rms, np.inf)
        k = int(np.argmin(rms))
        return float(rms[k]), float(phis[k]), float(a[k]), float(R[k])

    cands = [difficulty] if difficulty is not None else range(0, d_max + 1)
    best = None
    coarse = np.arange(0.0, 360.0, 4.0)
    for d in cands:
        r, phi, a, R = _score(d, coarse)
        if best is None or r < best[0]:
            best = (r, d, phi, a, R)

    if best is None or not np.isfinite(best[0]):
        return None
    # refine the phase around the coarse winner
    d = best[1]
    fine = np.arange(best[2] - 4.0, best[2] + 4.0, phi_step)
    r, phi, a, R = _score(d, fine)
    if r < best[0]:
        best = (r, d, phi, a, R)

    rms, d, phi, pivot_y, R = best
    # The launch point shares the pivot, at [144] - 56.
    return (D.AimClock(phi, t0, d), d, pivot_y + 56.0, R, rms)


def fit_aim(times, tips, difficulty=0):
    """
    Recover the aim oscillation and the platform height from tip observations.

    Returns (AimClock, platform_y, amplitude_deg, rms_deg) or None.

    `platform_y` comes out of the same circle fit: the dart pivots about the
    hand, and the game places the launch point at `[144] + 72*sin(theta) - 56`
    about that same pivot, so the fitted centre is `[144] - 56`.
    """
    if len(tips) < 30:
        return None
    P = np.asarray(tips, dtype=float)
    T = np.asarray(times, dtype=float)

    cx, cy, _r = fit_circle(P)
    ang = np.degrees(np.arctan2(P[:, 1] - cy, P[:, 0] - cx))

    amp_model, rate = D.aim_params(difficulty)
    t_ref = T[0]
    w = math.radians(rate)
    dt = T - t_ref
    M = np.stack([np.ones_like(dt), np.sin(w * dt), np.cos(w * dt)], axis=1)
    coef, *_ = np.linalg.lstsq(M, ang, rcond=None)
    _C, A, B = coef
    amp = math.hypot(A, B)
    psi = math.degrees(math.atan2(B, A))
    rms = float(np.sqrt(np.mean((M @ coef - ang) ** 2)))

    # Phase from the measurement; amplitude and offset from the source.
    return D.AimClock(psi, t_ref, difficulty), cy + 56.0, amp, rms


def aim_direct_is_trustworthy(arm_r, rms):
    """
    Gate for fit_aim_direct.  The amplitude is an input here rather than an
    output, so it cannot serve as the check the way it did for the circle fit;
    the residual and a physically plausible arm length do the job instead.
    """
    return rms < 3.0 and ARM_MIN <= arm_r <= ARM_MAX


def aim_is_trustworthy(amp, rms, difficulty=0):
    """
    The fitted amplitude is known in advance, so it doubles as a check on the
    whole chain -- capture rectangle, scale and tip detection all have to be
    right for it to land on the source's value.
    """
    expect, _rate = D.aim_params(difficulty)
    return abs(amp - expect) / expect < 0.12 and rms < 3.0


# ── In-flight tracking (calibration) ──────────────────────────────────────────

def track_flight(cam, t_click, duration=1.2):
    """
    Follow the dart between the player and the board, returning [(t, x, y), ...].

    This exists because landing position alone cannot separate the three things
    that move a dart: when it actually launched, how high the platform was, and
    where the aim was in its sweep.  All three produce the same landing error,
    so inferring one from one number picks the wrong one about as often as not
    -- which is exactly what happened over seven live throws, each "correction"
    confidently pushing a different variable the wrong way.

    A trajectory is overdetermined instead.  Once launched, the dart's motion is
    fully specified by the source:

        x(n) = x0 + n*vx           vx = 5.2*cos(theta)     x0 = [143] + 117
        y(n) = y0 + n*vy + 0.033*n(n+1)/2
                                   vy = 5.2*sin(theta)
                                   y0 = [144] + 72*sin(theta) - 56

    so fitting x against time gives vx -- hence theta -- and the launch instant,
    with no reference to the click at all.  y then gives [144] independently.
    Latency falls out as (observed launch) - (planned launch).
    """
    import time as _t
    import numpy as _np
    pts = []
    t_end = _t.perf_counter() + duration
    while _t.perf_counter() < t_end:
        frame, t = cam.grab()
        # The dart is bright, and between player and board nothing else is.
        roi = frame[cam.to_screen(80):cam.to_screen(500),
                    cam.to_screen(460):cam.to_screen(910)]
        if roi.size == 0:
            continue
        m = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), _TIP_LO, _TIP_HI)
        ys, xs = _np.nonzero(m)
        if len(xs) >= 12:
            pts.append((t, cam.to_game(float(xs.mean())) + 460,
                        cam.to_game(float(ys.mean())) + 80))
    return pts


def solve_flight(pts, difficulty=0):
    """
    Recover (launch_time, theta_deg, platform_y) from a tracked flight.
    Returns None if the track is too short or inconsistent.
    """
    if len(pts) < 6:
        return None
    T = np.array([p[0] for p in pts])
    X = np.array([p[1] for p in pts])
    Y = np.array([p[2] for p in pts])

    # x is linear in time: x = x0 + vx_per_s * (t - t_launch)
    A = np.stack([np.ones_like(T), T], axis=1)
    (c, vx_s), *_ = np.linalg.lstsq(A, X, rcond=None)
    if vx_s <= 0:
        return None
    vx = vx_s * D.STEP_S                      # px per 10 ms step
    if not (1.0 < vx < D.DART_SPEED + 0.2):
        return None
    theta = math.degrees(math.acos(min(1.0, vx / D.DART_SPEED)))
    # the dart is thrown upward, so theta is negative
    theta = -theta

    x0 = D.platform_x(difficulty) + D.LAUNCH_DX
    t_launch = (x0 - c) / vx_s

    # y at launch, back out [144]
    n = (T - t_launch) / D.STEP_S
    vy = D.DART_SPEED * math.sin(math.radians(theta))
    y0 = float(np.median(Y - (n * vy + D.GRAVITY * n * (n + 1) / 2.0)))
    plat_y = y0 - D.LAUNCH_ARM * math.sin(math.radians(theta)) - D.LAUNCH_DY
    return t_launch, theta, plat_y


# ── Wind ──────────────────────────────────────────────────────────────────────
#
# `_event_Darts` re-rolls the wind after every throw once the difficulty is
# above zero:
#
#     DN    = randInt(300, 450 - [160]/([160]+40)*80)
#     [152] = 30*cos(DN)*randFloat(0.6,1)*([160]/([160]+40))
#     [153] = 30*sin(DN)*randFloat(0.6,1)*([160]/([160]+40))
#
# and applies it as `[140] += [152]/600`, `[141] += [153]/750` per step -- worth
# 30-70 px of drift at difficulty 9-14.  Leaving it out of the model is why
# accuracy fell apart as the difficulty climbed, and why the latency estimator
# thrashed: it was absorbing a per-throw random variable.
#
# The HUD shows it as a cyan arrow plus "N mph".  The arrow's direction is
# readable; the magnitude is bounded by the difficulty, which is measured.

# Just the arrow.  The "N mph" text is the same cyan and sits at x 503..540, so
# a window that reaches it drags the centroid into the digits; and the arrow
# itself spans y 11..41, so an 18 px top clipped its tip -- which is precisely
# the point the direction is measured from.
_WIND_ROI = (6, 48, 550, 605)           # y0, y1, x0, x1, game coords
# The readout is NOT one colour.  It is cyan at low wind speeds and magenta at
# high ones -- presumably a severity cue -- so a blue-only gate silently made
# every strong wind invisible to both the magnitude reader and the direction
# detector.  Measured hues on a real "11 mph" frame: 135-175, against 85-115
# for a "5 mph" one.  Both bands are matched, and nothing else in the HUD strip
# is this saturated.
# Saturation has to stay loose.  The glyph BODY sits around S=62 while its
# highlight edges are far more saturated, so an S>90 gate kept only the outline
# -- which split a "0" into its two vertical strokes and made "10" read as
# "111".  The background is dark (V about 58), so value is what actually
# separates text from panel here.
_WIND_BANDS = (
    (np.array([85, 40, 100]), np.array([125, 255, 255])),    # cyan / blue
    (np.array([130, 40, 100]), np.array([175, 255, 255])),   # magenta / violet
)
_WIND_LO = _WIND_BANDS[0][0]        # kept for callers that reference them
_WIND_HI = _WIND_BANDS[0][1]


def _wind_mask(bgr):
    """Mask of the wind readout, whichever colour it is showing."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, *_WIND_BANDS[0])
    for lo, hi in _WIND_BANDS[1:]:
        m = cv2.bitwise_or(m, cv2.inRange(hsv, lo, hi))
    return m


def read_wind_direction(frame, cam, difficulty=0):
    """
    Direction of the wind arrow in degrees, or None when there is no arrow
    (which is the case at difficulty 0, where the wind is exactly zero).

    Direction is taken as centroid -> farthest pixel: an arrowhead's tip is the
    extreme point of the blob, and unlike a PCA axis it gives a full 360 deg
    heading rather than one modulo 180.

    The game only ever rolls DN in [300, 450) degrees, i.e. -60..+90, so a
    result outside that is a misread and returns None rather than a wrong wind.
    """
    y0, y1, x0, x1 = _WIND_ROI
    roi = frame[cam.to_screen(y0):cam.to_screen(y1),
                cam.to_screen(x0):cam.to_screen(x1)]
    if roi.size == 0:
        return None
    m = _wind_mask(roi)
    ys, xs = np.nonzero(m)
    if len(xs) < 25:
        return None                      # no arrow -> no wind

    cx, cy = xs.mean(), ys.mean()
    d2 = (xs - cx) ** 2 + (ys - cy) ** 2
    k = int(np.argmax(d2))
    raw = math.degrees(math.atan2(ys[k] - cy, xs[k] - cx))

    # The farthest point from the centroid may be the arrow's head or the flare
    # of its tail, and the sprite has its own orientation baked in -- measured
    # +177.7 deg for a wind the game can only have rolled between -60 and +90.
    # Rather than assume which, try both and let the game's own constraint
    # decide: DN comes from randInt(300, 450), a 150 deg window, and the two
    # candidates are 180 deg apart, so at most one can be legal.
    # The legal band narrows with difficulty: DN = randInt(300, 450 - d/(d+40)*80),
    # so the upper edge falls from +90 at d=0 to about +60 by d=24.  A fixed
    # +/-92 window accepted +90 at d=24 -- a heading the game cannot roll -- and
    # so resolved the 180 deg ambiguity the wrong way.
    d = float(difficulty)
    hi = (450.0 - d / (d + 40.0) * 80.0) - 360.0      # e.g. +90 at d=0, +60 at d=24
    lo = -60.0
    legal = []
    for cand in (raw, raw - 180.0):
        a = (cand + 180.0) % 360.0 - 180.0
        if lo - 3.0 <= a <= hi + 3.0:
            legal.append(a)
    if len(legal) != 1:
        return None
    return legal[0]


def wind_candidates_exact(difficulty, direction_deg, mph):
    """
    The single (wind_x, wind_y) implied by the HUD, or None.

    `_DN = ceil(sqrt(wx^2+wy^2))` is what the HUD prints, so a readable number
    pins the magnitude to within one unit instead of the 40% band the
    difficulty alone allows -- collapsing the landing spread from ~12 px to
    almost nothing.
    """
    if difficulty <= 0:
        return [(0.0, 0.0)]
    if mph is None or direction_deg is None:
        return None
    a = math.radians(direction_deg)
    # ceil means the true magnitude is in (mph-1, mph]; take both ends.
    return [(m * math.cos(a), m * math.sin(a)) for m in (mph - 0.999, mph)]


def wind_candidates(difficulty, direction_deg, n=3):
    """
    Plausible (wind_x, wind_y) pairs given the measured difficulty and the
    arrow's direction.

    The magnitude is `30 * r * d/(d+40)` with `r` uniform in [0.6, 1] and
    re-rolled every throw, so it cannot be pinned from one frame -- but it *is*
    tightly bounded by the difficulty.  Returning the range as several
    candidates lets the planner demand a throw that works for all of them
    instead of betting on the midpoint.
    """
    if difficulty <= 0 or direction_deg is None:
        return [(0.0, 0.0)]
    peak = D.wind(difficulty)            # 30 * d/(d+40)
    a = math.radians(direction_deg)
    return [(peak * r * math.cos(a), peak * r * math.sin(a))
            for r in np.linspace(0.6, 1.0, n)]


# ── Wind magnitude (HUD "N mph") ──────────────────────────────────────────────
#
# `_DN = ceil(sqrt(wx^2 + wy^2))` is exactly what the HUD prints, so reading it
# collapses the magnitude from a range to a single integer -- and the landing
# spread from ~12 px to ~0.  Worth the trouble: the wind is re-rolled after every
# throw (60% chance of a fresh one, 40% of none at all), so it cannot be
# calibrated once the way latency can.
#
# The digits are a bitmap font that is not shipped locally, so templates have to
# be harvested from real frames.  Only the ones actually observed are stored;
# an unrecognised glyph returns None so the caller refuses to throw rather than
# guessing a wind it cannot read.

_DIGIT_PITCH = 8       # measured: 7 px glyph + 1 px spacing
_DIGITS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "wind_digits.json")
_DIGITS = None


def _load_digits():
    global _DIGITS
    if _DIGITS is None:
        try:
            with open(_DIGITS_PATH) as f:
                raw = json.load(f)
            _DIGITS = {k: np.array([[c == "#" for c in row] for row in v])
                       for k, v in raw.items()}
        except Exception:
            _DIGITS = {}
    return _DIGITS


UNKNOWN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "wind_unknown")


def _save_unknown(roi):
    """
    Keep a crop of any wind readout that cannot be parsed.

    The digits are a bitmap font that is not shipped with the game files, so the
    template set can only be built from glyphs actually seen.  Rather than
    guess at a missing one -- which would hand the planner a wrong wind -- the
    crop is written out to be labelled afterwards.
    """
    try:
        os.makedirs(UNKNOWN_DIR, exist_ok=True)
        n = len([f for f in os.listdir(UNKNOWN_DIR) if f.endswith(".png")])
        if n < 40:
            cv2.imwrite(os.path.join(UNKNOWN_DIR, f"wind_{int(time.time())}.png"),
                        cv2.resize(roi, None, fx=6, fy=6,
                                   interpolation=cv2.INTER_NEAREST))
    except Exception:
        pass


def wind_readout_present(frame, cam):
    """
    Whether the HUD is showing a wind readout at all.

    Distinguishes "no wind" from "wind we failed to read" -- the game draws
    nothing when the wind is zero, so coloured pixels here mean wind exists
    even if neither the number nor the arrow could be parsed.
    """
    # Only the ARROW region.  The arrow is drawn exclusively when the wind is
    # non-zero, which is precisely the distinction wanted, and it keeps this
    # independent of whatever the text panel happens to say.
    #
    # (At zero wind the panel prints "None" in white.  That would not have
    # matched anyway -- the mask requires saturation >= 40 and white has almost
    # none -- so the panel-wide version was not actually broken, just less
    # direct.)
    y0, y1, x0, x1 = _WIND_ROI
    roi = frame[cam.to_screen(y0):cam.to_screen(y1),
                cam.to_screen(x0):cam.to_screen(x1)]
    if roi.size == 0:
        return False
    return int(_wind_mask(roi).sum() // 255) > 25


def read_wind_mph(frame, cam):
    """
    The wind's magnitude in mph, as printed on the HUD, or None.

    None means either no wind is shown or the glyph is not in the template set;
    both are 'do not throw' rather than 'assume something'.
    """
    y0, y1 = cam.to_screen(24), cam.to_screen(48)
    x0, x1 = cam.to_screen(490), cam.to_screen(550)
    roi = frame[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    m = _wind_mask(roi)
    cols = np.nonzero(m.sum(axis=0))[0]
    if len(cols) == 0:
        return None

    # The number runs up to the space before "mph".  That space is the LARGEST
    # gap, not merely the first wide one: "11" puts a 6 px gap between its two
    # digits against 9 px before the text, so a fixed threshold cut the number
    # in half and read a lone "1".
    end = cols[-1]
    widest = 0
    for a, b in zip(cols, cols[1:]):
        if b - a > widest:
            widest, end = b - a, a
    if widest < 4:
        end = cols[-1]
    sub = m[:, cols[0]:end + 1]
    rows = np.nonzero(sub.sum(axis=1))[0]
    if len(rows) == 0:
        return None
    glyphs = sub[rows.min():rows.max() + 1] > 0

    # Split on blank columns.  This is a proportional font -- "1" is a 2 px bar
    # where the others are 7 px wide -- so a fixed pitch cannot work.  The
    # earlier failure on "11" was not the splitter but the number's extent:
    # its 6 px inter-digit gap was being mistaken for the space before "mph".
    digs, run = [], []
    for i in range(glyphs.shape[1]):
        if glyphs[:, i].any():
            run.append(i)
        elif run:
            digs.append(glyphs[:, run[0]:run[-1] + 1]); run = []
    if run:
        digs.append(glyphs[:, run[0]:run[-1] + 1])
    if not digs:
        return None

    lib = _load_digits()
    out = ""
    for dg in digs:
        # "1" is a bare 2 px bar in this font.  Normalising it to the 7 px box
        # the other glyphs use turns it into a solid rectangle that matches
        # almost anything -- it read "10" as "111".  Width identifies it on its
        # own, so it is handled before any template comparison.
        # Compare on a canonical grid rather than exact pixels: the same digit
        # comes out 9 or 10 rows tall depending on how the crossbar antialiases
        # frame to frame, so an equality test recognises nothing.
        d_norm = cv2.resize(dg.astype(np.uint8), (7, 10),
                            interpolation=cv2.INTER_NEAREST) > 0
        scored = []
        for label, tpl in lib.items():
            t_norm = cv2.resize(tpl.astype(np.uint8), (7, 10),
                                interpolation=cv2.INTER_NEAREST) > 0
            scored.append((float((t_norm == d_norm).mean()), label))
        ranked = sorted(scored, reverse=True)
        best_score, best = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0

        # Accept on an exact-ish match, OR on a decisive MARGIN over the next
        # best candidate.
        #
        # A fixed 0.95 floor alone cannot work, because the same digit renders
        # at different heights depending on the number it sits in: the "4" in
        # "14" is 8 rows tall, a lone "4" is 10.  Normalising both to a 7x10
        # grid stretches them differently, so a lone "4" scored only 0.771
        # against the template harvested from "14" -- correct digit, rejected,
        # and the run deadlocked refusing to throw blind.  Its runner-up was
        # 0.629, so the READING was never in doubt; only the absolute score was.
        #
        # The margin is the honest test of confidence here, and it still refuses
        # a glyph that genuinely resembles two digits, which is the case that
        # matters.  Templates for both renderings are harvested where known, so
        # this path is the fallback, not the norm.
        if best is None or (best_score < 0.95
                            and not (best_score >= 0.75
                                     and best_score - runner_up >= 0.12)):
            _save_unknown(roi)
            return None                 # unknown glyph -> refuse, never guess
        out += best.split("_")[0]
    try:
        val = int(out)
    except ValueError:
        return None

    # The magnitude is `30 * r * d/(d+40)` with r <= 1, so it can never exceed
    # 30.  A misread that produces something larger -- "10" currently comes
    # through as "111" because the zero is being split into narrow runs -- must
    # not be handed to the planner as a real wind.
    if not (0 <= val <= 30):
        return None
    return val


# ── Game over ─────────────────────────────────────────────────────────────────
#
# When the last life is spent the game prints two centred lines:
#
#     Game over!
#     Exit to claim your points... come back soon!
#
# and an EXIT button becomes the only useful control.  Points are only banked by
# pressing it, and the cooldown does not start until then, so leaving the bot
# sitting on the dead screen wastes real time.
#
# The SECOND line is the discriminator.  The start screen also draws centred
# white text ("Hit the board to start the game!"), but one line higher, so a
# band placed on the second line separates them without any text matching.
# Checked against all 30 full frames of recording 1785490006, which include the
# start screen and normal play: 0 white pixels in this band, every frame.
_GAMEOVER_BAND = (95, 118, 300, 660)     # y0, y1, x0, x1 in game coords
# Measured on recording 1785509141_full: the real game-over screen holds 819
# white pixels in this band for its whole duration, while a mid-run transient
# on a single frame reached 64.  A threshold of 60 fired on that transient and
# would have pressed EXIT in the middle of a live run.  300 sits in the gap.
_GAMEOVER_MIN_PX = 300

# Centre of the EXIT button, game coords.
EXIT_BUTTON = (925, 518)


def game_over(frame, cam):
    """True if the darts run has ended and the EXIT button is waiting."""
    y0, y1, x0, x1 = _GAMEOVER_BAND
    roi = frame[cam.to_screen(y0):cam.to_screen(y1),
                cam.to_screen(x0):cam.to_screen(x1)]
    if roi.size == 0:
        return False
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    white = (hsv[:, :, 1] < 50) & (hsv[:, :, 2] > 185)
    return int(white.sum()) >= _GAMEOVER_MIN_PX


def wind_candidates_unknown_dir(difficulty, n_dir=5, n_mag=2, mph=None):
    """
    Candidate (wind_x, wind_y) when the magnitude is known but the ARROW is not.

    The previous code called `wind_candidates(difficulty, 0.0)` here, i.e. it
    assumed the wind blew exactly horizontally, which sets wind_y to zero.  That
    is the worst possible assumption: the VERTICAL component is the one that
    moves the landing height, so the planner was confidently solving the wrong
    problem.  Simulated cost of pretending a +/-45 deg wind is horizontal:

        d=10  +23/-19 px      d=25  +46/-36 px      d=40  +62/-48 px

    against a red band of +/-20 px.  At d>=10 that is a guaranteed miss on its
    own, and it matches the misses seen live (d=24..29 landing -36 to -93 px).

    Spanning the direction instead makes the planner refuse throws it cannot
    actually make, which is the honest outcome -- see the tiered fallback in
    darts_bot for what happens then.
    """
    if difficulty <= 0:
        return [(0.0, 0.0)]
    peak = D.wind(difficulty)
    mags = ([float(mph)] if mph is not None
            else list(np.linspace(0.6 * peak, peak, n_mag)))
    out = []
    for a in np.linspace(-75.0, 75.0, n_dir):
        r = math.radians(a)
        for m in mags:
            out.append((m * math.cos(r), m * math.sin(r)))
    return out
