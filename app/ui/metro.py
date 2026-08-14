"""
The metro line down the side of a run list.

Each task gets a dot, joined to the next by a line, so the shape of the whole
run is visible without reading a word of it: what is finished, what is running,
what has not started, and whether anything went wrong.

    o  Swishy Hoops      green   done
    |
    o  Sushi Station     orange  running now
    |
    o  Throwy Darts      grey    not started

WHY A LINE AND NOT A PROGRESS BAR
---------------------------------
A bar says how far along one thing is.  A run list is several things, of very
different lengths, one of which may never end -- so a bar would either lie
about the total or refuse to fill.  A line makes no claim about duration; it
only says where the run has got to, which is the question actually being asked
by someone glancing at the window from across the room.

The segment ABOVE a dot is coloured by the task before it, so completing a task
visibly extends the line downward into the next one.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from core import worlds
from ui import theme

# Item states, stored on the item and read back when painting.
PENDING = "pending"
RUNNING = "running"
DONE = "done"
PROBLEM = "problem"

COLOURS = {
    PENDING: QColor("#9aa0a6"),     # grey: not started
    RUNNING: QColor("#f59e0b"),     # orange: happening now
    DONE:    QColor("#16a34a"),     # green: finished cleanly
    # Red is for a task that was skipped or failed.  It is deliberately as loud
    # as the others: coming back to a finished run, "one of these did not
    # happen" is the single most important thing to see.
    PROBLEM: QColor("#dc2626"),
}


class MetroDelegate(QStyledItemDelegate):
    """Draws the dot-and-line gutter, then the row's normal contents."""

    GUTTER = 30          # width reserved on the left for the line
    RADIUS = 5
    # 2 px above and below each card, so neighbours sit 4 px apart while the
    # card itself stays as short as its text allows.  Kept deliberately tight:
    # the taller each row, the fewer links of the chain fit on screen, and
    # seeing the whole run at once is the reason the chain exists.
    PAD = theme.ROW_GAP // 2
    EXTRA_HEIGHT = theme.ROW_GAP       # the gap, split above and below
    CARD_HEIGHT = theme.ROW_HEIGHT     # asked for directly, not left to the font

    def __init__(self, state_role, world_role, title_role=None,
                 params_role=None, parent=None):
        super().__init__(parent)
        self.state_role = state_role
        self.world_role = world_role
        # The task's name and its settings are drawn separately so the name can
        # be bold and the settings quiet.  Stored as two roles rather than
        # parsed back out of one string: the settings text is generated and
        # will change, and splitting on a separator would break the first time
        # a task name contained one.
        self.title_role = title_role
        self.params_role = params_role
        # The chain is only drawn once something has actually run.  While a
        # list is being built there is no progress to show, and leaving a line
        # of grey dots beside an idle list makes a finished run and an
        # untouched one look the same.
        self.show_metro = False
        # Row a drag would insert above, or -1.  Its row is made taller so the
        # tasks below appear to part, which says where the task will land
        # without a separate indicator line to interpret.
        self.drop_row = -1
        # Height of one row, learned from the first sizeHint.  The drop
        # placeholder is exactly this tall, so the gap that opens is precisely
        # the space the dragged task will occupy -- a placeholder of some other
        # size promises a slot that is not the one you get.
        self._row_h = 22

    def _state(self, index):
        return index.data(self.state_role) or PENDING

    def paint(self, painter, option, index):
        # Each task is a card floating on the background rather than a row in a
        # table: it carries its world's colour as a border and a pale wash of
        # the same colour behind the text, so a list that alternates between
        # World 1 and World 7 reads as alternating colours -- which is a list
        # that will spend its time travelling.
        world = index.data(self.world_role) or 0
        border = QColor(worlds.colour(world))
        fill = QColor(worlds.tint(world))
        gutter = self.GUTTER if self.show_metro else 6

        r = option.rect
        rows = index.model().rowCount()
        gap_above = index.row() == self.drop_row
        # `drop_row == rows` means "after the last task"; there is no row of
        # its own to grow, so the last row grows downward instead.
        gap_below = self.drop_row >= rows and index.row() == rows - 1

        top = r.top() + self.PAD + (self._row_h if gap_above else 0)
        bottom_pad = self.PAD + (self._row_h if gap_below else 0)
        card = QRectF(r.left() + gutter, top,
                      r.width() - gutter - self.PAD * 2,
                      r.height() - (top - r.top()) - bottom_pad)

        if gap_above or gap_below:
            slot = QRectF(r.left() + gutter,
                          r.top() + self.PAD if gap_above
                          else r.bottom() - self._row_h + self.PAD,
                          r.width() - gutter - self.PAD * 2,
                          self._row_h - self.PAD * 2)
            painter.save()
            painter.setRenderHint(painter.RenderHint.Antialiasing, True)
            pen = QPen(QColor("#1f6feb"), 1.6, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor("#eaf2ff")))
            painter.drawRoundedRect(slot, theme.CARD_RADIUS,
                                    theme.CARD_RADIUS)
            painter.restore()

        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        selected = bool(option.state & QStyle.State_Selected)
        # Selected fills with the world's own colour rather than merely
        # thickening its border: at this row height a heavier outline is easy
        # to miss, and which task the settings panel is editing has to be
        # unmistakable.
        painter.setBrush(QBrush(border if selected else fill))
        painter.setPen(QPen(border, 1.4))
        painter.drawRoundedRect(card, theme.CARD_RADIUS,
                                theme.CARD_RADIUS)

        title = index.data(self.title_role) if self.title_role else None
        params = index.data(self.params_role) if self.params_role else None
        text_area = card.adjusted(10, 0, -8, 0)

        name_col = QColor("#ffffff") if selected else QColor(theme.TITLE)
        detail_col = QColor("#f0e8e4") if selected else QColor(theme.MUTED)

        if title:
            # Name in bold, settings after a dash in a lighter weight, so the
            # eye finds the task first and the detail second.
            font = painter.font()
            font.setPixelSize(theme.FS_TASK)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(name_col))
            painter.drawText(text_area, Qt.AlignVCenter | Qt.AlignLeft, title)
            if params:
                used = QFontMetrics(font).horizontalAdvance(title)
                font.setBold(False)
                painter.setFont(font)
                painter.setPen(QPen(detail_col))
                painter.drawText(text_area.adjusted(used, 0, 0, 0),
                                 Qt.AlignVCenter | Qt.AlignLeft,
                                 "  -  " + params)
        else:
            font = painter.font()
            font.setBold(selected)
            painter.setFont(font)
            painter.setPen(QPen(name_col))
            painter.drawText(text_area, Qt.AlignVCenter | Qt.AlignLeft,
                             str(index.data(Qt.DisplayRole) or ""))
        painter.restore()

        if not self.show_metro:
            return

        state = self._state(index)
        colour = COLOURS.get(state, COLOURS[PENDING])
        model = index.model()
        row, last = index.row(), model.rowCount() - 1

        # The segment above is owned by the previous task -- but only a task
        # that has FINISHED extends the line into the next one.  Colouring it
        # by the previous state outright meant a running task painted the line
        # onward to the task after it, so the chain claimed to have reached
        # somewhere the run had not got to yet.  Done and problem both count as
        # finished: the run moved on either way.
        above = COLOURS[PENDING]
        if row > 0:
            prev = model.index(row - 1, 0).data(self.state_role) or PENDING
            if prev in (DONE, PROBLEM):
                above = COLOURS[prev]

        r = option.rect
        cx = r.left() + self.GUTTER // 2
        cy = r.center().y() + 1

        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        if row > 0:
            painter.setPen(QPen(above, 2))
            painter.drawLine(cx, r.top(), cx, cy - self.RADIUS)
        if row < last:
            # Below the dot the line belongs to this task, and it fills once
            # this task has FINISHED -- the same test the segment above uses.
            # These two disagreed at first: below tested `== DONE` while above
            # tested done-or-problem, so a failed task left half a segment
            # unpainted and the chain looked broken at its first link.
            done_here = state in (DONE, PROBLEM)
            painter.setPen(QPen(colour if done_here else COLOURS[PENDING], 2))
            painter.drawLine(cx, cy + self.RADIUS, cx, r.bottom())

        painter.setPen(QPen(colour, 2))
        painter.setBrush(QBrush(colour if state != PENDING
                                else QColor("#ffffff")))
        painter.drawEllipse(QRectF(cx - self.RADIUS, cy - self.RADIUS,
                                   self.RADIUS * 2, self.RADIUS * 2))
        # A running task gets a ring, so it is distinguishable from a finished
        # one without relying on colour alone.
        if state == RUNNING:
            painter.setPen(QPen(colour, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(cx - self.RADIUS - 3, cy - self.RADIUS - 3,
                                       (self.RADIUS + 3) * 2, (self.RADIUS + 3) * 2))
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        # The card height is set, not inferred from the font: the theme lab
        # asks for a task height directly, and letting the type size decide it
        # would mean changing one silently changed the other.
        size.setHeight(max(size.height(), self.CARD_HEIGHT))
        self._row_h = size.height() + self.EXTRA_HEIGHT
        extra = self.EXTRA_HEIGHT
        rows = index.model().rowCount()
        if index.row() == self.drop_row or (self.drop_row >= rows
                                            and index.row() == rows - 1):
            extra += self._row_h
        size.setHeight(size.height() + extra)
        return size
