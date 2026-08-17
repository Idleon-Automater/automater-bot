"""
Is there a newer release?

Asks one static JSON file on the release bucket and compares its version to
this build's.  Deliberately the smallest thing that can work:

  * it NEVER downloads or runs anything.  The footer offers a link and the
    browser does the rest.  A small unsigned exe that fetches code and
    executes it is the textbook description of a dropper, and this program
    already synthesises mouse input and reads another window -- it does not
    need to look any more like malware than it has to.
  * it NEVER raises.  No network, a captive portal, a proxy, a typo'd URL, a
    bucket that has not been uploaded to yet: every one of those means "no
    update to report", not a broken launch.
  * it NEVER blocks the UI.  The caller runs it on a thread; the window opens
    whether or not the check ever answers.

The manifest looks like:

    {"version": "1.1.0",
     "url": "https://.../releases/1.1.0/IdleonAutomator.exe",
     "sha256": "....",
     "notes": "Swishy Hoops can play several games in a row."}

`sha256` is unused today and is published anyway: it costs nothing now, and
without it in the format from the start there is no way to add integrity
checking later without breaking every already-released build's parser.
"""

import json
import re
import urllib.request

# This build.  Compared against the manifest, and shown in the window.
VERSION = "1.0.2"

BUCKET = "https://pub-6ca3288dbdb14574a96b21dac0c7fac1.r2.dev"
MANIFEST_URL = BUCKET + "/version.json"

# Short: this runs at launch, and a user on a bad connection should not wait.
# Note this bounds the HTTP exchange, NOT name resolution -- an unresolvable
# host took 11 s to fail in testing, which is the OS resolver's timeout and is
# not ours to set.  That is why the caller runs this on a thread.
TIMEOUT_S = 5.0

# A manifest should be a few hundred bytes.  Anything huge is a wrong URL or a
# captive portal's login page, and is not worth reading into memory.
MAX_BYTES = 64 * 1024


def parse_version(text):
    """
    "1.2.3" -> (1, 2, 3).  Junk -> ().

    Leading zeros, extra parts and a "v" prefix are all accepted, because the
    manifest is hand-edited and a release should not be missed over a typo.
    Anything unparseable compares as older than everything, so a malformed
    manifest reports "no update" rather than nagging on every launch.
    """
    if not isinstance(text, str):
        return ()
    m = re.findall(r"\d+", text)
    return tuple(int(p) for p in m[:4]) if m else ()


def is_newer(remote, local=VERSION):
    """Whether `remote` is a later version than `local`."""
    r, l = parse_version(remote), parse_version(local)
    if not r:
        return False
    # Pad so (1, 1) and (1, 1, 0) compare equal rather than the shorter losing.
    n = max(len(r), len(l))
    return r + (0,) * (n - len(r)) > l + (0,) * (n - len(l))


def fetch(url=MANIFEST_URL, timeout=TIMEOUT_S):
    """The manifest as a dict, or None if it could not be read."""
    try:
        req = urllib.request.Request(url, headers={
            # Named plainly.  A release that lies about what it is in order to
            # look like a browser is not the kind of thing this should be.
            "User-Agent": f"IdleonAutomator/{VERSION}",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if getattr(r, "status", 200) != 200:
                return None
            data = json.loads(r.read(MAX_BYTES).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def check(url=MANIFEST_URL, timeout=TIMEOUT_S, local=VERSION):
    """
    Details of a newer release, or None.

    Returns {"version", "url", "notes"} -- everything the footer needs to
    offer the download, and nothing it does not.
    """
    data = fetch(url, timeout)
    if not data:
        return None
    remote = data.get("version")
    if not is_newer(remote, local):
        return None
    link = data.get("url")
    if not isinstance(link, str) or not link.startswith("https://"):
        # Refuse anything that is not an HTTPS string: the user is about to be
        # asked to click it.  A manifest with no url at all used to fall back
        # to the bucket root, which serves an XML listing error -- offering
        # someone a broken download is worse than saying nothing.
        return None
    return {"version": str(remote),
            "url": link,
            "notes": str(data.get("notes") or "")}
