"""
Worlds, and the colour each one is drawn in.

Every task belongs to a world, which is how the window groups them and how a
run list stays readable at a glance: a list that alternates between World 1 and
World 7 is a list that spends its time travelling, and seeing two colours
alternate says that faster than reading five rows of text.

WHY NOT THE GAME'S OWN COLOURS
------------------------------
The obvious move was to sample the world buttons from a screenshot, and it was
tried: the medians come back muddy, because the buttons are semi-transparent
over whatever scenery sits behind them -- World 1 and World 6 both sampled
brown when both are green in game.  So these are hand-picked in the game's
ordering instead, chosen to stay distinguishable from each other, which is the
job they actually have to do.
"""

NAMES = {
    1: "World 1", 2: "World 2", 3: "World 3", 4: "World 4",
    5: "World 5", 6: "World 6", 7: "World 7",
}

COLOURS = {
    1: "#3f8f4f",     # green
    2: "#a8781f",     # desert gold
    3: "#2f6fb5",     # snow blue
    4: "#8a3fa8",     # purple
    5: "#b8402a",     # red
    6: "#2f8f7a",     # jungle teal
    7: "#3f9fc0",     # underwater cyan
}

UNKNOWN = "#8a8f96"          # a task that has not said where it lives


def colour(world):
    return COLOURS.get(world, UNKNOWN)


def tint(world, amount=0.86):
    """
    A pale version of a world's colour, for filling a card behind text.

    Mixed toward white rather than given a fixed alpha: the cards sit on a
    background whose colour depends on the user's theme, and a translucent
    fill would come out differently on each one.
    """
    hexcol = colour(world).lstrip("#")
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: int(c + (255 - c) * amount)
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"
