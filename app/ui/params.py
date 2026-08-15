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

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QLabel, QSpinBox, QVBoxLayout,
                               QWidget)


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
                w.addItems([str(c) for c in p.choices])
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
            spin.valueChanged.connect(self._emit)
            if p.help:
                spin.setToolTip(p.help)

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
