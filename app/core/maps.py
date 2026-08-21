"""
Every map in the game: its name, its world, and where its marker sits.

All of it comes out of the save, so nothing here needs artwork, and a map
nobody has ever visited works as well as one that has been captured.

WHAT MADE THIS POSSIBLE
-----------------------
Reaching an arbitrary map looked like it needed a template per marker -- about
a hundred captures -- or letter recognition to read the map name off the
screen.  Neither is necessary.  The save carries two tables:

    MapDispName[id]     the map's display name
    MapDetails[id][2]   where its marker is drawn on the world map screen

The second was found by measuring rather than by reading code: the three
markers this program already had artwork for were located on real captures and
compared against the table.  Valley of the Beans matched its stored coordinate
to 0.0 px, Winding Willows to 3.6, Equinox Valley to 4.1.  Detecting every gold
marker on the World 1 map and matching the whole set gave the same answer --
16 of 21 within 4 px and NOTHING between 4 and 10, which is the shape of a real
correspondence rather than of coincidence.

Then the name table confirmed it from the other direction, independently: the
ids those markers pointed at, 14 and 19, are exactly Valley_Of_The_Beans and
Winding_Willows in MapDispName.

WORLDS ARE BLOCKS OF FIFTY
--------------------------
Checked at both ends of every world: Blunder Hills is 0 and Meel's Crypt 38;
YumYum Grotto 50; Frostbite Towndra 100 and Equinox Valley 120; Outer World
Town 150 and The Rift 166; Magma Rivertown 200; Spirit Village 250;
Shimmerfin Grove 300.  So the world is arithmetic and no table is needed.
"""

import re

from core import savefile

# The marker's drawn centre against the stored anchor.  Measured across three
# markers with known artwork and a whole-map fit over twenty-one more.
MARKER_OFFSET = (16, 14)

MAPS_PER_WORLD = 50
WORLDS = 7

_cache = {"names": None, "details": None}

# What a map name may be made of.  Used to notice a misread, not to find one.
_NAME_OK = re.compile(r"[A-Za-z0-9_'.\- ]+")
# How far past a declared length to look for the start of the next field when
# the name does not read cleanly.  Generous: the framing seen was six bytes.
_FRAMING_SLACK = 48


def _parse_names(text):
    """MapDispName as a list, index = map id.  None where the game stored a
    reference to a string this parser cannot resolve."""
    i = text.find("MapDispName")
    if i < 0:
        return []
    i += len("MapDispName")
    out, depth, p = [], 0, i
    while p < len(text):
        c = text[p]
        if c == "a":
            depth += 1
            p += 1
            continue
        if c == "h":
            depth -= 1
            p += 1
            if depth <= 0:
                break
            continue
        # Strings declare their own length.  Matching the name with a
        # character class instead reads "Blunder_Hills" as "Blunder_Hillsy13".
        m = re.match(r"y(\d+):", text[p:])
        if m:
            n = int(m.group(1))
            start = p + m.end()
            name = text[start:start + n]
            end = start + n
            # ...but the length alone is not enough, because this is a raw
            # leveldb file and its block framing gets written INTO a record.
            # Seen live: "y13:)adq�The_Clamworks" -- the 13 is
            # correct and six bytes of framing sit between the marker and the
            # name, so reading thirteen characters from the marker returns
            # rubbish and every later index is wrong. That cost twenty map
            # names, World 7 among them.
            #
            # The field ends where the next one starts, so the name is its
            # LAST n characters. Only used when the straightforward read does
            # not look like a name, so the common case pays nothing.
            if not _NAME_OK.fullmatch(name):
                nxt = re.search(r"y\d+:", text[start:start + n + _FRAMING_SLACK])
                if nxt:
                    field = text[start:start + nxt.start()]
                    if len(field) >= n:
                        name = field[-n:]
                        end = start + nxt.start()
            out.append(name if _NAME_OK.fullmatch(name) else None)
            p = end
            continue
        m = re.match(r"R\d+|z|i-?\d+|d[\d.eE+-]+", text[p:])
        if m:
            out.append(None)
            p += m.end()
            continue
        break
    return out


def _parse_details(text):
    """MapDetails as a list of nested lists, index = map id."""
    i = text.find("MapDetails")
    if i < 0:
        return []
    toks = re.findall(r"(a|h|z|i-?\d+|d-?[\d.eE+-]+|R\d+|y\d+)",
                      text[i + len("MapDetails"):])
    stack, cur, root = [], None, None
    for tk in toks:
        if tk == "a":
            new = []
            if cur is None:
                root = new
            else:
                cur.append(new)
                stack.append(cur)
            cur = new
        elif tk == "h":
            if not stack:
                break
            cur = stack.pop()
        elif tk[0] == "y":
            break
        elif cur is not None:
            cur.append(0 if tk == "z" else
                       int(tk[1:]) if tk[0] == "i" else float(tk[1:]))
    return root or []


def _tables(text=None):
    if _cache["names"] is None or text is not None:
        body = text if text is not None else savefile._newest_text()
        if not body:
            return [], []
        _cache["names"] = _parse_names(body)
        _cache["details"] = _parse_details(body)
    return _cache["names"], _cache["details"]


def world_of(map_id):
    """Which world a map belongs to, 1..7."""
    return min(WORLDS, map_id // MAPS_PER_WORLD + 1)


def pretty(name):
    """'Valley_Of_The_Beans' -> 'Valley Of The Beans'."""
    return (name or "").replace("_", " ")


def marker_xy(map_id, text=None):
    """
    Where to click on the world map screen to select this map, or None.

    None means the game does not draw a marker for it -- towns reached by the
    world tab, arenas behind a boss, and a few placeholders whose stored
    coordinate is an obvious sentinel like (999, 999).
    """
    _names, details = _tables(text)
    if map_id >= len(details):
        return None
    entry = details[map_id]
    if len(entry) < 3 or not isinstance(entry[2], list) or len(entry[2]) < 2:
        return None
    x, y = entry[2][0], entry[2][1]
    if not (0 < x < 960 and 0 < y < 540):
        return None
    return (int(x + MARKER_OFFSET[0]), int(y + MARKER_OFFSET[1]))


def maps_in(world, text=None):
    """
    Every reachable map in a world, as [(id, display name), ...].

    Only maps the game draws a marker for: anything else cannot be selected on
    the map screen, so offering it in a list would be offering a dead end.
    """
    names, _details = _tables(text)
    lo = (world - 1) * MAPS_PER_WORLD
    hi = min(lo + MAPS_PER_WORLD, len(names))
    out = []
    for map_id in range(lo, hi):
        name = names[map_id] if map_id < len(names) else None
        if not name or marker_xy(map_id, text) is None:
            continue
        out.append((map_id, pretty(name)))
    return out


def find_map(name, text=None):
    """The id of a map by display name, or None.  Case and spaces ignored."""
    names, _ = _tables(text)
    want = pretty(name).strip().lower()
    for map_id, got in enumerate(names):
        if got and pretty(got).strip().lower() == want:
            return map_id
    return None


def current_map(text=None):
    """
    Which map the character is standing on, or None.

    From the save, so it is up to ~175 s behind -- useless for reacting to a
    move, and exactly right for confirming one that happened a while ago.
    """
    body = savefile._newest_text() if text is None else text
    raw = savefile.raw("CurrentMap", body, window=24)
    if not raw:
        return None
    m = re.match(r"i(-?\d+)|z", raw)
    if not m:
        return None
    return 0 if m.group(0) == "z" else int(m.group(1))
