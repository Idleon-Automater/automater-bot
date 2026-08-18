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

from PySide6.QtCore import QMimeData, Qt
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
    _attach_ghost(drag, widget)
    # CopyAction only.  Offering MoveAction lets QAbstractItemView delete the
    # source row on its own once the drop is accepted -- which, combined with
    # the move this code already does, removed the task twice and emptied the
    # list.  Nothing here relies on Qt moving anything.
    drag.exec(Qt.CopyAction)
    return drag.target()


def _attach_ghost(drag, widget):
    """
    Carry a faded copy of the dragged thing under the cursor.

    Qt already draws whatever pixmap a QDrag is given, so this is only about
    supplying one: the widget paints itself into an image, the image is
    composited at reduced opacity, and Qt does the rest.  No artwork ships, no
    library is added, and nothing changes if it fails -- a drag without a
    pixmap is exactly the drag that happened before.

    The hot spot is where the pointer was inside the widget, so the ghost sits
    under the cursor at the same place the real one did rather than snapping a
    corner to it.
    """
    try:
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QCursor, QPainter, QPixmap

        # A list hands its whole self to start_drag, so grabbing the widget
        # would photograph every row.  When it is an item view, photograph just
        # the row being dragged -- worked out here rather than by giving
        # start_drag another argument, so no caller has to know about ghosts.
        item = (widget.currentItem() if hasattr(widget, "currentItem")
                else None)
        if item is not None and hasattr(widget, "visualItemRect"):
            rect = widget.visualItemRect(item)
            src = widget.viewport().grab(rect)
        else:
            src = widget.grab()
        if src.isNull() or src.width() < 2 or src.height() < 2:
            return
        ghost = QPixmap(src.size())
        ghost.fill(Qt.transparent)
        p = QPainter(ghost)
        p.setOpacity(0.6)
        p.drawPixmap(0, 0, src)
        p.end()
        drag.setPixmap(ghost)

        spot = widget.mapFromGlobal(QCursor.pos())
        if widget.rect().contains(spot):
            drag.setHotSpot(spot)
        else:
            drag.setHotSpot(QPoint(src.width() // 2, src.height() // 2))
    except Exception:
        pass          # a missing ghost is cosmetic; never break the drag
