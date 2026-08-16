#!/usr/bin/env python3
"""
Throwy Darts player.

Goal is nine bullseyes in a row.  The tactical shape of the game, from the
source:

  * Difficulty `[160]` scales both the wind and the platform's horizontal
    travel, and rises on essentially every scoring throw -- bullseyes included.
    It tracks throw count, not mistakes, so it cannot be kept at 0 by playing
    well.  A nine-streak therefore has to survive d=0 through d~10-15.
  * A miss still costs a dart and resets the streak (`[159]`).
  * Every third bullseye returns a dart, so a clean streak sustains itself.

The binding constraint is timing.  There is no throw animation -- the dart
leaves on the click step -- so the usable window is about +/-20 ms against
hoops' +/-150 ms.  Measured sensitivity: 20 ms of jitter gives ~93% bullseyes
(~55% for a nine-streak), 50 ms gives ~49% (~0.1%).  So this refuses to throw
until it has calibrated its own click latency, which it does by comparing where
a dart actually landed against where the plan said it would.
"""

import math
import random
import time

import cv2
import numpy as np

import dartsim as D
import dartvision as V
import minigame
from clicker import sleep_until
from core.frameclock import PERIOD as FRAME_S, FrameClock

# How good an aim fit has to be before a dart is spent on it.
#
# `aim_angle_is_trustworthy` rejects nonsense at 2.0 deg; this is the higher
# bar for actually throwing.  A healthy fit measures about 0.35 deg, and the
# throw that ended the best run so far was taken on 0.83 -- inside the old
# gate, obviously worse than usual, and it missed.  0.55 sits between the two.
GOOD_FIT_RMS = 0.55

# Above this difficulty the streak is already unreachable -- the red band is
# narrower than one frame of aim sweep -- so a merely-usable fit is thrown on
# rather than waited out.  Below it, waiting is nearly free and a miss is not.
STREAK_BAND_D = 15

# Bounded so a persistently noisy screen re-measures a few times and then gets
# on with it, instead of deadlocking the way earlier over-strict gates did.
MAX_REFITS = 3


def landed_dart_y(before, after, cam):
    """
    Where the last dart stuck, in game y, or None.

    Found by differencing the board column: the dart is the only thing that
    changes there between the throw and the next frame.
    """
    x0, x1 = cam.to_screen(D.BOARD_X_MIN), cam.to_screen(D.BOARD_X_MAX)
    y0, y1 = cam.to_screen(D.BOARD_Y_MIN), cam.to_screen(D.BOARD_Y_MAX)
    a = before[y0:y1, x0:x1].astype(int)
    b = after[y0:y1, x0:x1].astype(int)
    if a.shape != b.shape or a.size == 0:
        return None
    d = np.abs(b - a).sum(axis=2)
    rows = d.sum(axis=1)
    if rows.max() < 400:
        return None
    return cam.to_game(int(np.argmax(rows))) + D.BOARD_Y_MIN


def timing_error_from_landing(shot, aim, plat_y, actual_y, difficulty=0,
                              bounds=D.DEFAULT_BOUNDS, span=0.35):
    """
    How far off the planned instant the dart actually launched, in seconds.

    Found by searching: simulate launches either side of the planned one and
    take the nearest whose landing matches what was observed.

    The obvious alternative -- divide the landing error by the local slope --
    does not work, and got this badly wrong in practice.  The aim is a sinusoid,
    so d(landing)/d(launch time) changes sign across the sweep; a derivative
    taken at the planned instant can point the opposite way to the truth once
    the error is 100+ px, which is several hundred ms of a 3.69 s cycle and
    nowhere near the linear neighbourhood.  It inverted the sign on a real
    throw and pushed the latency further from the answer instead of towards it.
    """
    best = None
    t = shot.t0 - span
    while t <= shot.t0 + span:
        r = D.simulate(t, aim, plat_y, difficulty, bounds)
        if r.hit_y is not None:
            err = abs(r.hit_y - actual_y)
            key = (err, abs(t - shot.t0))
            if best is None or key < best[0]:
                best = (key, t)
        t += 0.002
    if best is None or best[0][0] > 12.0:
        return None
    return best[1] - shot.t0


def calibrate(cam, cfg, clicker):
    """
    Throw one dart purely to measure, and report what it actually did.

    Aim is not the point here -- the point is that a tracked flight pins the
    launch instant, the release angle and the platform height independently,
    where a landing position alone conflates all three.
    """
    cx = cam.rect["left"] + cam.rect["width"] // 2
    cy = cam.rect["top"] + cam.rect["height"] // 2
    latency = cfg.get("dart_latency_s", -0.031)

    ts, tips = [], []
    t_end = time.perf_counter() + 2.2
    frame0 = None
    while time.perf_counter() < t_end:
        frame, t = cam.grab()
        if frame0 is None:
            frame0 = frame
        p = V.dart_tip(frame, cam)
        if p is not None:
            ts.append(t); tips.append(p)

    fit = V.fit_aim_direct(ts, tips)
    if fit is None:
        print("[Calib] cannot fit the aim")
        return
    aim, difficulty, plat_y, arm_r, rms = fit
    plank = V.platform_y(frame0, cam)
    print(f"[Calib] difficulty={difficulty}  arm={arm_r:.0f}px  rms={rms:.2f}px")
    print(f"[Calib] platform: fit={plat_y:.0f}  plank={plank}")
    if not V.aim_direct_is_trustworthy(arm_r, rms):
        print("[Calib] fit not trustworthy - refusing to spend a dart on it.")
        return

    shot, half = D.plan(time.perf_counter() + 0.5, aim, plat_y, difficulty)
    if shot is None:
        print("[Calib] no plan; throwing at the sweep centre instead")
        return
    t_click = shot.t0 - latency
    print(f"[Calib] planned launch at t0, angle {shot.angle:+.1f}deg, "
          f"assumed latency {latency * 1000:+.0f}ms")

    clicker.click_at(cx, cy, t_click)
    pts = V.track_flight(cam, t_click, duration=1.3)
    print(f"[Calib] tracked {len(pts)} points in flight")

    sol = V.solve_flight(pts, difficulty)
    if sol is None:
        print("[Calib] flight track too short to solve - is the dart visible "
              "between the player and the board?")
        return
    t_launch, theta, plat_meas = sol
    print()
    print("[Calib] MEASURED FROM THE FLIGHT ITSELF:")
    print(f"          launch was {(t_launch - shot.t0) * 1000:+.0f} ms from plan")
    print(f"          release angle {theta:+.1f}deg   (planned {shot.angle:+.1f})")
    print(f"          platform_y    {plat_meas:.0f}    (screen said {plat_y:.0f})")
    true_lat = latency + (t_launch - shot.t0)
    print()
    print(f"          => dart_latency_s should be {true_lat:+.3f} "
          f"({true_lat * 1000:+.0f} ms)")
    print(f"          put that in hoops_config.json as \"dart_latency_s\"")


def press_exit(cam, clicker, dry=False):
    """Click the EXIT button.  Banks the points and starts the cooldown."""
    if dry:
        print("[Darts] dry run: not pressing EXIT.")
        return
    ex = cam.rect["left"] + cam.to_screen(V.EXIT_BUTTON[0])
    ey = cam.rect["top"] + cam.to_screen(V.EXIT_BUTTON[1])
    clicker.click_at(ex, ey, time.perf_counter() + 0.30)


def play(cam, cfg, clicker, dry=False, max_throws=None, settle=1.6,
         streak=0, adapt_latency=False, burn_on_stall=False, max_burns=6,
         should_stop=None):
    """
    Run darts until the streak is won, the darts run out, or max_throws.

    Returns (throws, bullseyes, best_streak, reason), where reason is one of
    "won", "gameover", "stalled", "screen_gone", "stopped" -- endless mode
    needs to tell a finished run from a broken one.
    """
    cx = cam.rect["left"] + cam.rect["width"] // 2
    cy = cam.rect["top"] + cam.rect["height"] // 2

    # Darts has a ~+/-30 ms window, so the starting guess matters.  30 ms was a
    # placeholder inherited from hoops' default; hoops actually *measured* ~89 ms
    # on real hardware, and starting at 30 put the first throws 50-140 ms early
    # -- 40-80 px high, straight out of the red band.
    latency = cfg.get("dart_latency_s")
    if latency is None:
        latency = 0.062    # 62 gave 8/8 on throws 1-8 of one run; raising
                           # it to 72 on the theory that late clicks needed
                           # compensating made 32 of 46 misses land HIGH,
                           # i.e. launching later still.  The sign was wrong.
                           # measured live once the aim was verified;
                           # see DARTS_NOTES.  Comparable to hoops' 89 ms,
                           # unlike the -231 ms the contaminated fits gave.
    best = streak
    # Re-measurements of a poor-but-usable aim fit.  Bounded so a persistently
    # noisy screen cannot spin here forever, and reset after every throw so the
    # allowance is per-throw rather than per-run.
    refits = 0
    # Consecutive looks at an unreadable wind readout.  After a couple, the
    # dart is thrown at a guessed wind rather than surrendered unaimed.
    wind_blind = 0
    # The last aim fit that passed both gates, kept so that a dart being spent
    # to break a stall can still be aimed with it rather than thrown blind.
    #
    # `last_good_plat_y` is SEPARATE from `last_plat_y` on purpose.  The latter
    # is a staleness marker that gets cleared precisely when a capture looks
    # repeated -- which is the main thing that causes these stalls -- so gating
    # the aimed burn on it meant the guard could never be true in the case it
    # was written for.  This one only ever holds the last real reading.
    last_aim = last_difficulty = last_bounds = last_good_plat_y = None

    # Tracks the display's 60 Hz phase from capture timestamps, so throws can
    # be planned on instants the click can actually hit.  See
    # core/frameclock.py for why this is the binding constraint on the streak.
    fclock = FrameClock()
    throws = bullseyes = 0
    lat_hist = []
    stalls = 0      # cycles that produced no throw; bounded so it cannot spin
    burns = 0       # darts spent purely to re-roll wind/platform out of a stall
    last_plat_y = None   # [144] only re-rolls after a throw, so it is reusable
    prev_sig = None      # (plat_y, angle) of the last throw; catches a frozen capture
    # Why the loop ended, so endless mode can decide what to do next.
    reason = "stopped"

    print(f"\n[Darts] {'DRY RUN - no clicks' if dry else 'playing'}   "
          f"latency={latency * 1000:.0f}ms   goal: 9 in a row "
          f"(starting from {streak})")
    print("[Darts] Ctrl+C to stop.\n")

    while max_throws is None or throws < max_throws:
        # Between throws is the only safe place to stop: mid-throw the dart is
        # already in the air on a timer this loop owns, and abandoning it there
        # wastes the dart.  The UI's Stop lands here.
        if should_stop is not None and should_stop():
            reason = "stopped"
            break
        # Bounded so a run that stops being winnable cannot spin.  The increments
        # for this existed for a while without the check ever landing in the
        # file, which is how a finished game produced several hundred identical
        # "cannot fit the aim" lines instead of stopping.
        # Stuck with lives still in hand: spend one deliberately.
        #
        # A stall means no click satisfies the current wind and platform.  Those
        # are re-rolled after EVERY throw, so one wasted dart buys a fresh set
        # of parameters -- while stalling out costs the whole remaining run plus
        # a 15 minute cooldown before anything can be retried.  Four of five
        # runs in one endless session ended this way with lives left, which is
        # a bad trade by any measure.
        #
        # The dart leaves on the click, so any click throws it; no plan needed.
        # Bounded per run, because if the screen itself is broken then burning
        # darts fixes nothing and the hard stop below should still fire.
        if burn_on_stall and stalls >= 4 and burns < max_burns and not dry:
            burns += 1
            # The dart is going to be spent either way, so aim it.  This used
            # to be a bare click at "now + 0.2 s", landing at a random point in
            # a 100-130 deg/s sweep -- a certain loss of the dart AND the
            # streak, for nothing but a re-roll.
            #
            # The last good aim and platform reading are usually only a second
            # old.  Re-planning against them with the safety margin dropped to
            # zero asks a different question -- not "is there a shot that is
            # certainly red" but "which instant is LEAST bad" -- and that is
            # exactly the right question when the alternative is throwing
            # blind.  Any red landing keeps the streak alive; the earlier
            # version could only ever break it.
            aimed = None
            if last_aim is not None and last_good_plat_y is not None:
                aimed, _h = D.plan_windy(
                    time.perf_counter() + 0.60, last_aim, last_good_plat_y,
                    last_difficulty, last_bounds,
                    V.wind_candidates(last_difficulty, 0.0),
                    spread=0.0, min_margin=0.0)
            if aimed is not None:
                print(f"[Darts] stuck for {stalls} cycles with lives left - "
                      f"spending a dart, but AIMED at the best available shot "
                      f"(predicted y={aimed.hit_y:.0f}, margin "
                      f"{aimed.margin:.0f}px) ({burns}/{max_burns} this run).")
                clicker.click_at(cx, cy, aimed.t0 - latency)
            else:
                print(f"[Darts] stuck for {stalls} cycles with lives left - "
                      f"no usable aim to plan with, spending a dart blind "
                      f"({burns}/{max_burns} this run).")
                clicker.click_at(cx, cy, time.perf_counter() + 0.20)
            throws += 1
            streak = 0
            stalls = 0
            last_plat_y = None
            prev_sig = None
            time.sleep(settle + 0.6)
            continue

        if stalls >= 8:
            print("[Darts] 8 cycles without a usable throw - stopping rather "
                  "than spinning.")
            reason = "stalled"
            break

        ts, angs, cents = [], [], []
        t_end = time.perf_counter() + 2.4
        frame0 = None
        while time.perf_counter() < t_end:
            frame, t = cam.grab()
            # Every grab is a free reading of the refresh phase: mss is
            # vsync-locked (R=0.984, phase sd 0.47 ms over 400 samples), so
            # these timestamps say where the frame boundary is without costing
            # a single extra capture.
            fclock.observe(t)
            if frame0 is None:
                frame0 = frame
            r = V.dart_angle(frame, cam)
            if r is not None:
                ts.append(t); angs.append(r[0]); cents.append((r[1], r[2]))

        # The run ending is the normal way for this loop to finish, and it is
        # not a detector failure -- so check for it explicitly rather than
        # letting the aim fit fail over and over against a frozen screen.
        if minigame.classify(frame0, cam) != minigame.DARTS:
            print("[Darts] the darts screen is gone (run over?) - stopping.")
            reason = "screen_gone"
            break

        # Out of lives: press EXIT.  Points are only banked by that button and
        # the cooldown does not start until it is pressed, so sitting on the
        # dead screen wastes real time.  Previously the run ended with the aim
        # fit failing over and over against a screen with no dart on it.
        # Confirm on a second frame before spending the run.  The band is clean
        # (819 px on a real game-over against 0 during play), but one frame in
        # 719 of a recording hit 64 px from some mid-run transient, and pressing
        # EXIT on a false positive throws the run away.  A real game over lasts
        # until it is dismissed, so waiting one frame costs nothing.
        if V.game_over(frame0, cam) and V.game_over(cam.grab()[0], cam):
            print(f"\n[Darts] GAME OVER - no lives left.")
            if dry:
                print("[Darts] dry run: not pressing EXIT.")
            else:
                ex = (cam.rect["left"] + cam.to_screen(V.EXIT_BUTTON[0]),
                      cam.rect["top"] + cam.to_screen(V.EXIT_BUTTON[1]))
                clicker.click_at(ex[0], ex[1], time.perf_counter() + 0.30)
                print("[Darts] pressed EXIT to bank the points and start the "
                      "cooldown.")
            reason = "gameover"
            break

        bounds = V.read_board(frame0, cam) or D.DEFAULT_BOUNDS

        # Difficulty is MEASURED, never counted.  `[160]` belongs to the game's
        # minigame session and survives this process restarting, so a local
        # counter starting at 0 is wrong the moment anything has been missed --
        # and it sets the aim's rate as well as its amplitude, so being wrong
        # about it makes every angle wrong.
        fit = V.fit_aim_angle(ts, angs, cents)
        if fit is None:
            print("[Darts] cannot fit the aim - a throw may still be in "
                  "flight.  waiting")
            time.sleep(random.uniform(0.75, 1.25))
            stalls += 1
            continue

        aim, difficulty, amp, offset, rms, fit_plat_y = fit
        if not V.aim_angle_is_trustworthy(difficulty, amp, offset, rms):
            print(f"[Darts] aim fit not trustworthy (d={difficulty} "
                  f"amp={amp:.1f} vs {D.aim_params(difficulty)[0]:.1f}, "
                  f"offset={offset:.1f} vs -20, rms={rms:.2f}deg) - "
                  f"refusing to throw")
            time.sleep(random.uniform(0.75, 1.25))
            stalls += 1
            continue

        # A fit can clear `aim_angle_is_trustworthy` and still be poor.  That
        # gate rejects nonsense (rms < 2.0 deg); a normal fit lands near
        # 0.35 deg, and the throw that broke the best run so far was taken on
        # 0.83 -- comfortably "trustworthy", visibly worse than usual, and it
        # missed.
        #
        # The costs are wildly asymmetric.  Re-measuring costs about a second,
        # and the aim keeps sweeping meanwhile.  Throwing on a mediocre fit
        # costs a dart, resets the streak, AND raises `[160]` permanently,
        # which widens and speeds the sweep for every throw afterwards.  So
        # when the streak is still winnable, spend the second.
        #
        # Only while it IS winnable: past d~15 the red band is narrower than
        # one frame of sweep, nine in a row is gone, and stalling for a better
        # fit would just burn the run's remaining darts on the clock.  There,
        # take the fit that passed and keep scoring.
        if (rms > GOOD_FIT_RMS and difficulty <= STREAK_BAND_D
                and refits < MAX_REFITS):
            refits += 1
            print(f"[Darts] aim fit is usable but poor (rms={rms:.2f}deg vs "
                  f"{GOOD_FIT_RMS:.2f} wanted, d={difficulty}, streak={streak})"
                  f" - re-measuring rather than spending a dart "
                  f"({refits}/{MAX_REFITS})")
            time.sleep(random.uniform(0.30, 0.55))
            continue

        # Remember the last fit that cleared both gates.  A stall-breaking dart
        # is thrown against this rather than blind, and it is at most a couple
        # of seconds old by then.
        last_aim, last_difficulty, last_bounds = aim, difficulty, bounds

        # The fit's own pivot solve is preferred: it comes from the same
        # measurement that is already checked against two source constants,
        # whereas reading the plank off the screen had a fixed-x window that
        # silently failed once the platform slid with difficulty.
        plat_y = fit_plat_y if fit_plat_y is not None else V.platform_y(frame0, cam)
        if plat_y is None:
            # `[144]` is only re-rolled when a throw completes, so between
            # throws the last good reading is still valid.  The plank detector
            # drops out intermittently, and treating each dropout as a stall
            # spent three of eight allowed cycles before the first throw --
            # enough to abort a nine-streak on detector noise alone.
            if last_plat_y is not None:
                plat_y = last_plat_y
                print(f"[Darts] plank not visible; reusing {plat_y:.0f} "
                      f"(unchanged since the last throw)")
            else:
                print("[Darts] cannot see the platform and have no previous "
                      "reading - refusing to throw")
                time.sleep(random.uniform(0.45, 0.75))
                stalls += 1
                continue
        elif last_plat_y is not None and abs(plat_y - last_plat_y) < 0.5:
            # `[144]` is re-rolled after every throw, so an identical reading
            # twice running means the estimate is stuck, not that the platform
            # held still.  Five repeats of plat_y=444 produced five throws at a
            # -40 deg angle -- far outside the -8..-24 of every good throw --
            # and every one of them missed.
            print(f"        platform reads {plat_y:.0f} again, unchanged since "
                  f"the last throw - stale, re-observing")
            last_plat_y = None
            time.sleep(random.uniform(0.45, 0.75))
            stalls += 1
            continue
        else:
            last_plat_y = plat_y

        # ── Plan ──────────────────────────────────────────────────────────────
        wind_dir = V.read_wind_direction(frame0, cam, difficulty)
        wind_mph = V.read_wind_mph(frame0, cam)
        # Prefer the exact magnitude off the HUD; the difficulty-derived range
        # is a 40% band and was worth ~12 px of landing spread on its own.
        dir_unknown = False
        blind_wind = False
        winds = V.wind_candidates_exact(difficulty, wind_dir, wind_mph)
        exact = winds is not None
        if winds is None:
            winds = V.wind_candidates(difficulty, wind_dir)
        if wind_dir is None and wind_mph is not None:
            # The number is on screen but its arrow was not resolved.  Wind
            # exists; only its heading is unknown, so fall back to the
            # difficulty range rather than assuming calm.
            winds = V.wind_candidates_unknown_dir(difficulty, mph=wind_mph)
            exact = False
            dir_unknown = True
        elif wind_dir is None and V.wind_readout_present(frame0, cam):
            # Wind-coloured pixels are in the HUD but neither the number nor the
            # arrow parsed.  Absence of a *reading* is not absence of wind: a
            # real 8 mph wind once read as "none", the planner assumed calm, and
            # the dart missed by 25-80 px.
            #
            # Worth re-looking a couple of times, since the readout is often
            # mid-redraw.  But refusing forever is what makes this expensive:
            # the run stalls, and the stall handler eventually spends a dart on
            # an UNAIMED click just to re-roll the board.  That throws away a
            # dart AND the streak for nothing.
            #
            # The aim fit and platform height are both good here -- only the
            # wind is unknown.  So once re-looking has failed, guess the wind
            # from the difficulty and throw a properly AIMED dart at it.  It is
            # the same dart either way; aimed, it can still land red.
            wind_blind += 1
            if wind_blind <= 2:
                print(f"[Darts] a wind readout is on screen but unreadable "
                      f"(see wind_unknown/) - looking again "
                      f"({wind_blind}/2)")
                time.sleep(random.uniform(0.75, 1.25))
                stalls += 1
                continue
            print(f"[Darts] wind still unreadable - aiming with the "
                  f"difficulty-derived guess rather than wasting the dart")
            winds = V.wind_candidates(difficulty, 0.0)
            exact = False
            dir_unknown = True
            blind_wind = True
        elif wind_dir is None:
            # Genuinely no readout at all: the game draws nothing when the wind
            # is zero.  Do NOT infer wind from the difficulty here:
            # recovering `[160]` is only good to about +/-1 at the low end (the
            # aim amplitude is 38.0 at d=0 against 38.5 at d=1, a gap smaller
            # than the fit's own residual), and a spurious d=1 after a bullseye
            # made this demand an arrow that could not exist, deadlocking the
            # run.  The arrow's absence is a direct observation and outranks it.
            winds = [(0.0, 0.0)]
            exact = True

        plat_y_eff = plat_y
        last_good_plat_y = plat_y      # survives the staleness clear above

        # The click goes out at t0 - latency, so the reachable LAUNCH instants
        # are the refresh grid shifted by the latency.  Planning on that grid
        # is the whole point: it replaces "a time we cannot hit, plus a frame
        # of uncertainty" with "a time we can hit, plus its jitter".
        align = None
        spread_s = D.LAUNCH_SPREAD_S
        if fclock.locked:
            ph = fclock.phase()
            align = (FRAME_S, (ph + latency) % FRAME_S)
            # What is left is the phase estimate's own scatter, measured at
            # 0.47 ms sd.  Three sigma of that, not a whole frame.
            spread_s = 0.0015

        shot, half = D.plan_windy(time.perf_counter() + 0.80, aim, plat_y_eff,
                                  difficulty, bounds, winds,
                                  spread=spread_s, align=align)
        frame_robust = shot is not None
        blind_dir = False
        if shot is None and dir_unknown:
            # With the arrow unread the direction spans +/-75 deg, and NO click
            # survives that at any difficulty -- verified 0/3 platform heights
            # at d=5..30.  That is the truthful answer, but stalling every such
            # throw would end the run, so fall back to the midpoint direction
            # and say so.  This is what the code did silently before; the only
            # change is that it is now labelled and is the LAST resort rather
            # than the first assumption.
            shot, half = D.plan_windy(time.perf_counter() + 0.80, aim,
                                      plat_y_eff, difficulty, bounds,
                                      V.wind_candidates(difficulty, 0.0),
                                      spread=spread_s, align=align)
            blind_dir = shot is not None
        if shot is None:
            # No click survives the whole frame window.  Past roughly d=35 that
            # is simply true -- the red band is narrower than one frame's worth
            # of aim sweep -- so refusing outright would end the run rather than
            # keep scoring.  The streak is already unreachable by then, so fall
            # back to the best single-instant plan and take the coin flip.
            shot, half = D.plan_windy(time.perf_counter() + 0.80, aim,
                                      plat_y_eff, difficulty, bounds, winds,
                                      spread=0.0, align=align)
        if shot is None:
            print(f"[Darts] no throw stays red across the whole wind range "
                  f"(d={difficulty}, {len(winds)} candidates) - re-observing")
            time.sleep(random.uniform(0.38, 0.62))
            stalls += 1
            continue

        if blind_wind:
            # Never let a guessed wind read like a measured one in the log.
            # The distinction is the difference between a throw that should
            # have landed and one that was always a long shot.
            wind_txt = "GUESSED from difficulty (readout unreadable)"
        elif wind_dir is None and dir_unknown:
            # Do NOT print "none" here.  The magnitude was read; it is the arrow
            # that was not, and calling that "none" hid the single largest
            # source of error in the last three runs.
            wind_txt = f"{wind_mph}mph@?deg(dir unread)"
        elif wind_dir is None:
            wind_txt = "none"
        elif exact:
            wind_txt = f"{wind_mph}mph@{wind_dir:+.0f}deg"
        else:
            wind_txt = f"{wind_dir:+.0f}deg(mag unread)"

        # The fallback's window is measured WITHOUT the frame-spread
        # requirement, so it is not comparable to a frame-robust one and reads
        # deceptively large -- a fallback throw printed +/-16 ms next to a real
        # +/-10 ms one.  Say which kind it is.
        # Say whether this throw was planned on the refresh grid.  If the send
        # times below stop being spread across a frame, this is why.
        win_txt = ("[framelock] " if align is not None else "[unlocked] ")
        win_txt += (f"window=+/-{half * 1000:.0f}ms" if frame_robust else
                   f"window=+/-{half * 1000:.0f}ms single-instant"
                   f"  [COIN FLIP: no click survives a frame]")
        if blind_dir:
            win_txt += "  [BLIND: arrow unread, assuming horizontal wind]"

        print(f"[Throw {throws + 1}] streak={streak}/9  d={difficulty} "
              f"(amp {amp:.1f} off {offset:.1f} rms {rms:.2f}deg)  "
              f"wind={wind_txt}  "
              f"plat_y={plat_y:.0f}  "
              f"angle={shot.angle:+.1f}deg  ->  y={shot.hit_y:.0f} "
              f"(red {bounds[2]:.0f}..{bounds[3]:.0f}, margin {shot.margin:.0f}px)"
              f"  {win_txt}")

        if half < 0.006:
            print("        window under 6ms - too tight to hit, re-observing")
            time.sleep(random.uniform(0.30, 0.50))
            continue

        # Refuse a throw planned from vision identical to the last throw's.
        #
        # The platform re-rolls after every throw and the aim never stops
        # sweeping, so two consecutive plans agreeing to 0.1 px AND 0.1 deg is
        # not possible from live pixels -- it means the capture is handing back
        # a stale frame.  Seen live: throws 37-43 all planned plat_y=356
        # angle=-14.7, landed +76/+83/+93/+76/+43 px out, and the run then
        # deadlocked on a degenerate fit (amp=0.0, rms=0.00 -- every tip sample
        # the same pixel).  Five darts spent on a frozen screen.
        #
        # The existing stale-platform check cannot catch this: it compares
        # against last_plat_y, which is deliberately cleared after every throw
        # because the game really does re-roll [144] then.
        # Key on plat_y ALONE, not (plat_y, angle).  The game re-rolls [144]
        # after every throw, so a repeat is already impossible from live pixels
        # -- whereas including the angle weakened the test to uselessness: a
        # frozen capture still produced angles drifting -18.6/-17.8/-17.7 from
        # fit noise, so the tuple differed and seven throws went out at a stale
        # plat_y=385, all seven missing the board entirely.
        sig = round(plat_y, 1)
        if sig == prev_sig:
            print(f"        vision identical to the last throw "
                  f"(plat_y={plat_y:.0f}) - [144] re-rolls every throw, so "
                  f"the capture is stale, not the game.  Re-observing.")
            last_plat_y = None
            time.sleep(random.uniform(0.45, 0.75))
            stalls += 1
            continue
        prev_sig = sig

        # Aim earlier by the measured SendInput cost as well as the latency.
        #
        # These are two different delays and only one of them was ever being
        # accounted for.  `latency` is the game's own lag between receiving the
        # click and launching the dart.  The send cost is the time SendInput
        # spends getting the event to the window at all -- 1-17 ms live against
        # 0.09 ms offline, which is why it stayed invisible for so long.
        #
        # The send cost is NOT overhead that can be subtracted away, and a
        # median of it was the wrong model.  It is frame quantisation: the game
        # polls input once per rendered frame, so the dart leaves anywhere in
        # [t0, t0 + 16.7 ms] after the event is posted, flat across the range.
        # A warm-up SendInput changed nothing (mean 8.9 -> 10.2 ms), because
        # there is nothing to warm -- the wait is for the game, not the API.
        #
        # So the planner now demands a click that scores across that whole
        # window (dartsim.LAUNCH_SPREAD_S), exactly as it already does across
        # the wind range, and `latency` is once again just the game's own lag.
        t_click = shot.t0 - latency
        if align is not None:
            # plan_windy returns the WORST-CASE launch across the spread, so
            # shot.t0 sits a little past the grid instant it was planned from.
            # Snapping recovers that instant: the offset is under 2 ms against
            # a half-frame of 8.3, so this can only ever move the click back
            # onto the point the plan was actually built on.
            snapped = fclock.snap(t_click)
            if snapped is not None:
                t_click = snapped
        if dry:
            throws += 1          # so --shots can end a dry run
            print(f"        would click in {t_click - time.perf_counter():+.3f}s\n")
            time.sleep(max(0.0, shot.t0 - time.perf_counter()) + 0.3)
            continue

        # ── Throw ─────────────────────────────────────────────────────────────
        # Never fire late.  The reported send error was always POSITIVE, 1-17 ms,
        # never negative -- the signature of t_click already being in the past
        # when the click is issued, not of timer jitter (sleep_until measures
        # 0.00 ms even after a heavy cv2 workload).  At d>=20 the whole window
        # is +/-10-16 ms, so a 17 ms overrun is the miss on its own.
        late = time.perf_counter() - t_click
        if late > 0.002:
            print(f"        plan expired {late * 1000:.0f}ms before the click "
                  f"could be issued - re-planning rather than firing late")
            stalls += 1
            continue

        before, _ = cam.grab()
        # How much runway sleep_until actually had.  This separates the two
        # remaining explanations for the +11.7 ms mean send error, which cannot
        # be told apart from the error alone: a large lead that still fired late
        # means the spin was preempted, a near-zero lead means the planner ate
        # the runway and the wait never had a chance.
        lead = t_click - time.perf_counter()
        sent = clicker.click_at(cx, cy, t_click)
        throws += 1
        print(f"        clicked ({(sent - t_click) * 1000:+.2f}ms off plan, "
              f"had {lead * 1000:.0f}ms lead"
              f" | wait {getattr(clicker, 'last_wait_err', 0.0) * 1000:+.2f}ms"
              f" + send {getattr(clicker, 'last_send_ms', 0.0):.2f}ms)")

        sleep_until(shot.t0 + shot.steps * D.STEP_S + 0.35)
        after, _ = cam.grab()

        actual_y = landed_dart_y(before, after, cam)
        if actual_y is None:
            # Nothing new on the board means the dart never stuck: it cleared
            # the top or fell short.  That is a real miss, so the streak is gone
            # whether or not the landing could be measured -- and the game still
            # re-rolls [144], so the cached platform height is now stale.  Not
            # clearing it here froze plat_y at 289 for eleven consecutive
            # throws, every one of them aimed at a platform that had moved.
            last_plat_y = None
            refits = 0            # a dart was spent, so the allowance renews
            streak = 0
            print("        no dart on the board - it missed entirely "
                  "(streak reset)")
            time.sleep(settle)
            continue

        last_plat_y = None        # the game re-rolls [144] after every throw
        refits = 0                # the allowance is per throw, not per run
        wind_blind = 0            # and the wind is re-rolled too, so re-look
        band = D.band_of(actual_y, bounds)
        hit = (band == D.BULLSEYE_BAND)
        if hit:
            bullseyes += 1
            streak += 1
            best = max(best, streak)
        else:
            streak = 0               # [160] rises too, but it is measured next
                                     # cycle rather than tracked here

        print(f"        landed y={actual_y:.0f} (predicted {shot.hit_y:.0f}, "
              f"off {actual_y - shot.hit_y:+.0f}px)  band={band}  "
              f"{'BULLSEYE' if hit else 'MISS'}  streak={streak}")

        # ── Calibrate latency from the landing error ──────────────────────────
        # Latency is only inferable from a landing when nothing else moves the
        # dart.  Above difficulty 0 the game applies a per-throw random wind,
        # worth 30-70 px of drift at d=9-14 -- so a landing error there is
        # mostly wind, and feeding it to the latency estimator made it swing
        # 62 -> 158 -> 2 -> 114 ms across consecutive throws, chasing noise.
        # Latency is only inferable from a landing when nothing else could have
        # moved the dart.  That is true at difficulty 0 (wind is exactly zero)
        # and now also whenever the magnitude was read off the HUD -- the ceil
        # leaves at most one unit of doubt, against the 40% band the difficulty
        # alone allows.  Anything else and the error is mostly wind, which is
        # what made this swing 62 -> 158 -> 2 -> 114 ms when it was fed
        # everything.
        can_attribute = (difficulty == 0) or exact
        dt = (timing_error_from_landing(shot, aim, plat_y_eff, actual_y,
                                        difficulty, bounds)
              if can_attribute else None)

        # NO launch-y bias loop here.  One was tried and had to be removed.
        #
        # The reasoning behind it was that shifting the launch point vertically
        # translates the whole trajectory, so landing height moves 1:1 with the
        # bias in a fixed direction and the feedback sign is safe.  That is true
        # of a FIXED shot and false of this bot, because the planner re-solves
        # for a new click and a new launch angle every throw.  Feeding it a
        # doctored plat_y does not translate the shot, it selects a different
        # shot, while the real dart still leaves the real platform.  The loop
        # was positive feedback: it saturated at its +60 px cap within three
        # throws while the landing error grew -24 -> -40 -> -59 -> -109 px.
        #
        # The real cause was plat_x, not plat_y.  See below.
        if not can_attribute and abs(actual_y - shot.hit_y) > 15:
            print(f"        (not adjusting latency: at d={difficulty} the "
                  f"wind's MAGNITUDE is still unknown within "
                  f"{0.6*D.wind(difficulty):.1f}-{D.wind(difficulty):.1f}, so a "
                  f"landing error is not attributable to timing)")
        # Adaptation is OFF by default.  Damping it (median of 5, half-step,
        # +/-20 ms cap) was not enough: across one run it still ranged 4-84 ms,
        # orbiting its own median without settling, on a window of only
        # +/-12-20 ms.  A per-throw landing carries more noise than the constant
        # it is trying to estimate, so the loop injects error rather than
        # removing it.  A fixed, once-measured value beats a wandering one.
        if adapt_latency and dt is not None and rms < 1.0:
            # Damped, and only from throws whose aim fit was clean.  Stepping
            # hard on every landing sent this 74 -> 34 -> 117 -> 9 -> 206 ms
            # across one run: each throw carries its own noise, and a single
            # sample is not evidence about a constant.  Averaging several and
            # moving a fraction of the way converges instead of ringing.
            lat_hist.append(dt)
            if len(lat_hist) > 5:
                lat_hist.pop(0)
            if len(lat_hist) >= 3:
                med = sorted(lat_hist)[len(lat_hist) // 2]
                if abs(med) > 0.004:
                    step = max(-0.020, min(0.020, med * 0.5))
                    latency = max(-0.150, min(0.250, latency + step))
                    cfg["dart_latency_s"] = latency
                    print(f"        -> dart latency now {latency * 1000:.0f}ms "
                          f"(median of {len(lat_hist)}, half-step)")

        if streak >= 9:
            print(f"\n[Darts] NINE IN A ROW - trophy earned after {throws} "
                  f"throws.")
            reason = "won"
            break

        print()
        time.sleep(settle)

    print(f"[Darts] {bullseyes}/{throws} bullseyes, best streak {best}")
    return throws, bullseyes, best, reason


def enter_game(cam, clicker, entry=None, dry=False, poll=5.0, timeout=3600.0):
    """
    From the world map: locate the entry, wait out the cooldown, click in.

    Returns (ok, entry).  `entry` is returned so the caller can keep the
    learned position for later cycles.

    This is also how an endless session STARTS.  The first version only had
    this logic inside the run loop, so `--endless` still required the minigame
    to be open already -- it could exit to the map but not enter from it, and
    launching from the map died in wait_for_game with no minigame on screen.
    """
    import overworld

    frame, _ = cam.grab()
    spot = entry or overworld.find_entry(frame, cam)
    if spot is None:
        print("[Endless] cannot locate the minigame entry on the world map, "
              "and I will not click a guessed position - a stray click can "
              "move the character and break every later cycle.\n"
              "          Re-run with --dart-at X,Y (game coords) to point "
              "at it.")
        return False, entry
    if entry is None:
        print(f"[Endless] entry detected at ({spot[0]:.0f}, {spot[1]:.0f})"
              f" - reusing it for the rest of the session.")
        entry = spot

    waited = 0.0
    while overworld.cooldown_running(frame, cam, entry):
        if waited == 0.0:
            print("[Endless] cooldown running - waiting.")
        time.sleep(poll)
        waited += poll
        if waited > timeout:
            print("[Endless] still on cooldown after an hour - giving up.")
            return False, entry
        frame, _ = cam.grab()
    if waited:
        print(f"[Endless] cooldown cleared after {waited / 60:.1f} min.")

    if dry:
        print("[Endless] dry run: not clicking the entry.")
        return False, entry

    clicker.click_at(cam.rect["left"] + cam.to_screen(entry[0]),
                     cam.rect["top"] + cam.to_screen(entry[1]),
                     time.perf_counter() + 0.30)
    print("[Endless] clicked the entry - waiting for the darts screen.")

    t_end = time.perf_counter() + 20.0
    while time.perf_counter() < t_end:
        frame, _ = cam.grab()
        if minigame.classify(frame, cam) == minigame.DARTS:
            print("[Endless] darts is up.")
            return True, entry
        time.sleep(random.uniform(0.38, 0.62))
    print("[Endless] darts never appeared after clicking - stopping.")
    return False, entry


def play_endless(cam, cfg, clicker, dry=False, entry=None, settle=1.6,
                 poll=5.0, max_runs=None, already_playing=True,
                 should_stop=None):
    """
    Play run after run: play -> EXIT -> wait out the cooldown -> click back in.

    Design notes, because two of these are deliberate and look like omissions:

      * A STALLED run is exited, not fought.  The stall guard fires when the
        vision has stopped making sense (a frozen capture, a screen with no
        dart on it), and clicking blind there spends lives to no purpose.
        EXIT still banks whatever the run scored and starts the cooldown, so
        recycling is strictly better than guessing.  Guessing already happens
        where it is useful -- the single-instant fallback throws when no click
        survives a frame, rather than refusing.

      * Nothing is ever clicked at a position that was not either detected or
        supplied.  `find_entry` is unvalidated (see overworld.py), and a stray
        click on the world map can move the character, which would move the
        entry and break every later cycle.  If the entry cannot be located the
        loop stops and says so.
    """
    runs = 0
    totals = [0, 0, 0]          # throws, bullseyes, best streak

    # Started from the world map rather than inside a run: enter first.
    if not already_playing:
        ok, entry = enter_game(cam, clicker, entry, dry=dry, poll=poll)
        if not ok:
            return tuple(totals) + (runs,)

    while max_runs is None or runs < max_runs:
        if should_stop is not None and should_stop():
            break
        runs += 1
        print(f"\n{'=' * 62}\n[Endless] run {runs}\n{'=' * 62}")
        throws, bulls, best, reason = play(cam, cfg, clicker, dry=dry,
                                           settle=settle, burn_on_stall=True,
                                           should_stop=should_stop)
        totals[0] += throws
        totals[1] += bulls
        totals[2] = max(totals[2], best)

        if reason == "won":
            print("[Endless] trophy earned - stopping, that was the goal.")
            break

        # Learn where to click back in while the minigame is still on screen
        # being exited; the entry only becomes visible after we leave.
        if reason in ("stalled", "screen_gone"):
            print(f"[Endless] run ended as '{reason}' - exiting to recycle.")
            press_exit(cam, clicker, dry)

        # Stop BEFORE re-entering when the run budget is spent.  Re-entry lives
        # at the end of the loop body, so without this the last iteration sat
        # out the full cooldown and then started a run nobody asked for --
        # 15 wasted minutes and a fresh game left open on screen.
        if max_runs is not None and runs >= max_runs:
            print(f"[Endless] {runs} run(s) done - not re-entering.")
            break

        time.sleep(random.uniform(1.50, 2.50))
        ok, entry = enter_game(cam, clicker, entry, dry=dry, poll=poll)
        if not ok:
            break

    print(f"\n[Endless] {runs} run(s): {totals[1]}/{totals[0]} bullseyes, "
          f"best streak {totals[2]}")
    return tuple(totals) + (runs,)
