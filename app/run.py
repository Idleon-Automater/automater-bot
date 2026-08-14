#!/usr/bin/env python3
"""
Launch the Idleon Automator.

    python run.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def selftest():
    """
    Check that every file a task loads by path is actually present.

    Exists because a packaged build can start perfectly and still be missing
    the artwork a task needs -- the failure surfaces later, as a task refusing
    to run, and looks like a bug in the task.  Writes to a file because the
    released exe is windowed and has nowhere to print.
    """
    from core import registry

    lines, ok = [], True
    for task in registry.available_tasks():
        loc = getattr(task, "location", None)
        paths = [p for p in (getattr(loc, "entry_icon", None),
                             getattr(loc, "popup_icon", None),
                             getattr(loc, "map_icon", None)) if p]
        for p in paths:
            good = os.path.exists(p)
            ok = ok and good
            lines.append(f"{'OK  ' if good else 'MISSING'}  {task.name}: {p}")
    # Data a task loads through its own loader, not via Location -- the sushi
    # board reader is the case that mattered: every file was bundled and the
    # task still refused, so "the files shipped" was not the same question as
    # "the loader can find them".
    try:
        import tasks.sushi.task as _st           # noqa: F401  (sets sys.path)
        import sushivision as V
        tiers = V.load_tiers()
        masks = V.load_digit_masks() if hasattr(V, "load_digit_masks") else None
        lines.append(f"sushi tier templates: {len(tiers) if tiers else 0}")
        lines.append(f"sushi digit masks   : {len(masks) if masks else 0}")
        lines.append(f"sushivision dir     : {os.path.dirname(V.__file__)}")
        for attr in ("TIERS_DIR", "DIGITS_FILE"):
            if hasattr(V, attr):
                pth = getattr(V, attr)
                lines.append(f"   {attr}: exists={os.path.exists(pth)}  {pth}")
        if not tiers and not masks:
            ok = False
    except Exception as e:
        ok = False
        lines.append(f"sushi vision FAILED: {type(e).__name__}: {e}")

    # The map-open anchor: bundled data loaded by path, which is the exact
    # shape of every packaging failure this selftest exists to catch.
    from core.navigate import Navigator
    good = os.path.exists(Navigator.MAP_ANCHOR)
    ok = ok and good
    lines.append(f"{'OK  ' if good else 'MISSING'}  map anchor: "
                 f"{Navigator.MAP_ANCHOR}")

    from tasks.minigames import prompt as _p
    good = os.path.exists(_p.READY_TEMPLATE)
    ok = ok and good
    lines.append(f"{'OK  ' if good else 'MISSING'}  hoops ready icon: "
                 f"{_p.READY_TEMPLATE}")

    from ui.main import ICON
    lines.append(f"window icon: exists={os.path.exists(ICON)}  {ICON}")
    if not os.path.exists(ICON):
        ok = False

    lines.append("ALL PRESENT" if ok else "SOMETHING IS MISSING")
    out = os.path.join(os.path.expanduser("~"), "idleon_selftest.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(chr(10).join(lines))
    return 0 if ok else 1


from ui.main import main

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--diagnose" in sys.argv:
        from tools.diagnose import run as diagnose
        sys.exit(diagnose())
    sys.exit(main())
