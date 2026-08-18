"""
The Idleon Automator window.

Left: every task that exists.  Right: the list you have built, reorderable by
drag.  Below: what is happening, and how long is left.

THREADING
---------
Tasks click a live game and take minutes.  Running one on the UI thread would
freeze the window, so the whole run happens on a worker thread and talks back
through Qt signals.  Stop is cooperative: the worker sets a flag the task checks
between steps, so the run ends at a point the task chose rather than being
killed mid-drag with the mouse button down.
"""

import os
import sys
import threading
import time
from html import escape

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter,
                               QScrollArea, QTabWidget, QToolButton,
                               QTextBrowser,
                               QVBoxLayout, QWidget)

import core.input as _input
import core.window as _window
from core import registry, settings, tasklists, update
from core.debuglog import log, start_session
from core.hotkey import StopKey
from core.task import Result, run_task
from ui.lists import ListTab, cross_icon
from ui.flow import TaskPalette
from ui.metro import DONE, PROBLEM, RUNNING
from ui.footer import HOW_TO, AfterRun, Footer, Panel
from ui.params import ParamEditor
from ui import theme

# The panic key.  Watched rather than registered, so it is never taken away
# from anything else -- but a function key is a better default than a letter
# regardless: nothing types F6 by accident.
STOP_KEY = "F6"

# The task a library row stands for, kept as data rather than parsed back out
# of the row's text -- the text carries a world suffix and will carry more.
NAME_ROLE = Qt.UserRole + 10
WORLD_ROLE = Qt.UserRole + 11


def _heading(text):
    """A section label in the title colour."""
    lab = QLabel(text)
    lab.setProperty("heading", True)
    return lab


class UpdateCheck(QObject):
    """
    Asks the release bucket whether there is a newer version.

    Off the UI thread because it must never delay the window: core.update
    bounds the HTTP exchange at 5 s, but NOT name resolution -- an unresolvable
    host took 11 s to fail in testing, and that is the OS resolver's timeout,
    not ours.  On the launch path that would be eleven seconds of nothing for
    anyone offline, which is exactly the user who least deserves it.

    A daemon thread rather than a QThread like Runner, and the difference
    matters at exit: a QThread still running when its owner is destroyed prints
    "Destroyed while thread is still running" and aborts, so closing the window
    during that 11 s resolver wait would crash on the way out.  The alternative
    -- blocking close until the check finishes -- trades a crash for a window
    that will not shut.  A daemon thread is simply abandoned at exit, which is
    the right answer for work whose result nobody wants any more.

    `found` carries the manifest dict, and is simply never emitted when there
    is no update, no network, or a manifest that does not parse: core.update
    returns None for all of those, so nothing here needs to tell them apart.
    """

    found = Signal(object)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            info = update.check()
            if info:
                self.found.emit(info)
        except Exception:
            # check() is written never to raise, and a version notice is not
            # worth a traceback on a background thread if that ever stops being
            # true.  The emit can also fail on its own if the window went away
            # while the request was in flight.
            pass


class Runner(QThread):
    progress = Signal(str)
    task_started = Signal(int, str)
    finished_one = Signal(int, str, float, bool, str)
    all_done = Signal()

    def __init__(self, tasks, navigate=True):
        super().__init__()
        self.tasks = tasks
        self.navigate = navigate
        self._stop = False

    def stop(self):
        self._stop = True

    def walk_to(self, task):
        """
        Travel to a task before running it.

        A failed journey is logged but not raised.  run_task() calls can_run()
        immediately afterwards and will report the task as *skipped*, which is
        what it is -- letting Blocked escape here would instead label it
        "failed", and a task that could not be reached has not failed.  The
        navigation-specific reason is logged first, since can_run() only knows
        the generic one ("the station is not on screen").
        """
        from core.navigate import ensure_at
        from core.task import Blocked

        try:
            rect = _window.acquire(lock=False, x=0, y=0)
            clicker = _input.Clicker()
            ensure_at(task, rect, clicker,
                      log=lambda m: self.progress.emit(f"    {m}"))
        except Blocked as e:
            self.progress.emit(f"    could not travel: {e}")
        except Exception as e:
            self.progress.emit(f"    could not travel: "
                               f"{type(e).__name__}: {e}")

    def run(self):
        for i, task in enumerate(self.tasks):
            if self._stop:
                break
            self.task_started.emit(i, task.name)
            self.progress.emit(f"--- {task.name} ---")
            t0 = time.perf_counter()
            # A task that throws must not take the worker thread with it: the
            # rest of the list is still worth running, and a silent dead thread
            # would look identical to a task that is simply slow.
            try:
                # Get the character there first.  ensure_at() is a no-op when
                # the task can already run, so a queue that stays in one place
                # never opens the map at all.
                if self.navigate:
                    self.walk_to(task)
                result = run_task(
                    task,
                    stop=lambda: self._stop,
                    on_progress=lambda p: self.progress.emit(f"    {p.message}"))
            except Exception as e:
                result = Result(ok=False,
                                summary=f"{task.name}: failed - "
                                        f"{type(e).__name__}: {e}")
            secs = time.perf_counter() - t0
            registry.record_run(task.name, secs, result.ok)
            self.progress.emit(f"    {result.summary}  "
                               f"[{registry.format_eta(secs)}]")
            self.finished_one.emit(i, task.name, secs, result.ok,
                                   result.summary)
        self.all_done.emit()


class Main(QMainWindow):
    stop_requested = Signal()
    force_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Idleon Automator")
        self.resize(1000, 900)
        # Tall by default and free to grow: a run list of eight
        # tasks has to be readable without scrolling, which was the
        # point of drawing it as a line.
        self.setMinimumSize(880, 620)
        self.runner = None
        self.hotkey = None
        self.stop_requested.connect(self.stop_run)
        self.force_requested.connect(self.force_stop)

        self.library = TaskPalette()
        self.library.selected.connect(self.show_details)
        self.library.activated.connect(self.add_named)
        # Sorted by world, then by name: the palette groups itself, which is
        # what the W1-W7 filter buttons were really for.  Sorting needs no
        # interaction and cannot be left switched on by accident.
        self._tasks = {t.name: t for t in sorted(
            registry.available_tasks(),
            key=lambda t: (getattr(t, 'world', 0) or 99, t.name))}

        lib_scroll = QScrollArea()
        lib_scroll.setWidget(self.library)
        lib_scroll.setWidgetResizable(True)
        lib_scroll.setMinimumHeight(76)      # chips wrap; it needs little
        lib_scroll.setMaximumHeight(190)     # and should not hog the panel

        self.lib_scroll = lib_scroll

        self.tabs = QTabWidget()
        self.tabs.setObjectName("lists")     # so its tabs can be styled alone
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.tabBarDoubleClicked.connect(self.rename_current)

        self.add_btn = QPushButton("Add to list")
        self.add_btn.clicked.connect(self.add_selected)
        self.add_btn.setEnabled(False)
        self.add_btn.setMinimumHeight(30)
        self.add_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.PANEL}; color: {theme.MUTED};"
            f" border: 1px solid {theme.BORDER_SOFT};"
            # Square: it sits flush under the palette, and a rounded top edge
            # against a straight one reads as two things that failed to meet.
            f" border-radius: 0px; padding: 5px 14px; }}"
            f"QPushButton:enabled {{ background: {theme.BORDER};"
            f" color: {theme.TITLE}; font-weight: bold;"
            f" border: 1px solid {theme.BORDER}; }}"
            f"QPushButton:enabled:hover {{ background: {theme.TITLE};"
            f" color: {theme.BACKGROUND}; border-color: {theme.TITLE}; }}")
        self.start = QPushButton("Run list")
        self.start.setMinimumHeight(42)
        self._run_style = (
            f"QPushButton {{ font-size: {theme.FS_RUN}px; font-weight: bold;"
            f" background: {theme.RUN_BG}; color: {theme.RUN_TEXT};"
            f" border: 2px solid {theme.RUN_BG};"
            f" border-radius: {theme.RADIUS}px; }}"
            f"QPushButton:hover {{ background: {theme.RUN_HOVER};"
            f" border-color: {theme.RUN_HOVER}; }}"
            # Nothing queued: still there, plainly not ready.  Disabled rather
            # than clickable-and-then-nothing-happens, which is how it used to
            # behave and gave no clue why.
            f"QPushButton:disabled {{ background: {theme.RUN_OFF_BG};"
            f" color: {theme.RUN_OFF_TEXT};"
            f" border-color: {theme.BORDER_SOFT}; }}")
        self.start.setStyleSheet(self._run_style)
        self.start.setEnabled(False)
        self.start.clicked.connect(self.start_run)

        # Two different readers.  The activity log is for watching a run in
        # progress; the report is for someone coming back to a finished one and
        # asking only "did it work". Mixing them buries the answer in detail.
        self.log = QPlainTextEdit(readOnly=True)
        # Four lines is enough: it is a ticker for "something is still
        # happening", and the run report is what gets read afterwards.
        fm = self.log.fontMetrics()
        # Three lines exactly: it is a ticker saying something is still
        # happening, and the report above is what gets read.
        self.log.setFixedHeight(fm.lineSpacing() * 3 + 12)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log.setStyleSheet(
            f"QPlainTextEdit {{ font-size: {theme.FS_LOG}px;"
            f" font-family: Consolas, 'Cascadia Mono', monospace;"
            f" background: {theme.PANEL};"
            f" border: {theme.BORDER_WIDTH}px solid {theme.BORDER};"
            f" border-radius: {theme.RADIUS}px; color: {theme.TEXT}; }}")
        self.report = QTextBrowser()
        # The report is the one place people look after a run, so the links
        # are repeated there -- and they have to actually open.
        self.report.setOpenExternalLinks(True)

        # Before-you-run instructions.  can_run() catches what a screenshot can
        # see, but "turn the oven mitt off" is not visible to it and is exactly
        # the thing that makes a run silently do nothing -- so it has to be
        # said here, before the user presses Run.
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setMinimumHeight(150)

        # Deferred until now: set_tasks selects the first chip, which emits
        # `selected` and draws into self.details -- which did not exist yet
        # while the palette was being built.
        self.library.set_tasks(list(self._tasks.values()))

        newtab = QPushButton("New list")
        newtab.clicked.connect(lambda: self.new_tab())
        # No rename button: double-clicking the tab does it, which is what
        # people try first anyway, and the icon was one more thing sitting in
        # the tab bar competing with the tabs themselves.
        openb = QPushButton("Open...")
        openb.setToolTip("Reopen a saved list that is not currently in a tab")
        openb.clicked.connect(self.open_saved)


        self.editor = ParamEditor()
        self.editor.changed.connect(self.param_edited)

        self.footer = Footer()
        self.footer.show_disclaimer.connect(self.open_disclaimer)
        self.footer.show_how_to.connect(self.open_how_to)

        # The left panel does two jobs at two different times, and never both
        # at once: while you are building a list you need the task palette,
        # and while a list is running you cannot use the mouse anyway -- so
        # the palette is dead space exactly when the report needs room.
        build = QVBoxLayout(); build.setContentsMargins(0, 0, 0, 0)
        # No automatic gaps: the palette and the Add button below it read as
        # one control, so the spacing is placed deliberately instead.
        build.setSpacing(0)
        build.addWidget(self.lib_scroll)
        build.addWidget(self.add_btn)
        before = _heading("Before you run")
        before.setAlignment(Qt.AlignCenter)
        before.setContentsMargins(0, 12, 0, 4)
        build.addWidget(before)
        build.addWidget(self.details)
        build_page = QWidget(); build_page.setLayout(build)

        # The report and the activity log belong together: one says what
        # happened, the other says what is happening, and reading a finished
        # run means glancing at both.  The report is capped so the log has
        # somewhere to sit rather than being pushed off the panel.
        act_here = QVBoxLayout(); act_here.setContentsMargins(0, 6, 0, 0)
        act_here.addWidget(_heading("Activity"))
        act_here.addWidget(self.log)
        self.activity_panel = QWidget(); self.activity_panel.setLayout(act_here)
        self.activity_panel.setVisible(False)

        # The report takes whatever height there is, from the top down; the
        # activity log is pinned to the bottom at a fixed three lines.  Capping
        # the report instead left it floating in the middle of an empty panel.
        self.after_run = AfterRun(self.footer)

        running = QVBoxLayout(); running.setContentsMargins(0, 0, 0, 0)
        running.addWidget(self.report, 1)      # what happened
        running.addWidget(self.after_run, 0)   # where to go next
        running.addWidget(self.activity_panel, 0)   # what is happening
        run_page = QWidget(); run_page.setLayout(running)

        # Tabs rather than an automatic swap: the report takes over on its own
        # when a run starts, but getting back to the palette afterwards has to
        # be a thing you can click, not a thing you have to guess at.
        self.left_stack = QTabWidget()
        self.left_stack.addTab(build_page, "Available tasks")
        self.left_stack.addTab(run_page, "Last run")

        left = QVBoxLayout(); left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.left_stack)
        leftw = QWidget(); leftw.setLayout(left)

        right = QVBoxLayout()
        hdr = QHBoxLayout()
        hdr.addWidget(_heading("Task lists")); hdr.addStretch()
        hdr.addWidget(openb); hdr.addWidget(newtab)
        right.addLayout(hdr)
        right.addWidget(self.tabs, 1)
        right.addWidget(self.editor)
        right.addWidget(self.start)
        rightw = QWidget(); rightw.setLayout(right)

        # A splitter, not a fixed ratio: how wide the palette wants to be
        # depends on how many tasks exist and how long their names are, and
        # only the person looking at it knows that.  Wider than half by default
        # so a twenty-task palette starts three chips to a row rather than one.
        title = QLabel("Putting Idle back in IdleOn")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {theme.TITLE}; padding: 4px; font-style: italic;"
            f" font-size: {theme.FS_TITLE}px;")
        self.title = title

        # A plain two-column layout.  This was a splitter so the palette could
        # be widened, but the draggable bars between the columns were more
        # visual weight than the resizing was worth.
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(leftw, 5)
        top.addWidget(rightw, 6)
        topw = QWidget(); topw.setLayout(top)

        # No bottom pane any more: the report and the log moved into the left
        # panel, and the height they used goes to the run list instead.
        # The title sits above everything, so the whole window is wrapped.
        shell = QVBoxLayout()
        shell.setContentsMargins(6, 4, 6, 6)
        shell.addWidget(self.title)
        shell.addWidget(topw, 1)
        shell.addWidget(self.footer)
        root = QWidget(); root.setLayout(shell)
        self.setCentralWidget(root)

        # Parented to the WINDOW, not to the central widget: as a child of the
        # central widget it sat alongside the footer and left that strip
        # showing along the bottom.
        self.disclaimer = Panel(self)
        self.disclaimer.hide()
        self.how_to = Panel(self, HOW_TO)
        self.how_to.hide()
        # Once, on the first launch ever.  A disclaimer nobody has read is
        # worth showing; one shown every time is one people learn to dismiss
        # without looking, which is worse than not showing it.
        if not settings.get("disclaimer_seen"):
            QTimer.singleShot(0, self.open_disclaimer)
            settings.set("disclaimer_seen", True)
        self._report_lines = []

        # Every saved list gets a tab; a fresh one if the user has none yet.
        for name in tasklists.names():
            self.new_tab(name, tasklists.load(name))
        if not self.tabs.count():
            self.new_tab()

        # Ask about updates once the window is up.  Kept on self so it is not
        # collected while its thread is still holding a reference to the
        # signal, and started via singleShot so the check never sits between
        # the user launching the program and seeing it.
        self._update = None            # the manifest, once a check has found one
        self._update_check = UpdateCheck()
        self._update_check.found.connect(self.update_found)
        QTimer.singleShot(0, self._update_check.start)

    def show_details(self, item, _prev=None):
        if item is None:
            self.details.setHtml("")
            return
        t = self._tasks[item.text().split('   (')[0]]
        reqs = "".join(f"<li>{escape(r)}</li>" for r in t.requirements) \
            or "<li>Nothing in particular.</li>"
        self.details.setHtml(
            f"<p>{escape(t.description)}</p>"
            f"<p><b>Requires:</b></p><ul>{reqs}</ul>"
            f"<p><i>Typically {registry.format_eta(registry.estimate_for(t))}."
            f"</i></p>")

    # ---- tabs ------------------------------------------------------------

    def new_tab(self, name=None, entries=None):
        name = name or f"New list {self.tabs.count() + 1}"
        tab = ListTab(name, entries)
        tab.entries_changed.connect(self.list_changed)
        tab.selection_changed.connect(self.show_params)
        self.tabs.addTab(tab, name)
        self._own_close_button(self.tabs.indexOf(tab))
        self.tabs.setCurrentWidget(tab)
        return tab

    def _own_close_button(self, index):
        """
        Replace Qt's close button with one we can actually position.

        `QTabBar::close-button { margin-right: ... }` is accepted by the
        stylesheet and then ignored -- measured, the cross sat 1 px from the
        tab's edge whatever the margin said, because Qt lays that sub-control
        out itself.  Supplying the widget is the only way to control the gap.
        """
        from PySide6.QtWidgets import QTabBar

        bar = self.tabs.tabBar()
        holder = QWidget()
        lay = QHBoxLayout(holder)
        # Tight to the label, with the gap kept on the outside edge -- the
        # space that matters is between the cross and the tab's border, not
        # between the cross and the name it belongs to.
        lay.setContentsMargins(0, 0, 9, 0)
        btn = QToolButton()
        btn.setIcon(cross_icon(16))
        btn.setIconSize(QSize(16, 16))
        btn.setAutoRaise(True)
        btn.setFixedSize(22, 22)
        btn.setToolTip("Close this list")
        page = self.tabs.widget(index)
        btn.clicked.connect(lambda: self.close_tab(self.tabs.indexOf(page)))
        lay.addWidget(btn)
        bar.setTabButton(index, QTabBar.RightSide, holder)

    def close_tab(self, index):
        """
        Close a tab, and be honest about what that does to the saved list.

        The first version asked "also delete the saved list?" and, on No, closed
        the tab anyway -- with no way to reopen a saved list, which made No and
        Yes the same thing from the outside.  Now No really does keep it, and
        Open... brings it back.
        """
        tab = self.tabs.widget(index)
        saved = tab.list_name in tasklists.names()
        if saved:
            box = QMessageBox(self)
            box.setWindowTitle("Close list")
            box.setText(f'Close "{tab.list_name}"?')
            box.setInformativeText(
                "It stays saved and you can reopen it with Open... , "
                "unless you choose to delete it.")
            keep = box.addButton("Close, keep saved", QMessageBox.AcceptRole)
            drop = box.addButton("Delete permanently", QMessageBox.DestructiveRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            clicked = box.clickedButton()
            if clicked not in (keep, drop):
                return
            if clicked is drop:
                tasklists.delete(tab.list_name)
        elif tab.queue.count():
            if QMessageBox.question(
                    self, "Close list",
                    f'"{tab.list_name}" has never been saved. Close it and '
                    f'lose it?',
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        self.tabs.removeTab(index)
        if not self.tabs.count():
            self.new_tab()

    def open_saved(self):
        """Reopen a saved list that is not already in a tab."""
        open_now = {self.tabs.widget(i).list_name
                    for i in range(self.tabs.count())}
        choices = [n for n in tasklists.names() if n not in open_now]
        if not choices:
            QMessageBox.information(
                self, "Open list",
                "Every saved list is already open."
                if tasklists.names() else "There are no saved lists yet.")
            return
        name, ok = QInputDialog.getItem(self, "Open list", "Saved lists:",
                                        choices, 0, False)
        if ok and name:
            self.new_tab(name, tasklists.load(name))

    def open_disclaimer(self):
        """Cover the window with the disclaimer until it is dismissed."""
        self._cover(self.disclaimer)

    def open_how_to(self):
        """Cover the window with the how-to until it is dismissed."""
        self._cover(self.how_to)

    def _cover(self, panel):
        panel.setGeometry(self.rect())
        panel.raise_()
        panel.show()
        panel.setFocus()

    def resizeEvent(self, event):
        # The panels are children rather than dialogs, so they have to be told
        # to keep covering the window when the window changes size.
        super().resizeEvent(event)
        for name in ("disclaimer", "how_to"):
            p = getattr(self, name, None)
            if p is not None and p.isVisible():
                p.setGeometry(self.rect())

    def rename_current(self, *_):
        """Ask for a new name for the list in view."""
        tab = self.current_tab()
        if tab is None:
            return
        name, ok = QInputDialog.getText(self, "Rename list",
                                        "Name for this list:",
                                        text=tab.list_name)
        if ok and tab.rename_to(name):
            self.tabs.setTabText(self.tabs.currentIndex(), tab.list_name)

    def current_tab(self):
        return self.tabs.currentWidget()

    def tab_changed(self, *_):
        self.show_params()
        self.refresh_eta()

    def list_changed(self, *_):
        tab = self.current_tab()
        if tab:
            self.tabs.setTabText(self.tabs.currentIndex(), tab.list_name)
        self.refresh_eta()

    # ---- library and settings -------------------------------------------

    def show_details(self, name=None, _prev=None):
        self.add_btn.setEnabled(bool(name or self.library.current_name()))
        t = self._tasks.get(name or self.library.current_name())
        if t is None:
            self.details.setHtml("")
            return
        reqs = ("".join(f"<li>{escape(r)}</li>" for r in t.requirements)
                or "<li>Nothing in particular.</li>")
        self.details.setHtml(
            f"<p>{escape(t.description)}</p>"
            f"<p><b>Requires:</b></p><ul>{reqs}</ul>"
            f"<p><i>Typically {registry.format_eta(registry.estimate_for(t))}."
            f"</i></p>")

    # Words that mean a run went wrong, and words that mean it is merely
    # taking its time.  Matched on the text because the engines were written
    # as command-line programs and print prose, not structured events -- and
    # rewriting three engines to emit event codes to colour a log would be a
    # poor trade.
    BAD = ("failed", "error", "could not", "cannot", "skipped", "refus",
           "not on screen", "traceback", "no module")
    WARN = ("stopped", "stalling", "stalled", "stall", "warning", "retry",
            "waiting", "time limit", "nothing changed")

    def log_line(self, text):
        """Append one activity line, coloured by what it appears to say."""
        body = escape(text)
        low = text.lower()
        if text.startswith("---"):
            html = f'<span style="color:#1f6feb"><b>{body}</b></span>'
        elif any(w in low for w in self.BAD):
            html = f'<span style="color:#dc2626"><b>{body}</b></span>'
        elif any(w in low for w in self.WARN):
            html = f'<span style="color:#b36b00">{body}</span>'
        else:
            html = f'<span style="color:#333">{body}</span>'
        self.log.appendHtml(html)

    def show_palette(self):
        """
        Go back to building lists.

        Only ever called deliberately.  An earlier version switched back on its
        own the moment you touched the run list, which made sense when the two
        panels were a stack you could not control -- with real tabs it fights
        the user, snatching the report away mid-read to show a palette they did
        not ask for.
        """
        self.left_stack.setCurrentIndex(0)

    def add_selected(self):
        self.add_named(self.library.current_name())

    def add_named(self, name):
        tab = self.current_tab()
        if tab is not None and name:
            tab.add(name)

    def show_params(self, *_):
        tab = self.current_tab()
        if tab is None:
            self.editor.show_task(None)
            return
        row = tab.queue.currentRow()
        is_last = row == tab.queue.count() - 1
        self.editor.show_task(tab.current_task(), is_last=is_last)

    def param_edited(self):
        """Push edited settings back to the selected entry as they change."""
        tab = self.current_tab()
        if tab:
            tab.update_current(self.editor.values())

    def queued_tasks(self):
        tab = self.current_tab()
        return tab.tasks() if tab else []

    def refresh_eta(self, *_):
        tab = self.current_tab()
        if tab is None:
            return
        tab.refresh_warning()
        label = tab.eta
        tasks = self.queued_tasks()
        # The Run button follows the list: an empty one cannot be run, and
        # saying so with the button beats letting it be pressed for nothing.
        running = self.runner is not None and self.runner.isRunning()
        self.start.setEnabled(bool(tasks) and not running)
        if not tasks:
            label.setText("Nothing queued")
            return
        # An endless task has no estimate to add, so the total is reported as a
        # floor rather than a figure that would quietly be wrong.
        endless = [t for t in tasks if t.runs_forever]
        total = sum(registry.estimate_for(t) for t in tasks if not t.runs_forever)
        if endless:
            label.setText(f"{len(tasks)} task(s)  -  at least "
                          f"{registry.format_eta(total)}, then "
                          f"{endless[-1].name} until you stop it")
        else:
            label.setText(f"{len(tasks)} task(s)  -  estimated "
                          f"{registry.format_eta(total)}")

    def set_running(self, running):
        """
        The single action button, and the Activity panel, follow the run.

        One button rather than two: when a list is running the only useful
        action is stopping, and when it is not the only useful action is
        starting -- so a pair of buttons meant one of them was always dead.
        """
        if running:
            self.activity_panel.setVisible(True)
            self.after_run.setVisible(False)
        if running:
            self.start.setText(f"Press {STOP_KEY} to stop")
            self.start.setEnabled(True)
            self.start.setStyleSheet(
                f"QPushButton {{ font-size: {theme.FS_RUN}px; font-weight: bold;"
                f" background: {theme.RUN_STOP_BG}; color: {theme.RUN_TEXT};"
                f" border: 2px solid {theme.RUN_STOP_BG};"
                f" border-radius: {theme.RADIUS}px; }}"
                f"QPushButton:hover {{ background: {theme.RUN_STOP_HOVER};"
                f" border-color: {theme.RUN_STOP_HOVER}; }}")
        else:
            self.start.setText("Run list")
            self.start.setStyleSheet(self._run_style)
            # Only once a run has actually happened, not on a window that has
            # simply never been used.
            self.after_run.setVisible(bool(self._report_lines))
            self.refresh_eta()          # re-decides whether it can be pressed

    def start_run(self):
        # The same button stops a run in progress -- it says so at the time.
        if self.runner is not None and self.runner.isRunning():
            self.stop_run()
            return
        tasks = self.queued_tasks()
        if not tasks:
            return
        issues = tasklists.problems(tasks)
        if issues:
            QMessageBox.warning(self, "This list would stall",
                                "\n\n".join(issues))
            return
        self.log.clear()
        self.set_running(True)
        tab = self.current_tab()
        tab.reset_states()
        tab.set_editable(False)
        self.left_stack.setCurrentIndex(1)          # show the report
        self.left_stack.setTabText(1, "Run in progress")
        self._report_lines = []
        self._run_started = time.time()
        self._stopped_early = False
        self.note("list", f"List \"{tab.list_name}\" started -- "
                          f"{len(tasks)} task(s)")

        self.runner = Runner(tasks)
        self.runner.progress.connect(self.log_line)
        self.runner.task_started.connect(self.task_started)
        self.runner.finished_one.connect(self.task_finished)
        self.runner.all_done.connect(self.run_finished)
        self.log_line(
            f"Press {STOP_KEY} to stop, {STOP_KEY} again to force "
            f"(works even while the bot has the mouse).")
        self.runner.start()

        # Watch the panic key only while the run is in progress.  The callbacks
        # fire on the watcher thread, so they go through signals rather than
        # touching widgets directly.
        self.hotkey = StopKey(STOP_KEY,
                              on_stop=self.stop_requested.emit,
                              on_force=self.force_requested.emit)
        self.hotkey.start()

    def stop_run(self):
        if self.runner:
            self._stopped_early = True
            self.runner.stop()
            self.log_line(f"    (stopping after the current step "
                          f"-- press {STOP_KEY} again to force)")

    def force_stop(self):
        """
        Give the mouse back immediately, at the cost of a clean finish.

        The button has already been released by the watcher before this runs.
        The thread is abandoned rather than asked to stop, because the whole
        point of the second press is that asking was not fast enough.
        """
        self._stopped_early = True
        self.log_line("    FORCED STOP -- mouse released, run abandoned")
        self.note("stopped", "Forced stop -- mouse released, run abandoned")
        if self.runner:
            self.runner.stop()
            self.runner.terminate()
            self.runner.wait(2000)
        self.run_finished()

    # ---- the metro line and the run report -------------------------------

    def note(self, kind, text):
        """Record one event for the run report."""
        self._report_lines.append({"kind": kind, "text": text,
                                   "when": time.strftime("%H:%M:%S")})
        self.render_report()

    def task_started(self, row, name):
        tab = self.current_tab()
        if tab:
            tab.set_state(row, RUNNING)
            tab.queue.scrollToItem(tab.queue.item(row))
        self.note("start", f"{name} started")

    def task_finished(self, row, name, seconds, ok, summary):
        tab = self.current_tab()
        if tab:
            tab.set_state(row, DONE if ok else PROBLEM)
        self.note("done" if ok else "problem",
                  f"{summary}  ({registry.format_eta(seconds)})")

    def render_report(self):
        """
        The whole run in plain language, newest last.

        Written for someone who was not watching: when each task started and
        finished, what it achieved, and -- the part that matters -- anything
        that did not work, in the same words the log used.  The list's own
        beginning and end are recorded too, so "it stopped early" is
        distinguishable from "it finished everything".
        """
        if not self._report_lines:
            self.report.setHtml(
                f"<p style='color:{theme.MUTED}'>Nothing has run yet. Each task will "
                "report here as it starts and finishes.</p>")
            return
        style = {
            "start":   ("#4a6fa5", "&#9679;"),
            "done":    ("#16a34a", "&#9679;"),
            "problem": ("#dc2626", "&#9679;"),
            "list":    ("{theme.TITLE}", "&#9632;"),
            "stopped": ("#b36b00", "&#9632;"),
        }
        done = sum(1 for r in self._report_lines if r["kind"] == "done")
        bad = sum(1 for r in self._report_lines if r["kind"] == "problem")
        head = (f"<p><b>{done} completed</b>"
                + (f", <span style='color:#dc2626'><b>{bad} with problems"
                   f"</b></span>" if bad else "")
                + "</p>")
        rows = []
        for r in self._report_lines:
            colour, glyph = style.get(r["kind"], (theme.TITLE, "&#9679;"))
            weight = "bold" if r["kind"] in ("problem", "list", "stopped") else "normal"
            rows.append(
                f"<tr>"
                f"<td style='color:{colour}'>{glyph}</td>"
                f"<td style='color:{theme.MUTED};padding-right:8px'>"
                f"{r['when']}</td>"
                f"<td style='color:{colour};font-weight:{weight}'>"
                f"{escape(r['text'])}</td>"
                f"</tr>")
        # The closing line sits at the end of the log itself, where the run's
        # last event belongs -- not in the strip below, which is about where to
        # go next rather than what happened.
        tail = ""
        if self._report_lines and not (self.runner is not None
                                       and self.runner.isRunning()):
            tail = (f"<p style='margin-top:10px;color:{theme.TITLE}'>"
                    f"<b>The run has completed.</b></p>")
            # The end of a finished run is the one moment the user is reading
            # this panel and is not busy -- so it is where a new version gets
            # mentioned a second time, in the place they are already looking.
            # No dialog: a modal that interrupts a finished run to announce
            # something optional is the thing everyone learns to dismiss
            # without reading, which would cost us the footer badge's credit
            # too.
            tail += self._update_html()
        self.report.setHtml(head + "<table cellspacing='3'>"
                            + "".join(rows) + "</table>" + tail)
        self.report.verticalScrollBar().setValue(
            self.report.verticalScrollBar().maximum())

    def update_found(self, info):
        """A newer release exists: badge it in the footer, and remember it so
        the end of the next finished run can mention it too."""
        self._update = info
        self.footer.announce_update(info)
        # A run may already have finished before the check came back, in which
        # case the report is on screen and needs redrawing to pick this up.
        self.render_report()

    def _update_html(self):
        """The new-version line for the end of the run report, or nothing."""
        if not self._update:
            return ""
        notes = (self._update.get("notes") or "").strip()
        said = (f"<span style='color:{theme.TEXT}'> &mdash; {escape(notes)}</span>"
                if notes else "")
        return (f"<p style='margin-top:12px'>"
                f"<a href='{self._update['url']}' "
                f"style='color:{theme.UPDATE_BG};font-weight:bold'>"
                f"Version {escape(self._update['version'])} is available"
                f"</a>{said}</p>")

    def run_finished(self):
        tab = self.current_tab()
        if tab:
            tab.set_editable(True)
        # The report stays up after the run ends -- that is when it is read --
        # and the tab keeps the last run until the next one replaces it.
        self.left_stack.setTabText(1, "Last run")
        if getattr(self, "_stopped_early", False):
            self.note("stopped", "List stopped early")
        else:
            self.note("list", "List completed")
        if self.hotkey:
            self.hotkey.stop_watching()
            self.hotkey = None
        # set_running rather than touching the button directly: it also shows
        # the links strip and keeps the activity log up.  Setting the button
        # here instead left that method defined and never called, so neither
        # panel ever appeared.
        self.set_running(False)
        # Once more, now that the worker has actually stopped: the last note
        # fires while the thread is still winding down, so isRunning() is
        # still true and the closing line gets skipped.
        self.render_report()
        self.log_line("--- finished ---")


ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "art", "app_icon.png")


def main():
    start_session()
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet())
    # Two separate mechanisms, and setting only one leaves the other generic:
    # this is the WINDOW icon (title bar, and the taskbar entry while the
    # program runs).  The exe's own icon is embedded at build time instead.
    if os.path.exists(ICON):
        app.setWindowIcon(QIcon(ICON))
    w = Main()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
