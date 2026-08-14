"""
The window's colours, in one place.

Kept together rather than scattered through the widgets so a palette can be
tried and changed in one edit -- which is what happened: the first version had
greys and blues written into six files, and changing them meant finding them.

WHAT IS AND IS NOT THEMED
-------------------------
These are the *chrome*: background, borders, text, headings.  The world colours
in core/worlds.py are deliberately not here -- they identify World 1 from World
7 and mean something in the game, so they do not move when the theme does.  The
run states (green done, amber running, red problem) stay put for the same
reason: they are information, not decoration.
"""

BACKGROUND = "#f2e9e4"
BORDER = "#c9ada7"
BORDER_WIDTH = 1
TEXT = "#4a4e69"
TITLE = "#22223b"
RADIUS = 4

# Type sizes, in pixels.  Qt wants whole pixels, so the half-steps chosen in
# the theme lab are rounded here -- 15.5 and 19.5 became 16 and 20, which is
# under half a pixel either way and not visible at these sizes.
FS_BODY = 16
FS_TITLE = 28        # the app title
FS_HEAD = 20         # section headings
FS_TASK = 16         # a task in the run list
FS_CHIP = 16         # a task in the palette
FS_BUTTON = 16
FS_RUN = 20          # the big Run list button
FS_LOG = 11          # activity, monospace
FS_SMALL = 12        # estimates and hints

# Shape of a task row.
ROW_HEIGHT = 28
ROW_GAP = 6
CARD_RADIUS = 4

# Tabs get their own two numbers: the gap between them reads as separation
# rather than spacing, and their corners need to be rounder than a panel's
# to be visible at tab size.
TAB_RADIUS = 7
TAB_GAP = 7

# Derived, so the palette stays a four-line edit.
PANEL = "#faf6f4"          # a shade up from the background, for input surfaces
MUTED = "#7d7f96"          # secondary text: durations, hints
BORDER_SOFT = "#ded0cb"    # inner rules that should not shout

# The one primary action on the page.  Filled in the title colour rather than a
# new hue: green, amber and red already mean done, running and wrong, and a
# fourth colour competing with those would weaken all of them.
RUN_BG = "#22223b"
RUN_HOVER = "#3b3d5c"
RUN_TEXT = "#f2e9e4"
RUN_OFF_BG = "#eae0da"     # nothing queued: present, but plainly not ready
RUN_OFF_TEXT = "#a99b95"

# While a run is going, the same button says how to stop it.  Warm rather than
# red: red already means a task went wrong, and stopping on purpose has not.
RUN_STOP_BG = "#8a5f5a"
RUN_STOP_HOVER = "#734c48"

# "A new version exists."  Saturated on purpose -- it is the one message that
# has to survive being ignored, and it appears at most once per launch.
#
# Violet rather than a warmer colour because every warm hue is already spoken
# for and would be read as status: amber means a run is happening, green that
# it finished, red that a task failed, and #b36b00 that it was stopped early.
# An update is none of those, so it takes the one bright hue no state uses --
# and violet is the palette's own family anyway, a saturated cousin of the
# TEXT and TITLE slates rather than an import from somewhere else.
UPDATE_BG = "#7c3aed"
UPDATE_HOVER = "#6d28d9"
UPDATE_TEXT = "#ffffff"


def stylesheet():
    """One application-wide sheet, applied before the window is built."""
    return f"""
    QWidget {{
        background: {BACKGROUND};
        color: {TEXT};
        font-size: {FS_BODY}px;
    }}
    QLabel {{ background: transparent; }}
    QLabel[heading="true"] {{
        color: {TITLE};
        font-weight: bold;
        font-size: {FS_HEAD}px;
    }}
    QListWidget, QPlainTextEdit, QTextBrowser, QScrollArea, QLineEdit,
    QSpinBox, QDoubleSpinBox {{
        background: {PANEL};
        border: {BORDER_WIDTH}px solid {BORDER};
        border-radius: {RADIUS}px;
        color: {TEXT};
    }}
    QTabWidget::pane {{
        border: {BORDER_WIDTH}px solid {BORDER};
        border-radius: {RADIUS}px;
        background: {BACKGROUND};
    }}
    QTabBar::tab {{
        background: {PANEL};
        color: {TEXT};
        /* Top corners only, and no bottom border: that is what makes a tab
           look joined to the pane beneath it.  Rounding all four was tried and
           breaks the effect -- the tab detaches and reads as a floating pill.
           The radius is larger than a panel's instead, which is what makes it
           visible at this size. */
        border: {BORDER_WIDTH}px solid {BORDER};
        border-bottom: none;
        border-top-left-radius: {TAB_RADIUS}px;
        border-top-right-radius: {TAB_RADIUS}px;
        border-bottom-left-radius: 0px;
        border-bottom-right-radius: 0px;
        /* Bold here, not on :selected.  A weight that appears only in the
           selected state changes the tab's size, and Qt then lays the two
           kinds out at different heights -- which is what made the tabs look
           misaligned.  Selection changes colour only. */
        font-weight: bold;
        padding: 6px 14px 6px 14px;
        font-size: {FS_BUTTON}px;
        margin-right: {TAB_GAP}px;
        margin-top: 0px;
        min-height: 20px;
    }}
    QTabBar::close-button {{
        /* Kept only as a fallback.  The real spacing comes from the button
           this program supplies itself, because Qt accepts these margins and
           then ignores them -- measured, the cross sat 1 px from the edge
           whatever was written here. */
        margin-right: 6px;
        margin-left: 2px;
    }}
    /* Only the list tabs carry a close cross, and only they want no padding
       on that side -- the cross supplies its own spacing.  Applying this to
       every tab bar stripped the right padding from the panel tabs too, which
       have no cross and simply ended up lopsided. */
    QTabWidget#lists QTabBar::tab {{
        padding-right: 0px;
    }}
    QTabBar::tab:selected {{
        background: {BORDER};
        color: {TITLE};
    }}
    QPushButton {{
        background: {PANEL};
        color: {TEXT};
        border: 2px solid {BORDER};
        border-radius: {RADIUS}px;
        padding: 5px 12px;
        font-size: {FS_BUTTON}px;
    }}
    QPushButton:hover {{ background: {BORDER}; color: {TITLE}; }}
    QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER_SOFT}; }}
    QToolButton {{ background: transparent; border: none; }}
    QSplitter::handle {{ background: {BORDER_SOFT}; }}
    QCheckBox {{ background: transparent; color: {TEXT}; }}
    """
