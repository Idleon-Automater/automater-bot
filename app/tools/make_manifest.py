#!/usr/bin/env python3
"""
Write dist/version.json to describe the exe sitting next to it.

    python tools/make_manifest.py

WHY THIS IS NOT PART OF THE BUILD
---------------------------------
Signing REWRITES the executable.  A signature is appended to the PE and the
file's bytes change, so a hash taken before signing describes a file nobody
will ever download.  The build calls this at the end so an unsigned build is
still complete -- but the moment a signing step exists, the order has to be:

    build  ->  sign  ->  make_manifest  ->  upload

Run this again after signing and the hash is correct.  Skip it and you publish
a SHA-256 that fails for every single user, which is worse than publishing
none: it tells the careful ones the download was tampered with.

The release notes are the only hand-written field, so they are read back out of
the existing manifest and preserved -- but only when the version still matches.
A version BUMP clears them, because notes describing the previous release would
tell users the wrong thing changed.
"""

import hashlib
import json
import os
import subprocess
import sys

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

NAME = "IdleonAutomator"
DIST = os.path.join(os.path.dirname(_APP), "dist")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def signature_status(exe):
    """
    'Valid', 'NotSigned', ... or None if it could not be determined.

    Asked of Windows rather than parsed out of the PE ourselves: the question
    is "will SmartScreen see a signature", and the only authority on that is
    the thing that checks it.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-AuthenticodeSignature '{exe}').Status"],
            capture_output=True, text=True, timeout=30)
        status = out.stdout.strip()
        return status or None
    except Exception:
        return None


def write(exe=None, out=None, quiet=False):
    """Regenerate the manifest for `exe`.  Returns the manifest dict."""
    from core.update import VERSION, BUCKET

    exe = exe or os.path.join(DIST, f"{NAME}.exe")
    out = out or os.path.join(DIST, "version.json")
    if not os.path.exists(exe):
        raise SystemExit(f"no exe at {exe} -- run tools/build_exe.py first")

    notes = ""
    try:
        with open(out, encoding="utf-8") as f:
            previous = json.load(f)
        if previous.get("version") == VERSION:
            notes = str(previous.get("notes") or "")
    except Exception:
        pass

    manifest = {
        "version": VERSION,
        "url": f"{BUCKET}/releases/{VERSION}/{NAME}.exe",
        "sha256": sha256_of(exe),
        "notes": notes,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    if not quiet:
        size = os.path.getsize(exe) / 1000 / 1000
        sig = signature_status(exe)
        print(f"[Manifest] {out}")
        print(f"[Manifest] version {VERSION}   {size:.1f} MB")
        print(f"[Manifest] sha256  {manifest['sha256']}")
        if sig == "Valid":
            print("[Manifest] signature: VALID -- hash covers the signed file")
        elif sig == "NotSigned":
            print("[Manifest] signature: NONE. If you meant to sign, sign the "
                  "exe and run this again -- signing changes the hash.")
        elif sig:
            print(f"[Manifest] signature: {sig}  <-- check this before publishing")
        if notes:
            print(f"[Manifest] kept your notes: {notes!r}")
        else:
            print('[Manifest] fill in "notes" -- it is what users are told '
                  "changed.")
    return manifest


if __name__ == "__main__":
    write()
