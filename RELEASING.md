# Releasing a new version

Everything downstream is derived from **one constant**: `VERSION` in
[app/core/update.py](app/core/update.py). The build reads it to name the manifest, build the
download URL, and stamp the exe. Change it in one place, build, and the rest follows.

Bucket root: `https://pub-6ca3288dbdb14574a96b21dac0c7fac1.r2.dev`

---

## The checklist

### 1. Bump the version

In `app/core/update.py`:

```python
VERSION = "1.1.0"
```

### 2. Build

```
cd "C:\Claude Programs\Idleon programs\Automator\app"
python tools/build_exe.py
```

Takes a couple of minutes. At the end it prints the four numbers you need:

```
[Build] C:\...\dist\IdleonAutomator.exe  (89.2 MB)
[Build] version 1.1.0   sha256 bc352501ac9e761b...
```

It also writes `dist/version.json` with a hash that is guaranteed to match the exe beside it.
Never hand-compute that hash.

### 2b. …or build it signed, in CI

A local build is **unsigned**, and an unsigned release makes every user click through
SmartScreen. For a real release, build in CI instead:

**Actions → build → Run workflow → tick "sign" → Run.**

That runs build → sign → hash → upload on a clean Windows runner, and the `IdleonAutomator`
artifact it produces contains the signed exe and a `version.json` whose hash matches it.
Download that artifact and use those two files for steps 3–5 below, in place of anything in
your local `dist/`.

The order is not cosmetic: signing rewrites the executable. Measured on a real request, the
artifact hash changed from `80a893a1…` to `6421e1f6…` through signing, so a manifest written
beforehand describes a file nobody will ever download.

**Signing is deliberately opt-in** — ordinary pushes build but do not sign, so requests are
not spent proving that the code still compiles.

**Note:** CI resolves dependencies fresh, so a CI-built exe is not byte-identical to a local
one and is usually a slightly different size. Whatever you upload is what the landing page's
size and SHA-256 must describe.

### 3. Write the release notes

Open `dist/version.json` and fill in `"notes"` — one plain sentence, since it's what users are
shown in the update tooltip.

```json
{
  "version": "1.1.0",
  "url": "https://pub-6ca3288dbdb14574a96b21dac0c7fac1.r2.dev/releases/1.1.0/IdleonAutomator.exe",
  "sha256": "...",
  "notes": "Refinery reports which material ran out."
}
```

Notes survive a rebuild of the *same* version, so fixing a bug and rebuilding won't lose them.
A version **bump** clears them deliberately — notes describing the previous release would tell
users the wrong thing changed.

### 4. Update the landing page

In [web/index.html](web/index.html), search for **`release block`**. Four values, all together:

| What | Looks like |
|---|---|
| download `href` | `.../releases/1.1.0/IdleonAutomator.exe` |
| size | `89&nbsp;MB` |
| version label | `Version 1.1.0` |
| SHA-256 | the 64-char hash |

All four must match `dist/version.json`. Nothing else on the page carries a version number.

### 5. Upload to R2

Two files, two different places:

| From `dist\` | Object key in the bucket |
|---|---|
| `IdleonAutomator.exe` | `releases/1.1.0/IdleonAutomator.exe` |
| `version.json` | `version.json` (bucket root) |

**Never overwrite an existing `releases/<version>/` file.** Each version folder is immutable —
that's what keeps a published SHA-256 true and old download links working. New version, new
folder.

`version.json` at the root *is* overwritten every release. It's the pointer.

### 6. Deploy the landing page

```
cd "C:\Claude Programs\Idleon programs\Automator"
npx wrangler@3 pages deploy web --project-name=idleonautomater
```

Or in the dashboard: Workers & Pages → the project → Create deployment → drag the **`web`
folder** (not the file, not a zip) → confirm it lands as **Production**, not Preview.

### 7. Verify

```
cd "C:\Claude Programs\Idleon programs\Automator\app"
python tools/check_release.py --deep
```

This reads the **live** manifest the way the app does and checks it against what is really in
the bucket: that `version` matches the folder in `url`, that the URL is HTTPS and resolves,
and — with `--deep` — that the published SHA-256 matches the bytes people will actually
download. Exit code 0 means coherent, 1 means do not announce.

`--deep` downloads the whole exe, so it takes a minute. Run it anyway before announcing; the
fast mode cannot tell you that you uploaded a manifest without its exe, which is the single
easiest mistake to make here.

Then by hand:

- [ ] The download button on `https://idleonautomater.pages.dev/` actually downloads
- [ ] An **older** build, launched, shows the update notice within a second or two

---

## Rolling back

Edit `version.json` at the bucket root to point at the previous version's folder and re-upload
it. The old exe is still there — that's the point of versioned paths. Nothing else to undo.

Users who already downloaded the bad build keep it; task lists are unaffected either way, and
an older exe still reads a newer `tasklists.json`.

## Testing the update loop without making two releases

Upload a `version.json` whose `version` is higher than the build you're running — `1.0.1`
against a 1.0.0 exe — and launch the app. The footer notice should appear. Put the real value
back afterwards.

## Things that will bite you

- **A rebuild changes the hash**, even with no code change. If you rebuild after uploading,
  re-upload the exe or the published hash is a lie.
- **The update check never downloads anything.** It shows a link; the browser does the rest.
  Keep it that way — a small unsigned exe that fetches and runs code is the textbook dropper
  pattern, and this program already synthesises mouse input.
- **`%APPDATA%\IdleonAutomator\` is never touched by a release.** Task lists, run history and
  tuning live there, which is why replacing the exe preserves them.
- **Changing the shape of `tasklists.json`** needs the loader to still read old files. It's
  tolerant today; keep it that way, because that's the file users would be most upset to lose.
