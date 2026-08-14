"""
The panic key.

While a task runs, the bot owns the mouse: it is moving the pointer along eased
paths and holding buttons down for whole seconds at a time.  Reaching for the
Stop button in the window means fighting it for the cursor, which is exactly
the situation this exists to end.  So stopping is a key, watched globally, and
it works whichever window has focus.

WATCHED, NOT REGISTERED
-----------------------
Windows offers RegisterHotKey, which would be tidier -- but a registered hotkey
is *consumed*: while the bot ran, nothing else on the machine could use that
key, and the default here is a plain letter.  Polling GetAsyncKeyState instead
observes the key without taking it, so pressing Q still types a Q everywhere
else.  The cost is a thread waking 25 times a second, which is nothing next to
what the automation is doing.

The watcher only runs during a run.  Outside one, the key is nobody's business.

TWO PRESSES, TWO MEANINGS
-------------------------
    first press    stop cleanly -- the task finishes the step it is on
    second press   stop now -- release the mouse and abandon the thread

The clean stop is the right one almost always, because a task interrupted
between steps leaves the game in a state it chose.  But "almost always" is no
good when the pointer is being dragged across a grid and the user wants their
machine back, and a sushi cycle can be a minute away from its next stopping
point.  Hence the second press -- which must release the mouse button itself,
since abandoning a thread mid-drag would otherwise leave the button held down
and the desktop unusable.
"""

import ctypes
import threading
import time

user32 = ctypes.windll.user32

# Virtual-key codes for the keys worth offering.  Letters and digits are their
# ASCII codes; the rest are named because their numbers are not memorable.
NAMED_KEYS = {
    "ESC": 0x1B, "SPACE": 0x20, "PAUSE": 0x13, "END": 0x23, "HOME": 0x24,
    "INSERT": 0x2D, "DELETE": 0x2E, "SCROLLLOCK": 0x91,
    **{f"F{i}": 0x6F + i for i in range(1, 13)},
}


def key_code(name):
    """Virtual-key code for a key name, or None if it is not one we know."""
    name = (name or "").strip().upper()
    if name in NAMED_KEYS:
        return NAMED_KEYS[name]
    if len(name) == 1 and (name.isalpha() or name.isdigit()):
        return ord(name)
    return None


def release_mouse():
    """
    Let go of the left button, whatever was holding it.

    Sent unconditionally: asking Windows whether the button is down and only
    then releasing it would be a race against the thread still driving it, and
    a spurious mouse-up costs nothing while a stuck one costs the desktop.
    """
    from core.input import _INPUT, _MOUSEEVENTF_LEFTUP, _mouse_event
    up = _mouse_event(_MOUSEEVENTF_LEFTUP)
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))


class StopKey(threading.Thread):
    """
    Watches one key while a run is in progress.

    `on_stop` is called on the first press, `on_force` on any press after that.
    Both are called from this thread, so anything touching the UI must marshal
    back to the main thread -- in practice they emit Qt signals, which does.
    """

    def __init__(self, key="Q", on_stop=None, on_force=None, poll=0.04):
        super().__init__(daemon=True)
        self.key = key
        self.vk = key_code(key)
        self.on_stop = on_stop
        self.on_force = on_force
        self.poll = poll
        self._quit = threading.Event()
        self.pressed_once = False

    def stop_watching(self):
        self._quit.set()

    def run(self):
        if self.vk is None:
            return                      # unknown key: watch nothing, quietly
        was_down = True                 # start True so a key already held when
        # the run begins does not count as a press -- otherwise starting a run
        # with the key down would stop it instantly.
        while not self._quit.is_set():
            down = bool(user32.GetAsyncKeyState(self.vk) & 0x8000)
            if down and not was_down:
                if not self.pressed_once:
                    self.pressed_once = True
                    if self.on_stop:
                        self.on_stop()
                else:
                    release_mouse()
                    if self.on_force:
                        self.on_force()
            was_down = down
            time.sleep(self.poll)
