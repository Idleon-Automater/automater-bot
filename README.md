# Idleon Automator

A free, portable Windows program that plays the tedious parts of
[Legends of Idleon](https://www.legendsofidleon.com/) for you. Build a list of tasks, press
Run, and go do something else.

It works by reading the screen and moving the mouse — no memory editing, no packet
manipulation, no modified game files. It travels between worlds, opens each screen, and does
the work the way a person would.

**[Download the latest release →](https://idleonautomater.pages.dev/)**

---

## ⚠️ Read this before using it

**Automation may break the game's rules.** Legends of Idleon is made by Lava Flame Studios,
who set the terms for how it may be played. This project is not made by, endorsed by, or
connected to Lava in any way. Using it could get your account suspended or banned. That risk
is yours to take, and it is a real one.

**It can get things wrong.** It reads the screen and clicks what it believes it sees. A
misread costs a click, and some clicks cost resources. It is written to do nothing when it is
unsure rather than guess — but "written to" is not "guaranteed to".

**Watch it the first few times.** Press <kbd>F6</kbd> to stop, twice to force it and get your
mouse back.

## What it can run

| Task | World | What it does |
|---|---|---|
| **Refinery** | 3 | Ranks up every salt that is ready — and only the ones that are ready — then reports which material ran out |
| **Sushi Station** | 7 | Sorts the grid, merges what can be merged, refills with the cook button, repeats |
| **Swishy Hoops** | 1 | Times each shot against the moving hoop, tracks the score, stops when lives run out |
| **Throwy Darts** | 1 | Reads the wind and the moving platform each throw, aiming for bullseyes and the nine-streak |

Each task knows how to travel to itself, so a list can cross the map unattended.

## Your data

Nothing leaves your computer. No account, no telemetry, no server.

Task lists, run history and tuning live in `%APPDATA%\IdleonAutomator\` — outside the
executable, which is why replacing the program with a newer one keeps them.

The one exception: on startup it asks a small JSON file on Cloudflare whether a newer version
exists. That tells Cloudflare your IP address, the way visiting any website does. If there is
an update you get a link — it never downloads or installs anything by itself. See
[`app/core/update.py`](app/core/update.py); it is about a hundred lines and does exactly that.

## Running from source

Windows, Python 3.11+.

```
pip install PySide6 opencv-python numpy mss pywin32
cd app
python run.py
```

## Building the executable

```
cd app
python tools/build_exe.py
```

Produces a single portable `dist/IdleonAutomator.exe` (~89 MB — OpenCV, Qt and numpy) plus
`dist/version.json`. No installer; it writes nothing outside `%APPDATA%`.

Release process, including signing and verification: **[RELEASING.md](RELEASING.md)**.

## Layout

```
app/
  core/      window capture, humanised input, navigation, task lists, update check
  tasks/     one package per task, each with its own nav templates
  ui/        the window
  tools/     build, release audit, manifest, release validation
web/         the landing page (static, self-contained)
```

Two things worth knowing if you contribute:

- **All synthetic input is humanised** — eased velocity, an arc off the straight line,
  per-step jitter, randomised holds. Anything new that moves the pointer should go through
  `Clicker.human_move()` rather than calling `SetCursorPos`. Timing-critical presses stay
  exactly timed; the humanised approach runs early instead.
- **`tools/release_audit.py` is an allowlist.** A release contains what it names and nothing
  else, because forgetting to exclude a file leaks it silently while forgetting to include one
  crashes obviously.

## Licence

[MIT](LICENSE).

## Not affiliated

Legends of Idleon and all game assets are the property of Lava Flame Studios. This project
contains no game code or assets beyond small on-screen reference crops used to recognise
buttons and menus.
