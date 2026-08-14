#!/usr/bin/env python3
"""
Is the published release actually coherent?

    python tools/check_release.py            # fast: everything but the hash
    python tools/check_release.py --deep     # also downloads and hashes the exe

Run this AFTER uploading, before telling anyone.  It reads the live manifest
the way the app does and checks it against the files really sitting in the
bucket, because the manifest is three facts that must agree and nothing
enforces that once a human has edited it.

THE FAILURE THIS EXISTS FOR
---------------------------
A manifest that says version 1.0.1 while its url still points at
releases/1.0.0/ is not a broken download -- it is worse.  The app offers the
update, the user downloads 1.0.0, relaunches, and is told 1.0.1 is available
again.  Forever.  Nothing errors; it just never resolves, and the only symptom
is users saying "it keeps asking me to update".

Exit code is 0 when everything agrees and 1 when it does not, so this can gate
a release script.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

TIMEOUT = 20

_problems = []
_checks = 0


def check(ok, label, detail=""):
    global _checks
    _checks += 1
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
    if detail:
        print(f"        {detail}")
    if not ok:
        _problems.append(label)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true",
                    help="download the exe and verify its SHA-256 (slow)")
    ap.add_argument("--url", default=None, help="manifest URL to check")
    args = ap.parse_args()

    from core.update import MANIFEST_URL, VERSION, parse_version
    url = args.url or MANIFEST_URL

    print(f"\nmanifest : {url}")
    print(f"this build says it is version {VERSION}\n")

    # --- the manifest itself -------------------------------------------
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": f"IdleonAutomator/{VERSION}-check"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(64 * 1024).decode("utf-8")
        manifest = json.loads(raw)
    except Exception as e:
        check(False, "manifest downloads and parses", f"{type(e).__name__}: {e}")
        return finish()

    check(True, "manifest downloads and parses")
    print(f"        {json.dumps(manifest)}\n")

    version = str(manifest.get("version") or "")
    exe_url = str(manifest.get("url") or "")
    sha = str(manifest.get("sha256") or "").lower()

    check(bool(parse_version(version)), "version is a readable number",
          f"version = {version!r}")
    check(exe_url.startswith("https://"), "download url is https",
          exe_url or "(missing)")

    # --- the check that matters ----------------------------------------
    # The version must appear in the url's path.  This is the mismatch that
    # produces the endless update loop, and it is invisible by inspection
    # because both halves look perfectly reasonable on their own.
    check(f"/{version}/" in exe_url,
          "url points at THIS version's folder",
          f"expected '/{version}/' in the path, got: {exe_url}")

    check(bool(sha) and len(sha) == 64,
          "sha256 is present and the right length",
          f"{len(sha)} chars")

    # --- does the exe actually exist? ----------------------------------
    size = None
    try:
        head = urllib.request.Request(exe_url, method="HEAD", headers={
            "User-Agent": f"IdleonAutomator/{VERSION}-check"})
        with urllib.request.urlopen(head, timeout=TIMEOUT) as r:
            size = int(r.headers.get("Content-Length") or 0)
        check(True, "the exe is really in the bucket",
              f"{size / 1000 / 1000:.1f} MB")
    except Exception as e:
        check(False, "the exe is really in the bucket",
              f"{type(e).__name__}: {e}")

    # --- and is it the file the manifest describes? --------------------
    if args.deep and size:
        print(f"\n  downloading {size / 1000 / 1000:.1f} MB to verify the hash...")
        try:
            h = hashlib.sha256()
            got = 0
            # Same User-Agent as every other request here.  Cloudflare answers
            # 403 to a bare "Python-urllib/3.x", so leaving it off made this
            # check fail in a way that looks like a permissions problem on the
            # bucket rather than a missing header.
            get = urllib.request.Request(exe_url, headers={
                "User-Agent": f"IdleonAutomator/{VERSION}-check"})
            with urllib.request.urlopen(get, timeout=120) as r:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
                    got += len(chunk)
            check(h.hexdigest() == sha,
                  "downloaded exe matches the published sha256",
                  f"published {sha}\n        actual    {h.hexdigest()}")
        except Exception as e:
            check(False, "downloaded exe matches the published sha256",
                  f"{type(e).__name__}: {e}")
    elif not args.deep:
        print("\n  (skipping the hash check -- pass --deep to download and "
              "verify it)")

    return finish()


def finish():
    print()
    if _problems:
        print(f"{len(_problems)} of {_checks} checks FAILED:")
        for p in _problems:
            print(f"  - {p}")
        print("\nDo not announce this release until these agree.")
        return 1
    print(f"all {_checks} checks passed -- the release is coherent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
