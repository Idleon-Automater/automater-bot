"""
A layout that wraps items onto the next line when they run out of room, and
the task palette built on it.

WHY NOT A LIST VIEW IN ICON MODE
--------------------------------
That was the first attempt, and it does wrap -- but it lays items out on a grid
whose column width is the widest item.  With chips of 150 to 198 px in a 334 px
panel two would have fitted side by side; instead Qt reserved 198 px per column
and fitted one.  Twenty tasks came out as eighteen rows, which is the problem
chips were meant to solve.

A flow layout packs each item at its own width and breaks the line only when
the next one will not fit, which is what "floating items" means here.
"""

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (QApplication, QLayout, QPushButton,
                               QSizePolicy, QWidget)

from ui.dnd import start_drag
from core.debuglog import log

from core import worlds
from ui import theme


class FlowLayout(QLayout):
    """Left to right, wrapping onto a new line when the width runs out."""

    def __init__(self, parent=None, margin=2, spacing=6, top=None):
        super().__init__(parent)
        self._items = []
        self._space = spacing
        self.setContentsMargins(margin, margin if top is None else top,
                                margin, margin)

    def __del__(self):
        while self.count():
            self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect, test_only):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            w = item.widget()
            if w is not None and w.isHidden():
                continue                      # filtered-out chips take no room
            hint = item.sizeHint()
            nxt = x + hint.width()
            if nxt > area.right() and line_height > 0:
                x = area.x()
                y = y + line_height + self._space
                nxt = x + hint.width()
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = nxt + self._space
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()


class TaskPalette(QWidget):
    """Every task as a chip, coloured by world, wrapped to the panel width."""

    selected = Signal(str)          # a chip was clicked
    activated = Signal(str)         # a chip was double-clicked: add it

    def __init__(self):
        super().__init__()
        self._chips = {}
        self._current = None
        # A little air above the first row, so the chips are not pressed
        # against the panel's top edge.
        self.flow = FlowLayout(self, margin=6, top=9)
        self.setLayout(self.flow)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

    def set_tasks(self, tasks):
        for chip in self._chips.values():
            chip.setParent(None)
        self._chips.clear()
        for t in tasks:
            self._add_chip(t.name, getattr(t, "world", 0), t.description)
        # Deliberately no initial selection: Add reads as available only once
        # a task has been picked, and auto-selecting the first one would make
        # it look ready before the user had chosen anything.

    def _add_chip(self, name, world, tip=""):
        b = _Chip(name, world)
        b.setToolTip(tip)
        b.clicked.connect(lambda _=False, n=name: self.select(n))
        b.doubleClicked.connect(lambda n=name: self.activated.emit(n))
        self.flow.addWidget(b)
        self._chips[name] = b

    def select(self, name):
        self._current = name
        for n, chip in self._chips.items():
            chip.setChecked(n == name)
        self.selected.emit(name)

    def current_name(self):
        return self._current

    def set_filter(self, chosen_worlds):
        """Show only these worlds; an empty set means show everything."""
        for chip in self._chips.values():
            chip.setVisible(not chosen_worlds or chip.world in chosen_worlds)
        self.flow.invalidate()
        self.updateGeometry()


class _Chip(QPushButton):
    doubleClicked = Signal()

    def __init__(self, text, world):
        super().__init__(text)
        self.world = world
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._press = None
        col, tint = worlds.colour(world), worlds.tint(world)
        # Bold in EVERY state.  Turning it on only for :checked widened the
        # text after the chip had been sized for the lighter font, so the
        # selected task was clipped at both ends -- the same mistake the
        # Remove button made, and it is invisible until something is selected.
        self.setStyleSheet(
            f"QPushButton {{ border: {theme.BORDER_WIDTH}px solid {col};"
            f" background: {tint}; color: {theme.TITLE};"
            f" font-weight: bold;"
            f" border-radius: {theme.RADIUS}px;"
            f" padding: 5px 14px; font-size: {theme.FS_CHIP}px; }}"
            f"QPushButton:hover {{ background: {worlds.tint(world, 0.78)}; }}"
            # Checked inverts rather than merely thickening: at chip size a
            # heavier border is easy to miss, and which task is about to be
            # added has to be unmistakable.
            f"QPushButton:checked {{ background: {col}; color: white; }}")

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Only past the drag threshold, or every click would become a drag and
        # single-clicking a chip to read its requirements would stop working.
        if not (event.buttons() & Qt.LeftButton) or self._press is None:
            return super().mouseMoveEvent(event)
        moved = (event.position().toPoint() - self._press).manhattanLength()
        if moved < QApplication.startDragDistance():
            return super().mouseMoveEvent(event)
        self.setDown(False)
        log("chip.drag.start", name=self.text())
        landed = start_drag(self, self.text())
        log("chip.drag.end", name=self.text(),
            landed=type(landed).__name__ if landed is not None else "None")
