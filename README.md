# Idleon Automator

An unofficial desktop assistant for the hands-on parts of
[Legends of Idleon](https://www.legendsofidleon.com/), a free-to-play idle MMO.

Idle games run themselves — except for the parts that don't. A handful of daily jobs still
need a person sitting there clicking: ranking up refinery salts, merging a sushi grid one
tile at a time, playing the same minigame for the same reward. This builds those into a list
you can run in one go.

**[Download the latest release →](https://idleonautomater.pages.dev/)**

## How it works

It is a screen reader and a mouse driver, and nothing more:

- **Reads** the game window with a screen capture, and locates things with OpenCV template
  matching — the same technique as a visual test harness
- **Acts** by moving the system cursor through `user32`, the way any accessibility or macro
  tool does

There is **no memory reading or writing, no code injection, no packet interception, no
modified or redistributed game files, and no network traffic to the game's servers**. It has
no more access to the game than a person watching the screen. If the window is covered, it
stops, because it genuinely cannot see.

## What it can run

| Task | World | What it does |
|---|---|---|
| **Refinery** | 3 | Ranks up every salt that is ready — and only the ones that are ready — then reports which material ran out |
| **Sushi Station** | 7 | Sorts the grid, merges what can be merged, refills with the cook button, repeats |
| **Swishy Hoops** | 1 | Times each shot against the moving hoop, tracks the score, stops when lives run out |
| **Throwy Darts** | 1 | Reads the wind and the moving platform each throw, aiming for bullseyes and the nine-streak |

Each task knows how to travel to itself, so a list can cross the map unattended.

## Before you use it

**It is unofficial.** Not made by, endorsed by, or connected to Lava Flame Studios. Whether
third-party automation is permitted is the game's operator's call, not mine — read their terms
and decide for yourself before running it. It is supervised, single-account, and does nothing
you could not do by hand; that is a design goal, not a legal opinion.

**It can get things wrong.** It clicks what it believes it sees, and a misread can cost
resources. It is written to do nothing when unsure rather than guess — but "written to" is not
"guaranteed to". Watch it the first few times, and press <kbd>F6</kbd> to stop it (twice to
force it and get the mouse back).

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
pip install -r requirements.txt
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

Every push also builds on a clean Windows runner via
[GitHub Actions](.github/workflows/build.yml), so the released binary is traceable to a public
commit rather than to somebody's laptop.

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
