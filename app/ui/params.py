"""
The settings panel for one queued task.

Built from the task's own `params` list rather than from a form written per
task, so a new task arrives with its settings already editable and this file
never learns what a "max score" is.

UNLIMITED IS A CHECKBOX, NOT A MAGIC NUMBER
-------------------------------------------
Settings that can be switched off get a tick-box that greys out the spinner.
The alternative -- 0, or blank, or the maximum meaning "no limit" -- is the
kind of thing a user has to be told, and would leave "stop at score 0" looking
like a valid choice.  Ticking a box that says "no limit" cannot be misread.
"""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QLabel, QSpinBox, QVBoxLayout,
                               QWidget)

from core import worlds
from ui import theme

# Options that begin "W3 - " belong to a world, and are tinted and grouped by
# it.  The fighting task offers 141 maps in one list, which without this is a
# wall of names where finding World 5 means reading a hundred lines of World 1
# to 4 first.  The tint is the same one the run list paints behind a task from
# that world, so the two places agree about what World 5 looks like.
#
# A heuristic on the text rather than something declared on the Param: nothing
# else in the program needs it, and a list whose options do not carry a world
# simply gets the plain combo box it had before.
_WORLD_OPTION = re.compile(r"^W([1-7]) - ")


def _fill_choices(box, choices):
    """Add the options, tinted and separated by world where they say one."""
    last = None
    for choice in choices:
        text = str(choice)
        m = _WORLD_OPTION.match(text)
        world = int(m.group(1)) if m else None
        if world is not None and last is not None and world != last:
            box.insertSeparator(box.count())
        box.addItem(text)
        if world is not None:
            i = box.count() - 1
            box.setItemData(i, QBrush(QColor(worlds.tint(world, 0.72))),
                            Qt.BackgroundRole)
            box.setItemData(i, QBrush(QColor(theme.TITLE)),
                            Qt.ForegroundRole)
        last = world


class ParamEditor(QWidget):
    """Editors for one task's settings.  Emits `changed` on every edit."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self._rows = {}
        self._task = None
        self._is_last = True
        self.form = QFormLayout()
        self.title = QLabel("Select a task in the list to set it up")
        self.title.setWordWrap(True)
        box = QVBoxLayout()
        box.addWidget(self.title)
        box.addLayout(self.form)
        box.addStretch()
        self.setLayout(box)

    def clear(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self._rows.clear()
        self._task = None

    def show_task(self, task, is_last=True):
        """
        Rebuild the form for `task`, seeded with its current values.

        `is_last` gates the run-forever switches: a task in the middle of
        a list cannot be endless, because nothing after it would ever
        start.  Greying the control and saying why is clearer than
        letting it be set and then warning about it afterwards.
        """
        self.clear()
        self._task = task
        self._is_last = is_last
        if task is None:
            self.title.setText("Select a task in the list to set it up")
            return
        if not task.params:
            self.title.setText(f"{task.name} has nothing to set up.")
            return
        self.title.setText(f"<b>{task.name}</b>")

        for p in task.params:
            value = getattr(task, p.name, p.default)
            endless_blocked = p.governs_endless and not is_last
            if p.kind == "bool":
                w = QCheckBox()
                w.setChecked(bool(value) and not endless_blocked)
                w.stateChanged.connect(self._emit)
                self._rows[p.name] = (p, w, None)
                self.form.addRow(p.label, w)
                # The greyed-out explanation wins over the general help: when a
                # control cannot be used, why is the only question being asked.
                w.setToolTip(p.help or "")
                if endless_blocked:
                    w.setEnabled(False)
                    w.setToolTip("Only the last task in a list can run forever")
                continue

            if p.kind == "choice":
                w = QComboBox()
                _fill_choices(w, p.choices)
                # Select by text, not by index: a saved list stores the option
                # the user picked, so it survives the choices being reordered.
                i = w.findText(str(value))
                w.setCurrentIndex(i if i >= 0 else 0)
                w.currentIndexChanged.connect(self._emit)
                if p.help:
                    w.setToolTip(p.help)
                self._rows[p.name] = (p, w, None)
                self.form.addRow(p.label, w)
                continue

            if p.kind == "minutes":
                spin = QDoubleSpinBox()
                spin.setDecimals(0)
                spin.setSuffix(" min")
            else:
                spin = QSpinBox()
            spin.setMinimum(int(p.minimum if p.minimum is not None else 0))
            spin.setMaximum(int(p.maximum if p.maximum is not None else 10 ** 6))
            spin.setValue(int(value) if value is not None
                          else int(p.default or spin.minimum()))
            # Type into it as well as step it.  The arrows alone made large
            # numbers unreachable -- 500 is 500 clicks -- and a spinner whose
            # only affordance is a 1-step arrow reads as broken long before it
            # gets there.
            spin.setKeyboardTracking(False)
            spin.setAccelerated(True)
            step = max(1, int((p.maximum or 100) / 50))
            spin.setSingleStep(step)
            spin.valueChanged.connect(self._emit)
            if p.help:
                spin.setToolTip(p.help)

            warn = None
            if p.advise_above is not None:
                # Allowed, but said out loud.  Refusing outright would be this
                # program deciding for someone what risk they may take with
                # their own account; saying nothing would be letting them take
                # it without knowing.
                warn = QLabel(p.advice)
                warn.setWordWrap(True)
                warn.setStyleSheet(f"color: {theme.RUN_STOP_BG}; "
                                   f"font-size: {theme.FS_SMALL}px;")
                warn.setVisible(int(spin.value()) > int(p.advise_above))
                spin.valueChanged.connect(
                    lambda v, w=warn, lim=p.advise_above:
                    w.setVisible(int(v) > int(lim)))

            box = None
            if p.allow_unlimited:
                box = QCheckBox("No limit")
                box.setChecked(value is None and not endless_blocked)
                spin.setEnabled(value is not None or endless_blocked)
                box.toggled.connect(
                    lambda on, s=spin: (s.setEnabled(not on), self._emit()))
                box.setToolTip(p.help or "")
                if endless_blocked:
                    box.setEnabled(False)
                    box.setToolTip("Only the last task in a list can run forever")

            row = QWidget()
            lay = QVBoxLayout(); lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(spin)
            if box:
                lay.addWidget(box)
            if warn is not None:
                lay.addWidget(warn)
            row.setLayout(lay)
            self._rows[p.name] = (p, spin, box)
            self.form.addRow(p.label, row)

    def _emit(self, *_):
        self.changed.emit()

    def values(self):
        """What the user has set, ready to hand to Task.configure()."""
        out = {}
        for name, (p, w, box) in self._rows.items():
            if box is not None and box.isChecked():
                out[name] = None                       # unlimited
            elif p.kind == "bool":
                out[name] = w.isChecked()
            elif p.kind == "choice":
                out[name] = w.currentText()
            else:
                out[name] = w.value()
        return out
