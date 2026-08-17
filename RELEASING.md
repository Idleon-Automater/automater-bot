# Releasing a new version

Bucket: `https://pub-6ca3288dbdb14574a96b21dac0c7fac1.r2.dev`
Site: `https://idleonautomater.pages.dev/`

Everything derives from one constant, so there is only ever one version number to type.

## Who does what

Say "release 1.0.3" and Claude runs everything below except step 5, then stops and asks for
the upload. The split is not about permission, it is about what a session can reach:

| | Steps | Why |
|---|---|---|
| **Claude** | 1, 2, 3, 4, 6, 7 | Local commands, plus the push and the Pages deploy |
| **You** | 5 — the R2 upload | Needs Cloudflare credentials that never live in a session |

Two things Claude should ask about rather than decide: the **release notes** (what users are
told changed is your call, not a summary of the diff), and anything that would **overwrite an
existing `releases/<version>/`**.

---

## 1. Set the version

`app/core/update.py`, one line:

```python
VERSION = "1.0.1"
```

## 2. Build

```
cd "C:\Claude Programs\Idleon programs\Automator\app"
python tools/build_exe.py
```

Two minutes. Prints the size and SHA-256 at the end, and writes `dist/version.json` to match.

*Once you have a real certificate:* build in CI instead — **Actions → build → Run workflow →
tick "sign", type the release notes → Run** — then download the `IdleonAutomator` artifact and
use those two files below instead of your local `dist/`.

## 3. Write the release notes

In `dist/version.json`, fill in `"notes"` — one sentence, shown to users in the update tooltip.

## 4. Update the landing page

`web/index.html`, search for **`release block`**. Four values, all next to each other:

- download link → `.../releases/1.0.1/IdleonAutomator.exe`
- size — the **binary** MB the build prints as *"put THIS on the landing page"*, not the
  decimal one. Windows calls MiB "MB", so the decimal figure disagrees with what the user
  sees beside the downloaded file by about 5%
- `Version 1.0.1`
- the SHA-256

The link and the hash must match `dist/version.json` exactly.

## 5. Upload to R2  — *yours*

| From `dist\` | Goes to |
|---|---|
| `IdleonAutomator.exe` | `releases/1.0.1/IdleonAutomator.exe` |
| `version.json` | bucket root |

**Never overwrite an old `releases/<version>/` folder.** New version, new folder — that is what
keeps published hashes true and old download links alive.

## 6. Deploy the page

```
cd "C:\Claude Programs\Idleon programs\Automator"
npx wrangler@3 pages deploy web --project-name=idleonautomater --commit-dirty=true
```

**From the project root, not from `app/`** -- `web/` sits at the root, and running it from
`app/` fails with `ENOENT: no such file or directory, scandir '...\app\web'`.

Wrangler prints a per-deployment URL like `c4f5374a.idleonautomater.pages.dev`. That is not
the live site; check `idleonautomater.pages.dev` itself, and do it with a fresh request --
a cached response will happily show you the previous version and look like a failed deploy.

## 7. Check before announcing

```
cd "C:\Claude Programs\Idleon programs\Automator\app"
python tools/check_release.py --deep
```

Green means the live manifest, the live exe and the published hash all agree. Red means don't
announce it yet. Takes a minute — it downloads the exe to verify the hash for real.

Then click the download button on the site once, to be sure.

---

## If something goes wrong

**Roll back:** edit `version.json` at the bucket root to point at the previous version's folder
and re-upload it. The old exe is still there. Nothing else to undo.

**Rebuilt after uploading?** The hash changed — re-upload the exe, or the published hash is
wrong. `check_release.py --deep` catches this.

**Task lists are never at risk.** They live in `%APPDATA%\IdleonAutomator\`, untouched by any
release, which is why replacing the exe keeps them.

*Why each step is the way it is — the signing order, the immutable folders, the hash rules —
is documented in the tools themselves: `build_exe.py`, `make_manifest.py`, `check_release.py`.*
