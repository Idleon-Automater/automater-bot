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

import argparse
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


def write(exe=None, out=None, quiet=False, notes=None):
    """
    Regenerate the manifest for `exe`.  Returns the manifest dict.

    `notes` given explicitly wins.  Otherwise they are carried over from an
    existing manifest of the SAME version -- which works locally but never in
    CI, where the runner starts with no previous file and there is nothing to
    carry over.  That is what --notes is for.
    """
    from core.update import VERSION, BUCKET

    exe = exe or os.path.join(DIST, f"{NAME}.exe")
    out = out or os.path.join(DIST, "version.json")
    if not os.path.exists(exe):
        raise SystemExit(f"no exe at {exe} -- run tools/build_exe.py first")

    if notes is None:
        notes = ""
        try:
            with open(out, encoding="utf-8") as f:
                previous = json.load(f)
            if previous.get("version") == VERSION:
                notes = str(previous.get("notes") or "")
        except Exception:
            pass
    notes = str(notes).strip()

    manifest = {
        "version": VERSION,
        "url": f"{BUCKET}/releases/{VERSION}/{NAME}.exe",
        "sha256": sha256_of(exe),
        "notes": notes,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    if not quiet:
        raw = os.path.getsize(exe)
        sig = signature_status(exe)
        print(f"[Manifest] {out}")
        # BINARY megabytes, because that is what the landing page quotes and
        # what the user will see next to the file afterwards.  Windows labels
        # MiB as "MB", so a decimal figure (89.4 for this build) disagrees with
        # Explorer's 85.3 by ~4.6% -- a mismatch that looks like the wrong file
        # on a page whose whole argument is "check this yourself".
        print(f"[Manifest] version {VERSION}   "
              f"{raw / 1048576:.1f} MB  <- put THIS on the landing page")
        print(f"[Manifest] ({raw:,} bytes; {raw / 1e6:.1f} MB decimal, which is "
              f"what a browser shows while downloading)")
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--notes", default=None,
                    help="release notes for this version. Omit to keep the "
                         "notes already in the manifest (same version only).")
    ap.add_argument("--exe", default=None, help="path to the executable")
    ap.add_argument("--out", default=None, help="path to write version.json")
    args = ap.parse_args()

    # Falls back to RELEASE_NOTES because PowerShell DROPS an empty string
    # argument entirely: `--notes ""` with nothing typed into the CI form
    # arrives as a bare `--notes` and argparse rejects it. An environment
    # variable has no such edge case, and cannot be mangled by quoting.
    notes = args.notes
    if not notes:
        notes = os.environ.get("RELEASE_NOTES")

    # Blank still means "none given", not "blank the notes" -- otherwise an
    # ordinary build would wipe notes a previous run had set.
    write(exe=args.exe, out=args.out, notes=(notes if notes else None))
