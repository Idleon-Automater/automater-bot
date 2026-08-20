"""
One tab: a run list you can build, name, and save.

Each tab owns an ordered list of entries, and each entry is a task name plus
the settings chosen for it.  Two entries can be the same task with different
settings -- "Sushi for 30 minutes" early and "Sushi until I stop it" at the end
is the whole point -- so an entry carries its own settings rather than pointing
at a shared task object.

WHY THE ENTRY STORES SETTINGS, NOT A TASK
-----------------------------------------
Tasks are built fresh at run time from (name, settings).  Keeping live task
objects in the list would mean the thing being edited is also the thing being
run, and a half-edited setting would reach the game.  Storing plain data and
building on demand keeps editing and running apart.
"""

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QIcon, QPainter, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem,
                               QPushButton, QToolButton, QVBoxLayout,
                               QWidget)

from ui.dnd import make_mime, read_mime, start_drag
from core import registry, tasklists
from core.debuglog import log
from ui.metro import (ASLEEP, DONE, PENDING, PROBLEM, RUNNING, MetroDelegate)
from ui import theme

ENTRY_ROLE = Qt.UserRole + 1
STATE_ROLE = Qt.UserRole + 2
WORLD_ROLE = Qt.UserRole + 3
TITLE_ROLE = Qt.UserRole + 4
PARAMS_ROLE = Qt.UserRole + 5
# The countdown shown on a task that has nothing to do yet, or None.  Held on
# the item rather than asked for while painting: paint runs on every repaint
# and asking the save file there would read it hundreds of times a second.
ASLEEP_ROLE = Qt.UserRole + 6


def pencil_icon(size=15, colour="#5a5a5a"):
    """
    A small pencil, drawn rather than loaded.

    Drawing it keeps the program to a single file with no artwork to ship, no
    icon that has to survive the release allowlist, and nothing to go missing
    from the executable.
    """
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(colour)
    p.setPen(QPen(c, 1.6))
    # the shaft, corner to corner
    p.drawLine(size * 0.28, size * 0.72, size * 0.72, size * 0.28)
    # the tip
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(size * 0.16, size * 0.84),
                             QPointF(size * 0.34, size * 0.78),
                             QPointF(size * 0.22, size * 0.66)]))
    # the eraser end
    p.drawLine(size * 0.66, size * 0.22, size * 0.80, size * 0.36)
    p.end()
    return QIcon(pix)


def cross_icon(size=12, colour="#8a5f5a"):
    """A small x, drawn to match the pencil rather than shipped as a file."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor(colour), 1.8))
    m = size * 0.26
    p.drawLine(m, m, size - m, size - m)
    p.drawLine(size - m, m, m, size - m)
    p.end()
    return QIcon(pix)


class TaskQueue(QListWidget):
    """
    The run list itself: accepts chips dropped in, and deletes rows dragged out.

    Drop handling is written out rather than left to InternalMove because Qt
    cannot distinguish "dropped outside" from "dropped back where it was" --
    see ui/dnd.py.
    """

    changed = Signal()

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        # COPY, never MOVE -- and this is the whole reason the first version
        # emptied the list.  On a MoveAction, QAbstractItemView removes the
        # source rows itself once the drop is accepted, on top of the move this
        # class had already performed by hand.  The row got moved and then
        # deleted, leaving a blank list and dangling items that crashed the run.
        # Copy semantics mean Qt adds and removes nothing; every change here is
        # explicit.
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDragDropMode(QListWidget.DragDrop)
        self._dropped_on_me = False

    def startDrag(self, actions):
        item = self.currentItem()
        if item is None:
            return
        entry = item.data(ENTRY_ROLE)

        # Whether the drop landed on this list is recorded by dropEvent rather
        # than read from drag.target() afterwards.  target() was the first
        # approach and it is not dependable -- it can come back empty for a
        # drop that did land here, which made the code delete a row the user
        # had only reordered.
        self._dropped_on_me = False
        start_drag(self, entry["task"], entry["params"], self.row(item))
        dropped_here = self._dropped_on_me
        self._dropped_on_me = False

        # Qt hides the row being dragged for the duration of the drag and does
        # not always put it back -- which is why the list looked blank even
        # when every task was still in it.  Unhide everything unconditionally;
        # nothing in this list is ever legitimately hidden.
        self.unhide_all()
        self.set_drop_row(-1)
        self.end_drag_state()

        if not dropped_here:
            row = self.row(item)        # may have shifted if it was reordered
            if row >= 0:
                self.takeItem(row)
                self.changed.emit()
        self.viewport().update()

    def end_drag_state(self):
        """
        Put the view back to rest after a drop.

        QAbstractItemView.dropEvent does this itself -- resets the state,
        stops the auto-scroll timer, clears the drop indicator -- and this
        class overrides dropEvent without calling super(), so none of it
        happened.  The view stayed in DraggingState and stopped painting its
        rows: the list looked empty while the debug log showed all ten tasks
        present and none hidden.  That is what made the bug so hard to place --
        every check of the data said the data was fine, because it was.
        """
        self.setState(QAbstractItemView.NoState)
        self.stopAutoScroll()
        self.setDropIndicatorShown(True)
        self.viewport().update()

    def unhide_all(self):
        """
        Undo Qt's mid-drag hiding of the dragged row.

        Qt hides the row being dragged and restores it when the drag ends --
        except when the drag ends in a way it did not expect, and then the row
        stays hidden and the list looks like it emptied itself.  This is called
        from every path a drag can finish through, because the cost is a loop
        over a handful of rows and the symptom is a list that appears to have
        thrown away the user's work.
        """
        changed = False
        for i in range(self.count()):
            if self.item(i).isHidden():
                self.item(i).setHidden(False)
                changed = True
        if changed:
            self.viewport().update()

    def dragLeaveEvent(self, event):
        log("queue.dragLeave", rows=self.count(),
            hidden=sum(self.item(i).isHidden() for i in range(self.count())))
        self.unhide_all()
        self.set_drop_row(-1)
        self.end_drag_state()
        super().dragLeaveEvent(event)

    def dragEnterEvent(self, event):
        payload = read_mime(event.mimeData())
        log("queue.dragEnter", ours=payload is not None,
            formats=",".join(event.mimeData().formats())[:70], rows=self.count())
        if payload is None:
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def dragMoveEvent(self, event):
        if read_mime(event.mimeData()) is None:
            event.ignore()
            return
        row = self.indexAt(event.position().toPoint()).row()
        # Below the last task counts as 'at the end' rather than 'nowhere',
        # so the placeholder still appears when adding to a short list.
        self.set_drop_row(self.count() if row < 0 else row)
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def set_drop_row(self, row):
        """
        Open a gap above `row` so the tasks below appear to part.

        Relaying out on every mouse move would be wasteful and would flicker,
        so it only happens when the target row actually changes.
        """
        d = self.itemDelegate()
        if getattr(d, "drop_row", -1) == row:
            return
        d.drop_row = row
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    def dropEvent(self, event):
        payload = read_mime(event.mimeData())
        log("queue.drop.in", ours=payload is not None, rows=self.count(),
            payload=str(payload)[:80])
        if payload is None:
            event.ignore()
            return
        target = self.indexAt(event.position().toPoint()).row()
        if target < 0:
            target = self.count()
        self._dropped_on_me = True      # read by startDrag when the drag ends
        # The row number alone decides move-vs-add: only a drag that began in a
        # run list carries one, and chips always send -1.  This used to also
        # test event.source(), which is another piece of Qt state to be wrong
        # about -- when it did not come back as expected, a reorder silently
        # became a second copy of the task.
        if payload.get("row", -1) >= 0:
            self.tab.move_entry(payload["row"], target)
        else:
            self.tab.add(payload["task"], payload.get("params"), at=target)
        # Copy, so Qt does not then delete the row we just placed.
        event.setDropAction(Qt.CopyAction)
        event.accept()
        self.unhide_all()
        self.set_drop_row(-1)
        self.end_drag_state()
        log("queue.drop.out", rows=self.count(),
            hidden=sum(self.item(i).isHidden() for i in range(self.count())),
            texts="|".join(self.item(i).text()[:12] for i in range(self.count())))
        self.changed.emit()


class ListTab(QWidget):
    """A named, reorderable run list."""

    entries_changed = Signal()
    selection_changed = Signal()

    def __init__(self, name="New list", entries=None):
        super().__init__()
        self.list_name = name

        self.queue = TaskQueue(self)
        self.queue.changed.connect(self.entries_changed.emit)
        self.queue.setItemDelegate(MetroDelegate(
            STATE_ROLE, WORLD_ROLE, title_role=TITLE_ROLE,
            asleep_role=ASLEEP_ROLE, params_role=PARAMS_ROLE,
            parent=self.queue))
        # Tall enough that a long list is readable without scrolling, which is
        # the point of showing the run as a line rather than a log.
        # Low enough that the whole panel still fits when the window is
        # made short.  It was 340, which is taller than the space available
        # on a small window -- the layout then overflowed and the list's
        # bottom border and the row of buttons were pushed off the edge.
        self.queue.setMinimumHeight(120)
        self.queue.setSpacing(0)

        self.queue.currentItemChanged.connect(
            lambda *_: self.selection_changed.emit())
        self.queue.model().rowsMoved.connect(
            lambda *_: self.entries_changed.emit())
        self.queue.model().rowsRemoved.connect(
            lambda *_: self.entries_changed.emit())

        # No name field in here any more: the tab above already shows the
        # name, and a second copy of it inside the panel was both redundant and
        # the thing that kept catching stray keystrokes.  Renaming happens on
        # the tab itself -- double-click it, or use the pencil beside it.
        self.remove = QPushButton("Remove task")
        self.remove.clicked.connect(self.remove_selected)
        # Washed-out red until a task is selected, and bright only under the
        # pointer: removing is destructive, so it should look available without
        # looking inviting.
        # Bold in EVERY state on purpose.  Turning bold on only for :hover
        # widened the text after the button had already been sized for the
        # lighter font, so the label was clipped at both ends the moment the
        # pointer touched it.  Only the colours change now.
        self.remove.setStyleSheet(
            "QPushButton { background: #fbe6e6; color: #8a4b4b;"
            " font-weight: bold; border: 1px solid #edc9c9;"
            " border-radius: 4px; padding: 5px 16px; }"
            "QPushButton:hover:enabled { background: #dc2626; color: white;"
            " border: 1px solid #b91c1c; }"
            "QPushButton:disabled { background: #f2f2f2; color: #b0b0b0;"
            " border: 1px solid #e2e2e2; }")
        self.remove.setEnabled(False)
        self.queue.currentItemChanged.connect(
            lambda *_: self.remove.setEnabled(
                self.queue.currentItem() is not None
                and self.queue.dragEnabled()))

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #b36b00; background: transparent;")

        # The estimate sits with the list it is about, under its own buttons,
        # rather than at the bottom of the window beside Run.
        self.eta = QLabel("Nothing queued")
        self.eta.setStyleSheet(f"color: {theme.MUTED}; background: transparent;"
                               f"font-size: {theme.FS_SMALL}px;")

        self.save_btn = QPushButton("Save list")
        self.save_btn.clicked.connect(self.save)
        row = QHBoxLayout(); row.addWidget(self.remove); row.addWidget(self.save_btn)
        row.addStretch()

        box = QVBoxLayout()
        box.addWidget(QLabel("Runs top to bottom -- drag to reorder"))
        box.addWidget(self.queue)
        box.addLayout(row)
        box.addWidget(self.eta)
        box.addWidget(self.warning)
        self.setLayout(box)

        for e in (entries or []):
            self.add(e.get("task"), e.get("params") or {})
        # A list loaded from disk starts saved; a new empty one has nothing
        # worth saving yet, so both begin clean.
        self.set_dirty(False)
        self.entries_changed.connect(lambda: self.set_dirty(True))

    # ---- entries ---------------------------------------------------------

    def add(self, task_name, params=None, at=None):
        if self.queue.dragEnabled():            # not mid-run
            self.forget_run()
        task = registry.make_task(task_name, params or {})
        log("tab.add", name=str(task_name), at=str(at),
            built=task is not None, rows_before=self.queue.count())
        if task is None:
            return                      # a saved list naming a removed task
        item = QListWidgetItem()
        item.setData(ENTRY_ROLE, {"task": task_name,
                                  "params": params or task.settings()})
        item.setData(STATE_ROLE, PENDING)
        item.setData(WORLD_ROLE, getattr(task, "world", 0))
        item.setData(ASLEEP_ROLE, self._asleep_for(task))
        if at is None or at >= self.queue.count():
            self.queue.addItem(item)
        else:
            self.queue.insertItem(at, item)
        self._relabel(item)
        log("tab.add.done", rows_after=self.queue.count(),
            text=item.text()[:30])
        self.entries_changed.emit()

    def move_entry(self, src, dst):
        """Reorder within the list, keeping the dragged row selected."""
        if self.queue.dragEnabled():
            self.forget_run()
        if src == dst or src < 0 or src >= self.queue.count():
            return
        item = self.queue.takeItem(src)
        if dst > src:
            dst -= 1                    # the list shrank when we took the row
        self.queue.insertItem(min(dst, self.queue.count()), item)
        self.queue.setCurrentItem(item)
        self.entries_changed.emit()

    def remove_selected(self):
        if self.queue.currentItem() is None:
            return
        self.forget_run()
        for it in self.queue.selectedItems():
            self.queue.takeItem(self.queue.row(it))
        self.entries_changed.emit()

    def entries(self):
        return [self.queue.item(i).data(ENTRY_ROLE)
                for i in range(self.queue.count())]

    def tasks(self):
        """Built tasks, in order.  Entries naming a gone task are dropped."""
        out = []
        for e in self.entries():
            t = registry.make_task(e["task"], e["params"])
            if t is not None:
                out.append(t)
        return out

    def current_task(self):
        it = self.queue.currentItem()
        if it is None:
            return None
        e = it.data(ENTRY_ROLE)
        return registry.make_task(e["task"], e["params"])

    def update_current(self, params):
        """Store edited settings back onto the selected entry."""
        it = self.queue.currentItem()
        if it is None:
            return
        e = dict(it.data(ENTRY_ROLE))
        e["params"] = params
        it.setData(ENTRY_ROLE, e)
        self._relabel(it)
        self.entries_changed.emit()

    # ---- run state (drives the metro line) -------------------------------

    @staticmethod
    def _asleep_for(task):
        """The task's own countdown, or None.  Never raises."""
        try:
            return task.asleep()
        except Exception:
            return None               # a broken check must not blank the list

    def refresh_asleep(self):
        """
        Re-ask every task whether it has anything to do.

        Called on a timer, so a countdown in the list ticks down while it is
        being looked at, and a task that comes due stops being faded without
        the window having to be reopened.  Only touches rows whose answer
        changed: setData on every row every minute repaints the whole list.
        """
        changed = False
        for i in range(self.queue.count()):
            item = self.queue.item(i)
            entry = item.data(ENTRY_ROLE) or {}
            task = registry.make_task(entry.get("task"), entry.get("params") or {})
            if task is None:
                continue
            now = self._asleep_for(task)
            if now != item.data(ASLEEP_ROLE):
                item.setData(ASLEEP_ROLE, now)
                changed = True
        if changed:
            self.queue.viewport().update()

    def set_state(self, row, state):
        item = self.queue.item(row)
        if item is not None:
            item.setData(STATE_ROLE, state)
            self.show_chain(True)
            self.queue.viewport().update()

    def reset_states(self):
        for i in range(self.queue.count()):
            self.queue.item(i).setData(STATE_ROLE, PENDING)
        self.queue.viewport().update()

    def show_chain(self, on):
        """
        Show or hide the metro line.

        A finished run leaves its dots behind, which is right while you are
        reading the result and wrong the moment you go back to building: a list
        of green dots beside tasks that have not run again is a list lying
        about itself.  So editing clears it, and the cards take back the space
        the gutter was using.
        """
        d = self.queue.itemDelegate()
        if getattr(d, "show_metro", None) != on:
            d.show_metro = on
            self.queue.scheduleDelayedItemsLayout()
            self.queue.viewport().update()

    def forget_run(self):
        """Back to a plain editable list: no dots, no line."""
        self.reset_states()
        self.show_chain(False)

    def set_editable(self, editable):
        """
        Lock the list while it runs.

        Reordering a list mid-run would leave the metro line pointing at rows
        that have moved, and the runner holds tasks it already built -- so the
        display and the run would disagree about what is happening.
        """
        self.queue.setDragEnabled(editable)
        self.queue.setAcceptDrops(editable)
        self.remove.setEnabled(editable and self.queue.currentItem() is not None)

    def _relabel(self, item):
        """
        Row text that says what will actually happen, settings included.

        The name and the settings are stored separately as well as combined,
        so the delegate can draw the name bold and the settings quiet.  Kept as
        two roles rather than split back out of one string later: task names
        are free text and will eventually contain a dash.
        """
        e = item.data(ENTRY_ROLE)
        task = registry.make_task(e["task"], e["params"])
        if task is None:
            item.setData(TITLE_ROLE, e["task"])
            item.setData(PARAMS_ROLE, "")
            item.setText(e["task"])
            return
        # Only the settings worth reading at a glance: a limit that is set, a
        # switch that is on, or the absence of a limit where that is the point.
        bits = []
        for p in task.params:
            v = getattr(task, p.name, p.default)
            if p.kind == "bool":
                if v:
                    bits.append(p.label.lower())
            elif v is None:
                if p.governs_endless:
                    bits.append("until stopped")
            elif p.kind == "minutes":
                bits.append(f"{int(v)} min")
            else:
                bits.append(f"{p.label.lower()} {v}{p.unit}")
        params = ", ".join(bits)
        item.setData(TITLE_ROLE, task.name)
        item.setData(PARAMS_ROLE, params)
        # The plain text stays in sync for tooltips and accessibility, even
        # though the delegate draws the two halves itself.
        item.setText(f"{task.name}" + (f"  -  {params}" if params else ""))

    # ---- validity and saving --------------------------------------------

    def normalise_endless(self):
        """
        Only the last task may run forever; the rest get their limit back.

        Enforcing it beats warning about it.  A list with an endless task in
        the middle is never what someone meant -- everything after it would sit
        there for good -- so instead of explaining that, the setting is simply
        put back to its default on any task that is not last, and moving a task
        to the end is what unlocks it.
        """
        n = self.queue.count()
        changed = False
        for i in range(n):
            item = self.queue.item(i)
            e = dict(item.data(ENTRY_ROLE))
            task = registry.make_task(e["task"], e["params"])
            if task is None or i == n - 1 or not task.runs_forever:
                continue
            params = dict(e["params"])
            for p in task.params:
                if p.governs_endless:
                    params[p.name] = False if p.kind == "bool" else p.default
            e["params"] = params
            item.setData(ENTRY_ROLE, e)
            self._relabel(item)
            changed = True
        return changed

    def refresh_warning(self):
        self.normalise_endless()
        issues = tasklists.problems(self.tasks())
        self.warning.setText("  ".join(issues))
        return issues

    def rename_to(self, new_name):
        """Rename this list, carrying any saved copy with it."""
        new_name = (new_name or "").strip()
        if not new_name or new_name == self.list_name:
            return False
        if self.list_name in tasklists.names():
            tasklists.rename(self.list_name, new_name)
        self.list_name = new_name
        self.entries_changed.emit()
        return True

    def set_dirty(self, dirty):
        """
        Colour the Save button by whether there is anything to save.

        Bright green means unsaved work; a washed-out green means the list on
        screen is the list on disk.  The button is always there either way --
        greying it out would answer "can I press this" when the question people
        actually have is "did I remember to".
        """
        self._dirty = dirty
        if dirty:
            self.save_btn.setStyleSheet(
                "QPushButton { background: #16a34a; color: white;"
                " font-weight: bold; border: 1px solid #0f7a37;"
                f" border-radius: {theme.RADIUS}px; padding: 5px 14px; }}"
                "QPushButton:hover { background: #12833c; }")
            self.save_btn.setToolTip("This list has unsaved changes")
        else:
            self.save_btn.setStyleSheet(
                "QPushButton { background: #dff0e3; color: #4a6b52;"
                " border: 1px solid #bcd8c4;"
                " border-radius: 4px; padding: 5px 14px; }")
            self.save_btn.setToolTip("Saved -- no changes since")

    def save(self):
        tasklists.save(self.list_name, self.entries())
        self.entries_changed.emit()
        # After the signal, because entries_changed is what marks it dirty.
        self.set_dirty(False)
