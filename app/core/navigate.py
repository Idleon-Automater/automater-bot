"""
Getting the character to where a task can run.

A queue of tasks is only useful if the character can get from one to the next,
so each task says where it lives and this module drives the map screen to take
it there.

THE ROUTE (the shape of all of them)
------------------------------------
    1. click MAP in the bottom bar          HUD, fixed screen position
    2. double-click the world's tab         HUD, and lands in that world's TOWN
    3. click or hover the entry object      world object, camera-relative
    4. click the task's icon in any popup   relative to the entry object

Step 2 is the game's own shortcut and does the whole journey by itself.  An
earlier version went through the map view and the FREE TELEPORT button, which
worked but had more to get wrong -- and needed a marker picked out of the
twenty-five drawn on a world map.

Steps 1-2 are HUD: they sit at the same screen pixels regardless of what the
character is doing, so they are calibrated once and reused everywhere.  Steps
4-5 are not.  As `overworld.py` puts it, a world object's position "moves with
the camera ... only stable while the character stands still" -- so those are
LOCATED each run by template match, never replayed from stored coordinates.
Getting that wrong is expensive rather than merely useless: a stray click on
the world map walks the character, which moves every later entry icon and
breaks the rest of the queue.

WHY THERE IS NO "WHERE AM I" READER
-----------------------------------
The map screen prints `Current Map: <name>`, and reading it was the obvious way
to decide whether a hop was needed.  It is not necessary: every task already
implements can_run(), which answers the same question more directly and with no
text recognition at all.  So the rule is simply

    try can_run() -> already there, skip the journey
    else          -> travel, then can_run() again to confirm arrival

which also means a failed teleport is caught immediately, at the one moment it
can still be reported instead of being clicked over.

TELEPORT BUDGET
---------------
Town teleports are free; other maps cost one of a daily allowance.  Routes
therefore prefer a town hop, and `Location.costs_teleport` marks the ones that
do not, so the UI can say what a queue will spend before it spends it.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from core.task import Blocked


def _dump_frame(frame, why):
    """
    Write the frame a failed check was looking at, next to the run history.

    Every screen check in this file answers yes or no, and when the answer is
    wrong the message reads the same whichever way it went wrong.  One picture
    settles it.
    """
    try:
        import cv2
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "IdleonAutomator", "failures")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{why}_{int(time.time())}.png")
        cv2.imwrite(path, frame)
        return path
    except Exception:
        return "no screenshot"


def _focus_game(settle=0.25):
    try:
        import core.window as _w
        _w.focus(settle=settle)
    except Exception:
        pass


# Measured off a 960x540 canvas -- the size `window.lock_window` parks the game
# at -- by finding the widgets rather than reading them off a screenshot by eye:
# the world buttons sit on a 32 px pitch with borders at y=41,73,...,233, and
# MAP is the sixth plate in the bottom bar, independently confirmed by its
# parchment icon at x 758-796.
#
# These ship.  They describe the game's own layout at a fixed canvas size, not
# anything about the machine that measured them, so a release works out of the
# box and calibration is only needed when a user's window differs.
DEFAULT_CALIBRATION = {
    "_measured_from": "960x540 canvas",
    "map_button": [778, 506],
    # World 7 at the top down to World 1 at the bottom, as they appear.
    "world_buttons": {"7": [39, 57], "6": [39, 89], "5": [39, 121],
                      "4": [39, 153], "3": [39, 185], "2": [39, 217],
                      "1": [39, 249]},
    # Interior of the map view, used to bound icon searches.  Deliberately a
    # little inside the frame so the border cannot match anything.
    "map_panel": [82, 34, 872, 452],
    "teleport_count_region": [88, 415, 276, 450],
    "free_teleport_button": [317, 432],
}


# A user override lives with their settings rather than beside the code, so a
# non-standard window size is their business and never leaks into a release.
def calibration_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "IdleonAutomator")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "map_calibration.json")


@dataclass
class Location:
    """
    Where a task lives, and how to get there.

    Most tasks are reached through their world's TOWN rather than through the
    specific map they sit on.  The town is a free teleport and the task's
    entrance is usually right there, so the route is shorter, costs nothing,
    and skips the fiddliest step -- picking one marker out of twenty-five on a
    map view.  `map_icon` exists for the tasks whose town shortcut is not
    unlocked, which need the long way round.
    """

    world: int                       # 1..7, which button in the left list
    map_name: str = ""               # human-readable, for the log
    via_town: bool = True            # take the free town teleport
    # Template of the entry object in the world (the speech bubble, the dart
    # sprite).  Found each run rather than stored as coordinates, because it
    # moves with the camera.
    entry_icon: Optional[str] = None
    # Template of the task's icon inside the popup the entry opens.
    popup_icon: Optional[str] = None
    # Optional `f(frame, (x, y)) -> bool`, asked once the entrance has been
    # located: is the thing showing a cooldown instead of an invitation?
    # Supplied by the task rather than known here, because what a cooldown
    # looks like is the task's business -- core has no idea what a minigame is.
    cooldown_check: Optional[object] = None
    # Where to click, RELATIVE to the middle of the entry template.
    #
    # Normally zero: the template is the thing you click.  It exists for
    # entrances whose clickable part cannot be the template -- the Equinox
    # mirror animates, so only its static stone crown makes a usable template,
    # while the part that responds to a click is the glass below it.
    entry_click_offset: tuple = (0, 0)
    # Only for the long way round: the destination marker on the map view.
    map_icon: Optional[str] = None

    @property
    def costs_teleport(self) -> bool:
        return not self.via_town


def load_calibration():
    """The user's override if they have one, otherwise the shipped defaults."""
    try:
        with open(calibration_path()) as f:
            user = json.load(f)
        merged = dict(DEFAULT_CALIBRATION)
        merged.update(user)
        return merged
    except Exception:
        return dict(DEFAULT_CALIBRATION)


def find_icon(frame, template, region=None, min_score=0.80):
    """
    Locate a world object by template match, returning (x, y, score) or None.

    This is how every camera-relative click target is found.  The bubble scores
    1.000 against its own frame with the next-best match at 0.330, so the
    threshold is not doing delicate work -- but it is the difference between
    clicking the entry and clicking open water, and a click on open water walks
    the character, so it stays.
    """
    import cv2
    x0, y0, x1, y1 = region or (0, 0, frame.shape[1], frame.shape[0])
    sub = frame[y0:y1, x0:x1]
    if sub.size == 0 or template.shape[0] > sub.shape[0] \
            or template.shape[1] > sub.shape[1]:
        return None
    res = cv2.matchTemplate(sub, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < min_score:
        return None
    h, w = template.shape[:2]
    return (x0 + loc[0] + w // 2, y0 + loc[1] + h // 2, score)


def save_calibration(data):
    with open(calibration_path(), "w") as f:
        json.dump(data, f, indent=2)


class Navigator:
    """
    Drives the map screen.  Needs calibration before it will click anything.

    Refusing to act without calibration is deliberate.  Guessed HUD coordinates
    would still click *somewhere*, and on the world map somewhere means walking
    the character, which is the one failure that corrupts every later task in
    the queue rather than just this one.
    """

    def __init__(self, rect, clicker, calibration=None):
        self.rect = rect
        self.clicker = clicker
        self.cal = calibration or load_calibration()

    def require_calibration(self):
        if not self.cal:
            raise Blocked(
                "the map screen has not been calibrated yet -- run "
                "Calibrate in the UI once, with the game open")
        missing = [k for k in ("map_button", "world_buttons")
                   if k not in self.cal]
        if missing:
            raise Blocked(f"map calibration is incomplete: missing {missing}")

    def _click_game(self, x, y, settle=0.45):
        """Click a point given in game canvas coordinates."""
        from core.capture import to_screen
        sx, sy = to_screen(self.rect, x, y)
        self.clicker.click(sx, sy)
        time.sleep(settle)

    MAP_ANCHOR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "nav", "map_open.png")

    def map_is_open(self, frame=None):
        """
        Whether the map screen is showing, by the "Teleports Remaining:"
        plaque in the bottom-left of the map panel.

        That plaque is on every map screen whichever world is selected, and on
        no other screen, so one template match settles it.  Scores on the real
        frames: 1.000 on all three map screens, 0.412 or below on every town,
        minigame map, refinery and sushi screen.

        THREE colour-based versions came before it and all three shipped
        broken, because saturation cannot tell "the map" from "anything
        colourful".  Evenly spaced borders failed on the lettering inside the
        tabs.  Borders at calibrated positions failed because the live tabs sit
        a few pixels off.  Counting saturated tabs failed on the sushi board --
        a wall of bright tiles in the same column -- which returned True with
        no map on screen at all, and the marker search then hunted a teleport
        marker across a plate of sushi.

        Both directions of error cost a run.  A false negative is worse: the
        map button toggles, so open_map clicks a screen that is already open
        and closes it.
        """
        import cv2
        frame = self._grab() if frame is None else frame
        tpl = cv2.imread(self.MAP_ANCHOR)
        if tpl is None:
            raise Blocked("the map anchor artwork is missing from this build "
                          f"({self.MAP_ANCHOR})")
        h, w = frame.shape[:2]
        # The plaque never moves, so only its corner of the screen is searched:
        # a full-frame search is slower and invites a stray match elsewhere.
        region = frame[int(h * 0.70):int(h * 0.89),
                       int(w * 0.04):int(w * 0.42)]
        if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
            return False
        score = float(cv2.matchTemplate(region, tpl,
                                        cv2.TM_CCOEFF_NORMED).max())
        self.last_map_score = score
        return score >= 0.70

    def open_map(self):
        """
        Open the map screen, and check that it opened.

        Clicking and hoping was the first version, and when the click did not
        land the run carried on regardless: it "double-clicked World 3" on a
        screen with no world tabs, reported "arrived in town", and only failed
        three steps later looking for an entrance in the wrong place.  Every
        step now proves itself before the next one is allowed to depend on it.
        """
        self.require_calibration()
        x, y = self.cal["map_button"]
        for attempt in range(3):
            if self.map_is_open():
                return
            # Focus first: the very first click on an unfocused game window is
            # spent activating it, so without this the map button is pressed
            # once and nothing happens.
            _focus_game()
            self._click_game(x, y, settle=1.0)
        if not self.map_is_open():
            # Save what it judged, because "did not open" and "opened but was
            # not recognised" look identical from here and need opposite fixes.
            shot = _dump_frame(self._grab(), "map_not_open")
            raise Blocked("the map screen did not open -- the MAP button was "
                          f"clicked three times with no effect (saved {shot})")

    def _world_button(self, world):
        self.require_calibration()
        buttons = self.cal["world_buttons"]
        key = str(world)
        if key not in buttons:
            raise Blocked(f"world {world} is not in the calibration -- "
                          f"only {sorted(buttons)} were recorded")
        return buttons[key]

    def pick_world(self, world):
        """Select a world, without travelling anywhere."""
        x, y = self._world_button(world)
        self._click_game(x, y, settle=0.6)

    def go_to_town(self, world):
        """
        Double-click a world's tab, which teleports to that world's town.

        This replaced a longer route through the FREE TELEPORT button.  The
        double-click is the game's own shortcut, it costs nothing, and it skips
        both the map view and the business of picking one marker out of the
        twenty-five drawn on it -- so there is less to get wrong and nothing to
        calibrate per world beyond the tab itself.

        It must be a real double-click.  Two ordinary clicks in a row are not
        one: each settles before and after itself, which spaced the presses
        about half a second apart, past the system threshold -- the map opened,
        the tab was clicked twice, and the character stayed where it was.
        """
        from core.capture import to_screen
        x, y = self._world_button(world)
        sx, sy = to_screen(self.rect, x, y)
        self.clicker.double_click(sx, sy)
        time.sleep(2.5)                        # the town has to load in

    def bounce_via(self, world, spare=None):
        """
        Leave the world and come back, to force the map to actually change.

        Teleporting to the world you are already standing in does nothing --
        which matters because a task that is already open leaves its panel over
        the entrance, and the "journey" then completes without moving or
        closing anything.  Hopping to a different world first makes the return
        trip a real map change, which clears whatever was on screen.
        """
        spare = spare or (1 if world != 1 else 2)
        self.open_map()
        self.go_to_town(spare)
        self.open_map()
        self.go_to_town(world)

    def go_to_map(self, loc):
        """
        Travel to a specific map by double-clicking its marker.

        For places the town shortcut does not reach.  The marker is FOUND by
        template rather than clicked at a remembered position: the map view is
        a static picture, but which world is showing is not, and clicking a
        remembered spot on the wrong world's map would teleport somewhere
        arbitrary -- and unlike a wasted town hop, that spends one of the daily
        allowance.
        """
        import cv2
        from core.capture import to_screen

        tpl = cv2.imread(loc.map_icon)
        if tpl is None:
            raise Blocked(f"could not read {loc.map_icon}")
        # Re-check here rather than trusting open_map: the MAP button toggles,
        # so any click between the two steps closes the map again, and the
        # marker search would then hunt across the town screen and report the
        # marker missing.  That is exactly how a hoops run ended up searching
        # World 1's town for the Valley of the Beans.
        if not self.map_is_open():
            shot = _dump_frame(self._grab(), "map_closed_before_search")
            raise Blocked("the map closed again before the marker could be "
                          f"found (saved {shot})")
        panel = tuple(self.cal["map_panel"])
        best = 0.0
        for _ in range(3):
            frame = self._grab()
            hit = find_icon(frame, tpl, region=panel, min_score=0.86)
            if hit:
                sx, sy = to_screen(self.rect, hit[0], hit[1])
                self.clicker.double_click(sx, sy)
                time.sleep(2.5)          # the map has to load in
                return
            res = cv2.matchTemplate(frame[panel[1]:panel[3], panel[0]:panel[2]],
                                    tpl, cv2.TM_CCOEFF_NORMED)
            best = max(best, float(res.max()))
            time.sleep(0.5)
        shot = _dump_frame(self._grab(), "marker_not_found")
        raise Blocked(
            f"could not find the map marker after 3 looks (best {best:.2f}, "
            f"needs 0.86) -- is World {loc.world} showing? (saved {shot})")

    def _grab(self):
        import mss
        import numpy as np
        mon = {k: self.rect[k] for k in ("left", "top", "width", "height")}
        with mss.mss() as sct:
            return np.asarray(sct.grab(mon))[:, :, :3]

    def entrance_visible(self, loc):
        """
        Is the task's entrance on screen right now?

        One look, no clicking.  If it is there, the character is already
        standing on the right map and the whole map-and-teleport routine can be
        skipped -- which is the difference between spending a teleport to
        arrive somewhere you already are, and simply opening the thing.

        A single frame on purpose.  This runs before every task, and a miss is
        harmless: it falls through to travelling, which is what would have
        happened anyway.  Being slow to say "no" would cost more than the
        occasional unnecessary trip.

        Returns (x, y, frame) when the entrance is there, or None.  A tuple is
        truthy and None is falsy, so callers wanting only a yes/no are
        unaffected -- but the position lets a caller look at what is drawn
        AROUND the entrance, which is how a cooldown is spotted.
        """
        import cv2

        if not loc.entry_icon or not os.path.exists(loc.entry_icon):
            return None
        tpl = cv2.imread(loc.entry_icon)
        if tpl is None:
            return None
        frame = self._grab()
        hit = find_icon(frame, tpl)
        if hit is None:
            return None
        return (hit[0], hit[1], frame)

    def click_entry(self, loc, tries=3):
        """
        Find the task's entrance in the world and open it.

        Hovering the entry pops up its menu; the task's own icon is then
        clicked inside that popup.  Both are located by template rather than
        remembered as coordinates -- they sit in the world, so they move
        whenever the camera does.

        Nothing is clicked unless it was found.  A miss here would land on
        open ground, which walks the character, moves the entry, and breaks
        every later task in the queue -- so a miss raises instead.
        """
        import cv2
        from core.capture import to_screen

        entry_tpl = cv2.imread(loc.entry_icon)
        if entry_tpl is None:
            raise Blocked(f"could not read {loc.entry_icon}")

        best = 0.0
        for attempt in range(tries):
            frame = self._grab()
            found = find_icon(frame, entry_tpl)
            if found:
                ex, ey, _score = found
                break
            # Remember how close it got, so a near miss reads differently from
            # "nothing like it on screen at all".
            import cv2
            r = cv2.matchTemplate(frame, entry_tpl, cv2.TM_CCOEFF_NORMED)
            best = max(best, float(r.max()))
            time.sleep(0.6)         # the map may still be fading in
        else:
            shot = _dump_frame(self._grab(), "entrance_not_found")
            raise Blocked(
                f"could not find the entrance after {tries} looks "
                f"(best match {best:.2f}, needs 0.80) -- either the map did "
                f"not load, or the entrance is off screen or covered "
                f"(saved {shot})")

        # Two shapes of entrance, and the difference matters.
        #
        # Some open a little menu when hovered -- the World 7 speech bubble
        # offers Mr. Minehead and the Sushi Station -- so those are HOVERED and
        # then the wanted icon is clicked inside the menu.  Others are the
        # thing itself: clicking the top of the World 3 refinery opens the
        # refinery.  A location with no popup artwork is the second kind, and
        # treating it as the first made navigation give up after the map steps
        # with "the popup artwork is missing" for an entrance that has no popup.
        if not loc.popup_icon:
            ox, oy = getattr(loc, "entry_click_offset", (0, 0))
            self._click_game(ex + ox, ey + oy, settle=1.8)
            return

        # Hover to raise the popup.  Deliberately a move, not a click: the
        # entry opens on hover, and clicking the world is what moves the
        # character.
        sx, sy = to_screen(self.rect, ex, ey)
        self.clicker.move(sx, sy)
        time.sleep(0.7)

        if not os.path.exists(loc.popup_icon):
            raise Blocked("the popup artwork is missing, so the bot cannot "
                          "tell which entry in the menu to click")
        popup_tpl = cv2.imread(loc.popup_icon)
        hit = find_icon(self._grab(), popup_tpl)
        if not hit:
            raise Blocked("the entrance did not open its menu, or the menu "
                          "did not contain the expected icon")
        px, py, _ = hit
        self._click_game(px, py, settle=1.5)


# How many times to redo the map-and-teleport sequence before giving up.
# Opening the map and picking a world are free; only the destination
# double-click costs a teleport, and it is the step least likely to fail.
TRAVEL_TRIES = 3


def ensure_at(task, rect, clicker, log=None):
    """
    Make sure the character is somewhere `task` can run.

    Returns a short description of what happened, or raises Blocked if the
    task's destination could not be reached.  The caller treats Blocked the
    same way it always has: skip this task, keep the queue going.
    """
    def say(msg):
        if log:
            log(msg)

    try:
        task.can_run()
        say(f"already at {task.name}")
        return "already there"
    except Blocked as why:
        first_reason = str(why)

    loc = getattr(task, "location", None)
    if loc is None:
        # No route was ever defined, so there is nothing to try; report the
        # original reason rather than inventing a navigation failure.
        raise Blocked(first_reason)

    if not loc.via_town and not loc.map_icon:
        raise Blocked(
            f"{task.name} is reached through its own map, but the marker for "
            f"that map has not been captured yet")
    if not loc.entry_icon or not os.path.exists(loc.entry_icon):
        raise Blocked(f"{task.name} has no entry artwork, so the bot would "
                      f"not know what to click once it arrives")

    nav = Navigator(rect, clicker)
    _focus_game()

    # A task can start from three places, and only the first two used to be
    # handled: already inside it (can_run passed above), somewhere else
    # entirely (travel, below), or STANDING ON THE RIGHT MAP with the thing
    # unopened.  That third case went the long way round -- open the map,
    # teleport to where the character already was, click the entrance -- which
    # spends a teleport to arrive nowhere and, because the teleport is a no-op
    # when you are already in that world, often needed the bounce fallback to
    # recover.  Looking first costs one screenshot.
    seen = nav.entrance_visible(loc)
    if seen and getattr(loc, "cooldown_check", None):
        ex, ey, frame = seen
        if loc.cooldown_check(frame, (ex, ey)):
            # Standing in the right place, in front of a thing that cannot be
            # entered yet.  Travelling is the one response that cannot help,
            # and it was the response: one run spent a teleport to World 1 and
            # abandoned the task, because "the entrance did not open" was
            # indistinguishable from "we are somewhere else".
            raise Blocked(f"{task.name} is on cooldown -- the countdown is "
                          f"showing at the entrance, so it cannot be entered "
                          f"from anywhere yet")
    if seen:
        say("already on the right map -- opening it")
        try:
            nav.click_entry(loc)
            task.can_run()
            say("opened it")
            return "entered"
        except Blocked as why:
            # The shortcut is an optimisation, never a commitment.  A template
            # can match something that is not the entrance, or the click can
            # land while the map is still settling -- so if it does not work
            # out, fall through and travel properly rather than failing a task
            # the long route would have reached.
            say(f"that did not open it ({why}) -- travelling properly instead")

    say(f"travelling to {loc.map_name or f'world {loc.world}'}"
        + (" (costs a teleport)" if loc.costs_teleport else ""))

    def travel_once():
        say("opening the map")
        nav.open_map()
        if loc.via_town:
            say(f"double-clicking World {loc.world}")
            nav.go_to_town(loc.world)
            say("arrived in town")
        else:
            say(f"selecting World {loc.world}")
            nav.pick_world(loc.world)
            say("looking for the map marker")
            nav.go_to_map(loc)
            say(f"arrived at {loc.map_name or 'the map'}")

    # Retried, because the ways this fails are transient and cost nothing to
    # redo.  The map closing before the marker is found is the common one: the
    # map screen is a click away and reopening it spends no teleport, only the
    # destination double-click does.
    #
    # Giving up after one attempt also left the character somewhere it had not
    # asked to be -- one run selected World 1, lost the map, and abandoned the
    # task standing in W1 town, having spent a teleport to reach a place with
    # nothing to do.  Moving to a known map is a fine way to recover a confused
    # position; it is not a place to stop.
    for attempt in range(1, TRAVEL_TRIES + 1):
        try:
            travel_once()
            break
        except Blocked as why:
            if attempt == TRAVEL_TRIES:
                raise
            say(f"{why}")
            say(f"travel attempt {attempt} of {TRAVEL_TRIES} did not land "
                f"-- trying again")
            time.sleep(0.8)

    say("looking for the entrance")
    try:
        nav.click_entry(loc)
    except Blocked as first:
        # Most likely already inside the task, with its panel covering the
        # entrance -- and the teleport did nothing because the character was
        # already in this world.  Go somewhere else and come back, which forces
        # a real map change and clears the screen.
        say(f"{first}")
        say("already in this world with something open -- hopping out and back")
        nav.bounce_via(loc.world)
        say("looking for the entrance again")
        nav.click_entry(loc)
    say("opened it")

    # Arrival is proved by the task itself, not by having clicked in the right
    # order.  If can_run() still refuses, the route went wrong somewhere and
    # this is the last moment it can be said out loud rather than clicked over.
    task.can_run()
    return "travelled"
