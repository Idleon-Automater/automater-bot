"""
The three links along the bottom, and the disclaimer they can open.

The disclaimer is drawn INSIDE the window rather than in a dialog of its own.
A dialog would be a second thing in the taskbar and a second thing to lose
behind the game; a panel that covers the window and goes away again keeps the
program feeling like one place.

THE LOGOS ARE DRAWN, NOT SHIPPED
--------------------------------
Both marks are painted with a few primitives, for the same reason the pencil
and the close cross are: no artwork to bundle, nothing for the release
allowlist to pass on, nothing that can go missing from the executable.  They
are recognisable rather than exact -- the real Discord and Ko-fi marks are
their owners' trademarks, and approximating them in a few lines of QPainter is
honest about what it is.  Drop real PNGs in ui/art and point the buttons at
them if you would rather have the genuine ones.
"""

import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QIcon, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ui import theme

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "art")


def brand_icons(stem, drawn, colour):
    """
    The real marks if they have been supplied, otherwise the drawn stand-ins.

    Returns (resting, hover).  Two files per brand -- coloured for a light
    ground, white for the filled hover state -- because that is how the brand
    guidelines say to use them, and because a coloured mark on its own colour
    is invisible.
    """
    rest = os.path.join(ART, f"{stem}.png")
    over = os.path.join(ART, f"{stem}_white.png")
    if os.path.exists(rest) and os.path.exists(over):
        return QIcon(rest), QIcon(over)
    return drawn(20, colour), drawn(20, "#ffffff")

# The real addresses.  Left blank, the buttons say so rather than opening
# nothing -- see _link() and _image_link().
DISCORD_URL = "https://discord.gg/EqqvcFSPKp"
KOFI_URL = "https://ko-fi.com/idleonautomater"

# Their own brand colours, so the links read as the places they lead to.
DISCORD_COLOUR = "#5865F2"
KOFI_COLOUR = "#FF5E5B"

DISCLAIMER = """
<h2>Before you use this</h2>

<p><b>This program plays the game for you.</b> It moves your mouse and clicks
on your behalf, in a live game, on your account.</p>

<p><b>Automation may break the game's rules.</b> Legends of Idleon is made by
Lava, who set the terms for how it may be played. This program is not made by,
endorsed by, or connected to Lava in any way. Using it could get your account
suspended or banned. That risk is yours to take, and it is a real one.</p>

<p><b>It can also simply get things wrong.</b> It reads the screen and clicks
what it believes it sees. A misread costs a click, and some clicks cost
resources &mdash; refining a salt that was not ready, or spending materials you
were saving. It is written to do nothing when it is unsure rather than guess,
but "written to" is not "guaranteed to".</p>

<p><b>Watch it the first few times.</b> Press F6 to stop, twice to force it and
get your mouse back. Do not leave it running unattended until you have seen it
do the thing you are asking for, correctly, more than once.</p>

<p><b>It checks for updates, and that is all it sends.</b> On startup it asks
one small file on a Cloudflare bucket whether a newer version exists. That
request tells Cloudflare your IP address, the way visiting any website does.
Nothing else leaves your machine &mdash; no account details, no screenshots, no
usage data, and there is no server collecting anything. If an update exists you
are shown a link; the program never downloads or installs anything by itself.
Offline, the check fails quietly and you will not notice it.</p>

<p><b>No warranty.</b> This is a hobby program given away as-is. Nobody owes
you a working bot, a recovered account, or your afternoon back.</p>

<p style="color:#7d7f96"><i>If any of that is not a trade you want to make,
close this and play by hand &mdash; which is, genuinely, fine.</i></p>
"""


def discord_icon(size=16, colour="#5865F2"):
    """A rounded game-pad silhouette: recognisable, not the real mark."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QBrush(QColor(colour)))
    p.setPen(Qt.NoPen)
    body = QRectF(size * 0.08, size * 0.24, size * 0.84, size * 0.52)
    p.drawRoundedRect(body, size * 0.26, size * 0.26)
    # the two flared corners that make the shape read as Discord rather than
    # as a plain pill
    path = QPainterPath(QPointF(size * 0.22, size * 0.72))
    path.lineTo(size * 0.12, size * 0.88)
    path.lineTo(size * 0.34, size * 0.76)
    p.drawPath(path)
    path = QPainterPath(QPointF(size * 0.78, size * 0.72))
    path.lineTo(size * 0.88, size * 0.88)
    path.lineTo(size * 0.66, size * 0.76)
    p.drawPath(path)
    p.setBrush(QBrush(QColor("#ffffff")))
    r = size * 0.09
    p.drawEllipse(QPointF(size * 0.37, size * 0.50), r, r * 1.15)
    p.drawEllipse(QPointF(size * 0.63, size * 0.50), r, r * 1.15)
    p.end()
    return QIcon(pix)


def kofi_icon(size=16, colour="#FF5E5B"):
    """A cup with a handle and a curl of steam."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(colour)
    p.setBrush(QBrush(c))
    p.setPen(Qt.NoPen)
    cup = QRectF(size * 0.16, size * 0.38, size * 0.50, size * 0.44)
    p.drawRoundedRect(cup, size * 0.10, size * 0.10)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(c, size * 0.10))
    p.drawArc(QRectF(size * 0.58, size * 0.44, size * 0.30, size * 0.30),
              -70 * 16, 200 * 16)
    p.setPen(QPen(c, size * 0.08))
    p.drawArc(QRectF(size * 0.28, size * 0.10, size * 0.20, size * 0.22),
              0, 180 * 16)
    p.end()
    return QIcon(pix)


HOW_TO = """
<h2>How to use it</h2>

<p><b>1. Build a list.</b> Pick a task on the left and press <i>Add to list</i>,
or double-click it, or drag it into the list. Drag a task inside the list to
reorder it. To remove one, select it and press <i>Remove task</i>, or drag it
out of the list.</p>

<p><img src="{ART}/howto_drag.png" width="640"></p>

<p><b>2. Set each task up.</b> Click a task in your list and its settings appear
underneath &mdash; how long to run, what score to stop at, which upgrade to buy.
Every task also says what it needs before it will work.</p>

<p><b>3. Keep your lists.</b> Use the tabs at the top to make more than one
&mdash; a Daily and a Weekly, say. Press <i>Save list</i> when the button is
bright; washed out means there is nothing to save. Lists are kept between
sessions, and survive updating the program.</p>

<p><b>4. Press Run list.</b> The tasks run in order. Each one travels to itself,
so the character does not need to start anywhere in particular.</p>

<h2>While it runs</h2>

<p><b>Leave the game window visible and unobscured.</b> The bot works by looking
at the screen. If the game is minimised, behind another window, or scrolled off,
it cannot see and it will stop rather than click blind.</p>

<p><b>Do not touch the mouse.</b> The bot moves the pointer itself, and a nudge
at the wrong moment lands a click somewhere it was not meant to go &mdash; which
in the overworld moves your character and breaks every task after it.</p>

<p><b>Press <span style="font-weight:bold">F6</span> to stop.</b> It finishes
what it is doing and stops cleanly. <b>F6 again forces it</b> and gives the
mouse straight back. This works even while the bot has the pointer.</p>

<p><b>Some tasks spend a teleport.</b> Anything reached through the map rather
than a town costs one of the daily allowance &mdash; the task says so in its
requirements. Town routes are free.</p>

<p><b>Doing nothing is often correct.</b> A refinery with nothing ready, or an
Equinox bar that is not full, will report that and move on. That is the task
working, not failing.</p>

<h2>If something goes wrong</h2>

<p>The <b>Last run</b> tab keeps the report from the last list, saying what each
task did and why anything was skipped. A task that could not start says what it
was missing rather than guessing.</p>
"""

# Qt reads a local image in rich text through a file URL, and the panel is
# built long before anyone knows where the program was installed -- so the path
# is filled in here rather than written into the text above.  The pictures are
# grabs of THIS window with an arrow drawn on, made by
# _dev/make_howto_art.py, so they cannot drift out of date the way a
# hand-drawn mock-up of a UI does.
HOW_TO = HOW_TO.replace("{ART}", "file:///" + ART.replace(os.sep, "/"))


class Panel(QWidget):
    """A panel that covers the window until it is dismissed."""

    closed = Signal()

    def __init__(self, parent, body=None):
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background: {theme.BACKGROUND};")

        text = QLabel(DISCLAIMER if body is None else body)
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        text.setAlignment(Qt.AlignTop)
        text.setStyleSheet(
            f"color: {theme.TEXT}; background: transparent;"
            f" font-size: {theme.FS_BODY}px;")

        holder = QWidget()
        hl = QVBoxLayout(holder)
        # The panel itself runs edge to edge; the breathing room is put around
        # the words instead, which is what keeps a wall of text readable.
        hl.setContentsMargins(100, 30, 100, 20)
        hl.addWidget(text)
        hl.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {theme.BACKGROUND}; }}")
        holder.setStyleSheet(f"background: {theme.BACKGROUND};")

        close = QPushButton("Back to the program")
        close.setMinimumHeight(34)
        close.setStyleSheet(
            f"QPushButton {{ background: {theme.RUN_BG}; color: {theme.RUN_TEXT};"
            f" font-weight: bold; border: 2px solid {theme.RUN_BG};"
            f" border-radius: {theme.RADIUS}px; }}"
            f"QPushButton:hover {{ background: {theme.RUN_HOVER};"
            f" border-color: {theme.RUN_HOVER}; }}")
        close.clicked.connect(self.dismiss)

        buttons = QWidget()
        bl = QHBoxLayout(buttons)
        bl.setContentsMargins(100, 0, 100, 26)
        bl.addWidget(close)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)     # covers the window completely
        box.setSpacing(0)
        box.addWidget(scroll)
        box.addWidget(buttons)

    def dismiss(self):
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event):
        # Escape closes it, because every panel that covers a window should.
        if event.key() == Qt.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(event)


class LinkButton(QPushButton):
    """
    A link whose mark swaps to white while the button is filled.

    The icon is painted in the brand colour, and hovering fills the button with
    that same colour -- so the mark vanished into its own background.  A
    stylesheet cannot swap an icon, so the two are drawn up front and exchanged
    on enter and leave, which is what the brand guidelines do anyway: coloured
    mark on a light ground, white mark on a coloured one.
    """

    def __init__(self, text, icons):
        super().__init__(text)
        self._rest, self._over = icons
        self.setIcon(self._rest)

    def enterEvent(self, event):
        if self.isEnabled():
            self.setIcon(self._over)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setIcon(self._rest)
        super().leaveEvent(event)


class AfterRun(QWidget):
    """
    The two links, shown between the run report and the activity log.

    Its own strip rather than lines inside the report: the report is a record
    of what happened and grows as the run goes, and an invitation buried at the
    bottom of it scrolls away with everything else.
    """

    def __init__(self, footer):
        super().__init__()
        self.setVisible(False)

        def block(caption, button):
            """A centred caption with its centred button underneath."""
            lab = QLabel(caption)
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet(
                f"color: {theme.TEXT}; background: transparent;"
                f" font-size: {theme.FS_BODY}px;")
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 0, 0, 0)
            btn_row.addStretch()
            btn_row.addWidget(button)
            btn_row.addStretch()
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(5)
            col.addWidget(lab)
            col.addLayout(btn_row)
            return col

        self.discord = footer.make_discord()
        self.kofi = footer.make_kofi()

        box = QVBoxLayout(self)
        box.setContentsMargins(2, 10, 2, 10)
        box.setSpacing(14)
        box.addLayout(block("Get information and request features",
                            self.discord))
        box.addLayout(block("Help the project going further", self.kofi))


class Footer(QWidget):
    """How to use it, Disclaimer, Discord, Ko-fi."""

    show_disclaimer = Signal()
    show_how_to = Signal()

    def __init__(self):
        super().__init__()
        row = QHBoxLayout(self)
        # Matches the margins the two columns above use, so the links sit
        # under the content rather than hanging off the edge of it.
        row.setContentsMargins(6, 6, 10, 0)
        row.setSpacing(8)

        link = ("QPushButton {{ background: transparent; border: none;"
                " color: {c}; padding: 3px 8px; font-size: {s}px;"
                " text-decoration: underline; }}"
                "QPushButton:hover {{ color: {h}; }}")

        # An outlined button, not a text link like Disclaimer.  Sitting in a
        # row of quiet grey links it read as small print, which is the wrong
        # weight for the one thing a first-time user should open BEFORE
        # pressing Run -- it carries "leave the game visible", "do not touch
        # the mouse" and "F6 to stop".  Same reasoning as the update badge:
        # shape is what gets noticed, not colour.
        howto = QPushButton("?  How to use it")
        howto.setCursor(Qt.PointingHandCursor)
        howto.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" border: 2px solid {theme.RUN_BG}; color: {theme.RUN_BG};"
            f" border-radius: {theme.RADIUS}px; font-weight: bold;"
            f" padding: 4px 12px; font-size: {theme.FS_SMALL}px; }}"
            f"QPushButton:hover {{ background: {theme.RUN_BG};"
            f" color: {theme.RUN_TEXT}; }}")
        howto.clicked.connect(self.show_how_to.emit)

        disc = QPushButton("Disclaimer")
        disc.setCursor(Qt.PointingHandCursor)
        disc.setStyleSheet(link.format(c=theme.MUTED, h=theme.TITLE,
                                       s=theme.FS_SMALL))
        disc.clicked.connect(self.show_disclaimer.emit)

        # Hidden until a check finds something.  A control that is present but
        # says "you are up to date" is a control that has to be looked at on
        # every launch to learn nothing; this one appearing IS the message.
        #
        # A filled badge rather than another underlined link like Disclaimer:
        # sitting in a row of quiet text links, a link is exactly what the eye
        # has learned to skip.  Changing the SHAPE does more for being noticed
        # than changing the colour, and this is the one thing in the footer
        # that has to survive being ignored.
        self.update_btn = QPushButton()
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setVisible(False)
        self.update_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.UPDATE_BG};"
            f" color: {theme.UPDATE_TEXT}; border: none;"
            f" border-radius: {theme.RADIUS}px; font-weight: bold;"
            f" padding: 4px 12px; font-size: {theme.FS_SMALL}px; }}"
            f"QPushButton:hover {{ background: {theme.UPDATE_HOVER}; }}")
        self.update_btn.clicked.connect(self._open_update)
        self._update_url = ""

        self.discord = self.make_discord()
        # The invitation is part of the button, not a caption beside it: a
        # label outside the border reads as a note about the button rather than
        # as the thing the button does.
        # Ko-fi supply a finished button rather than a bare mark, so it is
        # used as the whole control: beige at rest, red under the pointer.
        self.kofi = self.make_kofi()

        row.addWidget(howto)
        row.addWidget(disc)
        row.addWidget(self.update_btn)
        row.addStretch()
        row.addWidget(self.discord)
        row.addWidget(self.kofi)

    def announce_update(self, info):
        """
        Show the notice for a newer release.  `info` is core.update.check()'s
        result, so None -- no update, no network, bad manifest -- means leave
        the footer exactly as it was.

        The version is named rather than a bare "an update is available",
        because someone who has just downloaded 1.0.1 should be able to see at
        a glance that the notice is stale rather than wonder.
        """
        if not info or not info.get("url"):
            return
        self._update_url = info["url"]
        self.update_btn.setText(f"New version {info['version']} "
                                f"\N{EM DASH} download")
        notes = (info.get("notes") or "").strip()
        # The notes are the release's own words and could be any length; the
        # tooltip is where they fit without the footer growing a second row.
        self.update_btn.setToolTip(f"{notes}\n\n{self._update_url}"
                                   if notes else self._update_url)
        self.update_btn.setVisible(True)

    def _open_update(self):
        """Hand off to the browser.  This program never downloads the exe
        itself -- see core/update.py for why."""
        if self._update_url:
            QDesktopServices.openUrl(self._update_url)

    def make_discord(self):
        """A fresh Discord button.  Widgets cannot be in two layouts at once,
        so the footer and the after-run strip each get their own."""
        return self._link(
            "Discord", brand_icons("discord_mark", discord_icon, DISCORD_COLOUR),
            DISCORD_URL, DISCORD_COLOUR)

    LINK_HEIGHT = 38          # Discord and Ko-fi match, side by side

    def make_kofi(self):
        return self._image_link("support_me_on_kofi_beige",
                                "support_me_on_kofi_red", KOFI_URL,
                                height=self.LINK_HEIGHT)

    def _image_link(self, rest_name, hover_name, url, height=38):
        """A link that IS its artwork -- no border, no text of our own."""
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QPixmap

        rest = QPixmap(os.path.join(ART, f"{rest_name}.png"))
        over = QPixmap(os.path.join(ART, f"{hover_name}.png"))
        if rest.isNull() or over.isNull():
            return self._link("Ko-fi  -  Support the project",
                              brand_icons("kofi_mark", kofi_icon, KOFI_COLOUR),
                              url, KOFI_COLOUR)
        rest = rest.scaledToHeight(height, Qt.SmoothTransformation)
        over = over.scaledToHeight(height, Qt.SmoothTransformation)
        b = LinkButton("", (QIcon(rest), QIcon(over)))
        b.setIconSize(QSize(rest.width(), rest.height()))
        b.setFixedSize(rest.width(), height)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton { background: transparent; border: none;"
                        " padding: 0px; }")
        if url:
            b.clicked.connect(lambda _=False, u=url:
                              QDesktopServices.openUrl(u))
            b.setToolTip(url)
        else:
            b.setEnabled(False)
            b.setToolTip("No Ko-fi address set yet")
        return b

    def _link(self, label, icons, url, colour):
        from PySide6.QtCore import QSize

        b = LinkButton(f" {label}", icons)
        b.setIconSize(QSize(20, 20))
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(self.LINK_HEIGHT)
        b.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" border: 2px solid {colour}; border-radius: {theme.RADIUS}px;"
            f" color: {colour}; font-weight: bold; padding: 5px 14px;"
            f" font-size: {theme.FS_BUTTON}px; }}"
            f"QPushButton:hover {{ background: {colour}; color: #ffffff; }}"
            f"QPushButton:disabled {{ border-color: {theme.BORDER_SOFT};"
            f" color: {theme.MUTED}; }}")
        if url:
            b.clicked.connect(lambda _=False, u=url:
                              QDesktopServices.openUrl(u))
            b.setToolTip(url)
        else:
            # An address nobody filled in: say so rather than opening nothing
            # and looking broken.
            b.setEnabled(False)
            b.setToolTip(f"No {label} address set yet")
        return b
