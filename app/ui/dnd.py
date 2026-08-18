"""
Dragging tasks into a list, and out of it again.

Three ways in -- the Add button, a double-click on a chip, and dragging a chip
onto the list -- because people reach for different ones and none of them costs
anything to support.  Two ways out: drag the task off the list, or select it and
press Remove.

WHY THE DROP HANDLING IS WRITTEN OUT RATHER THAN LEFT TO QT
-----------------------------------------------------------
Qt's InternalMove reorders a list happily, but it cannot tell "dropped outside,
so delete it" from "dropped back where it started, so do nothing" -- both leave
the row count unchanged and both report success.  Distinguishing them is the
whole point of drag-to-remove, so the drag is run by hand and its landing place
inspected afterwards.

Everything travels as JSON on a private MIME type.  Plain text would mean a
task called "Task 1" dragged from a text editor would land in the run list.
"""

import json

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QDrag

MIME = "application/x-idleon-task"


def make_mime(task_name, params=None, source_row=-1):
    """Pack a task into drag data.  `source_row` is -1 when it came from the
    palette rather than from a list, which is how a drop tells an insertion
    from a reorder."""
    data = QMimeData()
    data.setData(MIME, json.dumps({
        "task": task_name,
        "params": params or {},
        "row": source_row,
    }).encode("utf-8"))
    return data


def read_mime(data):
    """Unpack drag data, or None if this is not ours."""
    if not data.hasFormat(MIME):
        return None
    try:
        return json.loads(bytes(data.data(MIME)).decode("utf-8"))
    except Exception:
        return None


def start_drag(widget, task_name, params=None, source_row=-1):
    """
    Run a drag and say where it landed.

    Returns the widget it was dropped on, or None if it was dropped on nothing
    -- which is what "dragged off the list" looks like, and what the caller
    uses to decide whether to delete the row.
    """
    drag = QDrag(widget)
    drag.setMimeData(make_mime(task_name, params, source_row))
    _attach_ghost(drag, task_name)
    # CopyAction only.  Offering MoveAction lets QAbstractItemView delete the
    # source row on its own once the drop is accepted -- which, combined with
    # the move this code already does, removed the task twice and emptied the
    # list.  Nothing here relies on Qt moving anything.
    drag.exec(Qt.CopyAction)
    return drag.target()


def _attach_ghost(drag, task_name):
    """
    Carry a small faded label of the task under the cursor.

    DRAWN, not photographed.  The first version grabbed the widget itself,
    which is the obvious thing and looks wrong in practice: a run-list row is
    the full width of the list and carries its selected-state fill, so dragging
    one produced a 400 px dark slab following the pointer.  A chip and a row
    also look nothing alike, so the same gesture had two different ghosts.

    Drawing one small pill gives both the same, sized to the words rather than
    to whatever widget the drag started from.  Nothing ships to support it --
    no artwork, and every class used here is already in the program.
    """
    try:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
        from PySide6.QtWidgets import QApplication

        from ui import theme

        # The application's own font, bolded -- the same thing the list delegate
        # does for a selected row, so the ghost is in the program's typeface
        # rather than whatever the platform default happens to be.
        font = QApplication.font()
        font.setBold(True)
        fm = QFontMetrics(font)
        pad_x, pad_y = 12, 6
        w = fm.horizontalAdvance(task_name) + pad_x * 2
        h = fm.height() + pad_y * 2

        ghost = QPixmap(w, h)
        ghost.fill(Qt.transparent)
        p = QPainter(ghost)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setOpacity(0.75)
        p.setBrush(QColor(theme.PANEL))
        p.setPen(QPen(QColor(theme.BORDER), 2))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2),
                          theme.RADIUS, theme.RADIUS)
        p.setPen(QColor(theme.TITLE))
        p.setFont(font)
        p.drawText(ghost.rect(), Qt.AlignCenter, task_name)
        p.end()

        drag.setPixmap(ghost)
        # Just below and right of the pointer, so it never covers the drop
        # placeholder the list draws to show where the task would land.
        drag.setHotSpot(ghost.rect().topLeft() + QPoint(-10, -6))
    except Exception:
        pass          # a missing ghost is cosmetic; never break the drag
