#!/usr/bin/env python3
"""
Finding, locking and measuring the Idleon game window.

Why the old approach failed
---------------------------
The old bot did:

    hwnd = FindWindowW(None, "IdleOn")          # exact title match
    left = l + 8 ;  top = t + 38                # assumed border + title bar

Three separate problems, any one of which breaks it:

1. `FindWindowW` with a title requires an *exact*, full-string match.  The game
   never sets a static title -- `distBuild/app.html` has no <title> element at
   all, so the title is written at runtime by react-helmet and changes while the
   game loads ("Legends Of Idleon | Loading...", then something else).  None of
   the four hard-coded variants can be relied on.

2. `src/main/index.js` creates the BrowserWindow with `frame: false`.  There is
   no OS title bar and no OS border, so the +38 / +8 insets are pure fiction.
   The client area *is* the window rectangle.

3. The process was not DPI-aware.  On a scaled display Windows then hands back
   virtualised (logical) coordinates from GetWindowRect, while `mss` captures
   real physical pixels -- so even a correct rectangle grabs the wrong region.

This module fixes all three: it enumerates windows and matches on the owning
process image name, declares per-monitor DPI awareness before any window call,
and derives the canvas rectangle from GetClientRect / ClientToScreen instead of
guessing insets.  It also parks the window at (0, 0) so the geometry is stable
across runs, which is what you asked for.
"""

import ctypes
import time
import ctypes.wintypes as wt
import os
import sys

user32   = ctypes.WinDLL("user32",   use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# The game canvas, in game pixels (Stencyl scene size).
CANVAS_W, CANVAS_H = 960, 540

# Default BrowserWindow size from src/main/index.js.  960x572 with a 960x540
# canvas leaves 32 px for the app's own custom title bar, drawn in-page.
DEFAULT_WIN_W, DEFAULT_WIN_H = 960, 572

_SWP_NOZORDER   = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_SW_RESTORE     = 9


def set_dpi_aware():
    """
    Declare per-monitor DPI awareness so window coordinates are physical pixels,
    matching what mss captures.  Must run before any window query.
    """
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor-v2"
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)   # PROCESS_PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        pass
    user32.SetProcessDPIAware()
    return "system"


def _process_image(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        size = wt.DWORD(1024)
        buf  = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def _title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def enumerate_candidates():
    """All visible top-level windows, as dicts.  Useful for --list-windows."""
    found = []
    CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 200 or h < 200:
            return True
        found.append({
            "hwnd":  hwnd,
            "title": _title(hwnd),
            "cls":   _class_name(hwnd),
            "exe":   os.path.basename(_process_image(hwnd)),
            "rect":  (r.left, r.top, w, h),
        })
        return True

    user32.EnumWindows(CB(cb), 0)
    return found


def find_game_window():
    """
    Locate the Idleon window.  Matches on the process image name first (the only
    thing the game cannot change at runtime), then falls back to the window
    title, then to an Electron window of roughly the right shape.

    Returns a candidate dict, or None.
    """
    # Never pick our own window.  This is not hypothetical: the automator is
    # called "Idleon Automator" and runs from IdleonAutomator.exe, so a match on
    # "idleon" in the process name found IT before the game -- and the bot spent
    # several sessions screenshotting and clicking its own interface, which
    # looks exactly like a game that ignores every click.
    own_exe = os.path.basename(sys.executable).lower()
    own_names = {own_exe, "idleonautomator.exe", "python.exe", "pythonw.exe"}
    cands = [c for c in enumerate_candidates()
             if c["exe"].lower() not in own_names
             and "automator" not in c["exe"].lower()
             and "automator" not in c["title"].lower()]

    def by(pred):
        hits = [c for c in cands if pred(c)]
        return hits[0] if hits else None

    # The real game first, by its exact image name, before any looser match.
    return (by(lambda c: "legendsofidleon" in c["exe"].lower())
            or by(lambda c: "idleon" in c["exe"].lower())
            or by(lambda c: "idleon" in c["title"].lower())
            or by(lambda c: c["cls"] == "Chrome_WidgetWin_1"
                            and abs(c["rect"][2] / max(c["rect"][3], 1) - 960 / 572) < 0.05))


def lock_window(hwnd, x=0, y=0, w=DEFAULT_WIN_W, h=DEFAULT_WIN_H, focus=True):
    """
    Park the window at a fixed screen position and size.  With `frame: false`
    there are no non-client insets, so the requested size is the client size.
    """
    user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.SetWindowPos(hwnd, 0, x, y, w, h,
                        _SWP_NOZORDER | _SWP_SHOWWINDOW |
                        (0 if focus else _SWP_NOACTIVATE))
    if focus:
        user32.SetForegroundWindow(hwnd)


def canvas_rect(hwnd):
    """
    Screen rectangle of the 960x540 game canvas, as an mss monitor dict.

    The canvas is width-fitted and bottom-anchored inside the client area: the
    app draws its own title bar along the top and the Stencyl canvas fills what
    is left.  Deriving it this way (rather than hard-coding a 32 px header)
    keeps working if the window is resized.

    `scale` is screen pixels per game pixel; the caller should sanity-check it
    against the fitted platform amplitude, which must come out at 110 * scale.
    """
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    cw, ch = r.right - r.left, r.bottom - r.top

    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))

    # Fit 16:9 inside the client area, preferring full width.
    scale = min(cw / CANVAS_W, ch / CANVAS_H)
    w = int(round(CANVAS_W * scale))
    h = int(round(CANVAS_H * scale))

    return {
        "left":   pt.x + (cw - w) // 2,   # centred horizontally
        "top":    pt.y + (ch - h),        # anchored to the bottom, under the header
        "width":  w,
        "height": h,
        "scale":  scale,
    }


def point_belongs_to(hwnd, x, y):
    """
    Is the pixel at screen (x, y) actually owned by our game window?

    Clicks are delivered to whatever is topmost at the cursor, not to whatever
    we think we are aiming at.  If any window has come to sit over the canvas --
    an explorer window, a notification, the terminal itself -- the click silently
    goes there instead, the game never sees a throw, and the run looks exactly
    like a string of misses.  Checking costs one API call per shot.
    """
    top = user32.WindowFromPoint(wt.POINT(int(x), int(y)))
    if not top:
        return False
    # WindowFromPoint can return a child (Chromium renderer) -- walk up to the
    # top-level owner before comparing.
    GA_ROOT = 2
    root = user32.GetAncestor(top, GA_ROOT) or top
    return root == hwnd


def acquire(lock=True, x=0, y=0):
    """
    One-call setup: DPI-aware, find the window, park it, return its canvas rect.
    Raises RuntimeError with a readable message if the window is not found.
    """
    mode = set_dpi_aware()
    win  = find_game_window()
    if win is None:
        listing = "\n".join(
            f"    {c['exe']:<28} {c['cls']:<22} {c['rect']} {c['title']!r}"
            for c in enumerate_candidates())
        raise RuntimeError(
            "Could not find the Idleon window.\n"
            "  Visible top-level windows were:\n" + (listing or "    (none)"))

    if lock:
        lock_window(win["hwnd"], x, y)

    rect = canvas_rect(win["hwnd"])
    rect["hwnd"] = win["hwnd"]
    rect["dpi_mode"] = mode
    rect["exe"] = win["exe"]
    rect["title"] = win["title"]
    return rect


def focus(hwnd=None, settle=0.35):
    """
    Bring the game to the front WITHOUT moving or resizing it.

    Screen capture reads pixels at the window's coordinates, not the window's
    own buffer, so anything overlapping it is captured instead.  With the game
    behind a terminal, a capture returns the terminal -- and every downstream
    detector then reports nonsense that looks like a vision bug.  This cost a
    long debugging detour into grid geometry and digit thresholds when the real
    problem was that the frames were not of the game at all.

    `acquire(lock=True)` already focuses as part of locking; this is for the
    lock=False path, where the window position must be left alone.
    """
    if hwnd is None:
        # find_game_window() returns a candidate DICT, not a raw handle --
        # passing it straight to ShowWindow raises a ctypes ArgumentError.
        cand = find_game_window()
        if not cand:
            return None
        hwnd = cand["hwnd"]
    if not hwnd:
        return None
    user32.ShowWindow(hwnd, _SW_RESTORE)
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(settle)
    return hwnd
