#!/usr/bin/env python3
"""
Build IdleonAutomator.exe -- one file, double-click to run.

    python tools/build_exe.py

Builds from a COPY of the source, assembled by the release allowlist rather
than pointed at the working folder.  That is the whole point: PyInstaller
bundles what it is given, and given the working folder it would happily sweep
in the recordings, the debug dumps and the unknown-sprite folders alongside the
code.  Staging first means the only files that can possibly reach the exe are
the ones `release_audit` names.

WHAT ENDS UP IN THE EXE
-----------------------
Code, the digit masks, the two navigation templates, and the two small config
files.  No screenshots of live play, no run history, no saved task lists --
those are written to the user's own APPDATA at run time, so a fresh copy starts
empty for whoever runs it.

NO PATHS FROM THIS MACHINE
--------------------------
`--workpath` and `--distpath` are pointed inside the staging directory, and the
build is run with its cwd there, so nothing records where this was built.  The
exe is named for the program, not for anyone who made it.
"""

import os
import shutil
import subprocess
import sys
import tempfile

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from release_audit import classify           # noqa: E402

NAME = "IdleonAutomator"


def stage(dest):
    """Copy exactly what the allowlist permits into a clean directory."""
    copied = skipped = 0
    for root, dirs, files in os.walk(_APP):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), _APP).replace("\\", "/")
            verdict, _why = classify(rel)
            if verdict != "SHIP":
                skipped += 1
                continue
            target = os.path.join(dest, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(root, f), target)
            copied += 1
    return copied, skipped


def build():
    tmp = tempfile.mkdtemp(prefix="idleon_build_")
    src = os.path.join(tmp, "src")
    os.makedirs(src)
    copied, skipped = stage(src)
    print(f"[Build] staged {copied} files, left {skipped} behind")

    # Every non-Python file under tasks/, found rather than listed.
    #
    # PyInstaller bundles only what it is told about, and it cannot see a file
    # opened by path at run time -- so a hand-written list has to be updated
    # for every new template, mask or config.  That list was missed twice: the
    # demo package, then the refinery's entry template, which shipped an exe
    # that started fine and then reported "the refinery entrance has not been
    # captured yet" for artwork that was sitting right there in the source.
    # Discovering them removes the whole class of mistake.
    datas = []
    for top in ("tasks", "ui", "core"):
        for root, dirs, files in os.walk(os.path.join(src, top)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith((".py", ".pyc")):
                    continue
                full = os.path.join(root, f)
                rel_dir = os.path.relpath(root, src).replace("\\", "/")
                datas.append((full, rel_dir))
                # ALSO at the bundle root, under the path the engine modules
                # will look for: they import each other flatly, so PyInstaller
                # collects them as top-level modules and their __file__ lands
                # at the bundle root while their data sits under tasks/<pkg>/.
                parts = rel_dir.split("/")
                if len(parts) >= 2 and parts[0] == "tasks":
                    flat = "/".join(parts[2:]) or "."
                    datas.append((full, flat))

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", NAME,
        # The exe's embedded icon: what Explorer and the taskbar show for the
        # file itself, which is a different thing from the window icon Qt sets
        # at run time.
        "--icon", os.path.join(src, "ui", "art", "app_icon.ico"),
        "--workpath", os.path.join(tmp, "work"),
        "--distpath", os.path.join(tmp, "dist"),
        "--specpath", tmp,
        # The engines import each other flatly (`import gamewindow`), which the
        # analyser cannot see through, so their folders go on the path.
        "--paths", "tasks/sushi",
        "--paths", "tasks/minigames",
    ]
    # Absolute sources: --add-data resolves relative paths against the spec
    # directory, not the working directory, so bare names silently miss.
    for full, dest in datas:
        args += ["--add-data", f"{full}{os.pathsep}{dest}"]
    print(f"[Build] bundling {len(datas)} data file(s)")
    # PySide6 ships every Qt module, and PyInstaller bundles what it is not
    # told to leave out.  This program uses widgets and nothing else -- no web
    # view, no 3D, no QML, no multimedia -- and those are the large ones.  The
    # exe is dominated by Qt (212 MB installed) rather than by anything of
    # ours: every image, template and mask we ship comes to 1.6 MB, under 2%.
    for unused in ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
                   "PySide6.QtWebEngineQuick", "PySide6.QtQuick",
                   "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQuickWidgets",
                   "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
                   "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
                   "PySide6.Qt3DExtras", "PySide6.QtCharts",
                   "PySide6.QtDataVisualization", "PySide6.QtMultimedia",
                   "PySide6.QtMultimediaWidgets", "PySide6.QtPdf",
                   "PySide6.QtPdfWidgets", "PySide6.QtDesigner",
                   "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest",
                   "PySide6.QtBluetooth", "PySide6.QtNfc",
                   "PySide6.QtPositioning", "PySide6.QtSensors",
                   "PySide6.QtSerialPort", "PySide6.QtWebSockets",
                   "PySide6.QtWebChannel", "PySide6.QtRemoteObjects",
                   "PySide6.QtScxml", "PySide6.QtSpatialAudio",
                   "PySide6.QtTextToSpeech", "PySide6.QtUiTools",
                   "tkinter", "matplotlib", "PIL", "scipy", "pandas"):
        args += ["--exclude-module", unused]

    # Every task package, found rather than listed: they are imported inside a
    # function, so PyInstaller's analyser never sees them, and a package left
    # out of this list does not fail the build -- it fails on launch, in the
    # user's hands.  That has now happened twice.
    tasks_dir = os.path.join(src, "tasks")
    for entry in sorted(os.listdir(tasks_dir)):
        if os.path.isdir(os.path.join(tasks_dir, entry))                 and entry != "__pycache__":
            args += ["--hidden-import", f"tasks.{entry}"]

    # Imported by name at run time, so the analyser never sees them.
    for hidden in ("sushi_bot", "sushisim", "sushivision", "sushi_watch",
                   "darts_bot", "dartsim", "dartvision", "overworld",
                   "hoopsim", "idleon_hoops_bot", "minigame"):
        args += ["--hidden-import", hidden]
    args.append("run.py")

    print("[Build] running PyInstaller (this takes a couple of minutes)...")
    r = subprocess.run(args, cwd=src)
    if r.returncode != 0:
        print("[Build] FAILED")
        return 1

    out_dir = os.path.join(os.path.dirname(_APP), "dist")
    os.makedirs(out_dir, exist_ok=True)
    exe = os.path.join(tmp, "dist", f"{NAME}.exe")
    final = os.path.join(out_dir, f"{NAME}.exe")
    shutil.copy2(exe, final)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n[Build] {final}  ({os.path.getsize(final) / 1e6:.1f} MB)")
    print("[Build] double-click it; nothing else needs installing.")

    # Write the release manifest next to the exe.  Hand-computing a SHA-256 and
    # hand-editing a version string is exactly the kind of step that gets
    # skipped or mistyped on release day, and a manifest whose hash does not
    # match its exe is worse than publishing no hash.
    #
    # It lives in its own tool because SIGNING REWRITES THE EXE: once there is a
    # signing step it has to run between this build and the manifest, and the
    # manifest has to be regenerated afterwards.  Doing it here as well keeps an
    # unsigned build complete in one command.
    sys.path.insert(0, _APP)
    from core.update import VERSION
    from tools.make_manifest import write as write_manifest

    print()
    write_manifest(exe=final, out=os.path.join(out_dir, "version.json"))
    print(f"[Build] to release: upload the exe to releases/{VERSION}/"
          f"{NAME}.exe, then version.json to the bucket root.")
    print("[Build] SIGNING? sign the exe first, then re-run "
          "tools/make_manifest.py -- signing changes the hash.")
    return 0


if __name__ == "__main__":
    sys.exit(build())
