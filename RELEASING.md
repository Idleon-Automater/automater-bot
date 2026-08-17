# Releasing a new version

Bucket: `https://pub-6ca3288dbdb14574a96b21dac0c7fac1.r2.dev`
Site: `https://idleonautomater.pages.dev/`

Everything derives from one constant, so there is only ever one version number to type.

## Who does what

Say "release 1.0.3" and Claude runs steps 1-4, then **stops** and hands over the two files.
The split is not about permission, it is about what a session can reach:

| | Steps | |
|---|---|---|
| **Claude** | 1 2 3 4 | version, build, notes, landing page |
| **You** | **5** | the R2 upload — needs Cloudflare credentials that never live in a session |
| **Claude** | 6 7 8 | deploy the page, verify the release, push |

**The handover is a hard stop.** Step 7 checks the *live* bucket, so it cannot run before the
upload exists — running it early fails on a URL that is not there yet. Claude should pause
after step 4 and say which two files to upload where, not carry on and report a failure it
caused by going too early.

Claude asks rather than decides on:

- **the release notes** — what users are told changed is your call, not a summary of a diff
- **anything that would overwrite an existing `releases/<version>/`** — that invalidates a
  hash somebody may already have checked

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

**Close any running copy of the exe first.** Windows will not let PyInstaller overwrite a file
that is executing, and the build fails part-way through with a permissions error that reads
like something worse.

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

**If the release adds or removes a task, the page describes it in three more places** — all
easy to miss, because the release block is the part you go looking for:

- the **run-list mock-up** in the hero (`<ul class="run">`), with the world's colour dot
- a **task card** in *What it can run*
- the **count sentence** — "Five tasks today"

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

Also confirm the live page really updated, with a request that cannot be served from cache:

```powershell
$r = Invoke-WebRequest "https://idleonautomater.pages.dev/?cachebust=$(Get-Random)" -UseBasicParsing
[regex]::Match($r.Content, 'Version (\d+\.\d+\.\d+)').Value
```

## 8. Push

```
cd "C:\Claude Programs\Idleon programs\Automator"
git push
```

Last, not first: the repo should end up describing the build that actually shipped. The
version bump, the page edits and the notes are all commits, so a release that is not pushed
leaves the public repo claiming the previous version.

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
