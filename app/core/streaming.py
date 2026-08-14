"""
Turning a printing engine into a stream of progress lines.

The darts and hoops players were written as command-line programs: one long
blocking call that prints as it goes.  That is the right shape for a terminal
and the wrong shape for a UI, which needs a live log and a way to stop.

Rewriting them into generators would mean touching hundreds of lines of timing
code that currently works, in two engines, to serve a display concern.  So
instead the engine runs on a thread with its stdout captured, and this module
turns what it prints into the same Progress stream every other task yields.

WHAT THIS COSTS
---------------
`sys.stdout` is process-global, so the capture is too: while an engine is
running, anything else that prints would be swallowed into its log.  That is
acceptable only because the scheduler runs exactly one task at a time.  If that
ever stops being true, this breaks quietly, which is why it is written down.

Stopping is still the engine's own business.  This module can only stop
*reading*; the engine decides where it is safe to put the darts down, via the
`should_stop` callable it was given.
"""

import queue
import sys
import threading


class _LineQueue:
    """A file-like object that pushes each completed line onto a queue."""

    def __init__(self, q, mirror=None):
        self.q = q
        self.mirror = mirror     # keep the terminal working when there is one
        self.buf = ""

    def write(self, s):
        if self.mirror is not None:
            try:
                self.mirror.write(s)
            except Exception:
                pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = line.rstrip()
            if line:
                self.q.put(line)

    def flush(self):
        if self.mirror is not None:
            try:
                self.mirror.flush()
            except Exception:
                pass

    def isatty(self):
        return False


class EngineRun:
    """
    A blocking engine call, running on a thread, readable line by line.

    Use it as::

        run = EngineRun(lambda: darts_bot.play(...))
        for line in run.lines():
            yield Progress(line)
        run.raise_if_failed()
        throws, bulls, best, reason = run.result

    The engine's return value and any exception are captured rather than lost,
    because for these games the return value *is* the report -- how many throws,
    how many bullseyes, and why it stopped.
    """

    def __init__(self, fn):
        self.fn = fn
        self.result = None
        self.error = None
        self._q = queue.Queue()
        self._done = threading.Event()
        self._thread = None

    def _target(self):
        try:
            self.result = self.fn()
        except BaseException as e:      # BaseException: KeyboardInterrupt too
            self.error = e
        finally:
            self._done.set()

    def lines(self, poll=0.1):
        """Yield the engine's output lines until it finishes."""
        real_stdout = sys.stdout
        sys.stdout = _LineQueue(self._q, mirror=real_stdout)
        self._thread = threading.Thread(target=self._target, daemon=True)
        self._thread.start()
        try:
            while True:
                try:
                    yield self._q.get(timeout=poll)
                except queue.Empty:
                    # Only stop once the engine is done AND its output is
                    # drained -- the last lines are usually the summary, and
                    # dropping them would lose the run's result.
                    if self._done.is_set() and self._q.empty():
                        break
        finally:
            sys.stdout = real_stdout
        self._thread.join(timeout=5.0)

    def raise_if_failed(self):
        if self.error is not None:
            raise self.error
