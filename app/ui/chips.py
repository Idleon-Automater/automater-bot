"""
The task palette: every task as a chip, wrapped into rows.

One row per task stops working somewhere around a dozen tasks -- the list turns
into a scroll, and picking the one you want means reading rather than looking.
Chips wrap to the width available, so twenty tasks fit in the space five rows
used to take, and each carries its world's colour so the palette is sorted by
place before you have read a single name.

The wrapping itself is Qt's: a list view in icon mode with wrapping on already
flows items left to right and reflows them when the panel is resized.  Writing
a custom flow layout to do the same would be more code and would lose keyboard
selection.  What this file adds is only the drawing.
"""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from core import worlds


class ChipDelegate(QStyledItemDelegate):
    """A rounded, world-coloured chip that sizes itself to its text."""

    H_PAD = 12
    V_PAD = 7
    GAP = 6

    def __init__(self, world_role, parent=None):
        super().__init__(parent)
        self.world_role = world_role

    def sizeHint(self, option, index):
        fm = QFontMetrics(option.font)
        text = str(index.data(Qt.DisplayRole) or "")
        return QSize(fm.horizontalAdvance(text) + self.H_PAD * 2 + self.GAP,
                     fm.height() + self.V_PAD * 2 + self.GAP)

    def paint(self, painter, option, index):
        world = index.data(self.world_role) or 0
        border = QColor(worlds.colour(world))
        fill = QColor(worlds.tint(world))
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        r = QRectF(option.rect).adjusted(0, 0, -self.GAP, -self.GAP)

        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        if selected:
            # Selected chips invert rather than merely thicken: at chip size a
            # heavier border is easy to miss, and which task is about to be
            # added has to be unmistakable.
            painter.setBrush(QBrush(border))
            painter.setPen(QPen(border, 1.5))
        else:
            painter.setBrush(QBrush(fill.lighter(104) if hovered else fill))
            painter.setPen(QPen(border, 1.4))
        painter.drawRoundedRect(r, r.height() / 2.2, r.height() / 2.2)

        painter.setPen(QPen(QColor("#ffffff") if selected
                            else QColor("#1a1a1a")))
        font = painter.font()
        font.setBold(selected)
        painter.setFont(font)
        painter.drawText(r, Qt.AlignCenter, str(index.data(Qt.DisplayRole) or ""))
        painter.restore()
