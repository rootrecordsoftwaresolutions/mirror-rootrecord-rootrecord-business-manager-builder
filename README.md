# RootRecord Business Manager — private builder repository

## End-user documentation

For **customers, evaluators, and buyers**, the product overview (features, privacy, system requirements, FAQ, support links) lives in **[`docs/PRODUCT_README.md`](docs/PRODUCT_README.md)**. This root README stays focused on building and maintaining the app.

To publish **both** GitHub targets in one go (private **builder**: `main` + tag + source release; public **download**: sync `docs/PRODUCT_README.md` into the download clone, `git push`, **signed** PyInstaller/Inno build, installer release), run **`build\Publish-Dual-Releases.bat`** (defaults to **`-Sign`**; use **`-NoSign`** only for local test publishes). See **`build\publish_dual_releases.ps1`** for `-SkipBuild` and skip-if-tag-exists switches.

To refresh the **customer-facing GitHub repo** only (README + images in git; Windows installer only on **Releases**), run **`build\sync_public_github_docs.ps1`**, then commit and push from **`rootrecord-business-manager-download`** (nested under this tree or next to this package, wherever your `.git` clone lives). Site: **https://github.com/RootRecord/rootrecord-business-manager-download** — publish the current-version installer with **`build\publish_product_release.ps1`** (uses `build\output\RootRecordSetup-<APP_VERSION>.exe` only).

The slug **`rootrecord-business-manager`** redirects to the private builder repo on this org, so the product page uses **`rootrecord-business-manager-download`** instead.

---

**Audience:** internal (you). This is maintainer documentation for source, **Windows** and **Linux** builds, and releases—not polished end-user help.

**Repository purpose:** This GitHub repo (`rootrecord-business-manager-builder`) holds **source code and build tooling**. It is **private**. Built artifacts (`dist/`, PyInstaller work dirs, Windows installers under `build/output/`) are **not** committed. What you ship publicly is normally:

- **Windows:** Inno **installer** `.exe` (primary, tested).
- **Linux (optional):** **tarball** of the PyInstaller folder bundle—treat as **best-effort** (see below).

**Pushing to GitHub:** `git push`, **GitHub Actions** (`.github/workflows/build-linux.yml`), and **`build/publish_github_release.ps1`** are unchanged. The app no longer includes an **in-app “check GitHub for a newer installer”** updater; distributing updates is via your normal **Releases** workflow (installer + optional Linux tarball), not from inside the running app.

---

## Platform status (read this first)

| Platform | Role in this project | Testing / confidence |
|----------|----------------------|----------------------|
| **Windows** | **Primary** target. Daily use, installer, SmartScreen/signing notes, most UI paths. | **High** — this is what you build and run day to day. |
| **Linux** | **Secondary / experimental.** Same Python codebase and `build_rootrecord.spec`, but **not** used as the main desktop in this workflow. | **Low — mostly untested.** The spec and a **GitHub Actions** job prove “it builds on Ubuntu.” That is **not** the same as validating every screen, tray, PDF export, plugins, or edge cases on real distros. |

### Linux disclaimer (important)

- The **Linux artifact exists so you can pair a tarball with the Windows `.exe` on a public Release**, not because Linux is a fully supported tier yet.
- **CI** (`ubuntu-latest`) only verifies **PyInstaller completes** and packages **`RootRecord-linux-x64.tar.gz`**. It does **not** launch the GUI in a headed environment or run an automated test suite.
- Expect **rough edges**: system **Tk** packages, **Wayland vs X11**, **tray (`pystray`)**, fonts, and file paths may differ from Windows. Some features (e.g. **start-on-login** via Windows registry) have **no Linux equivalent** in-tree unless you add them later.
- **Before** advertising Linux on a public repo, plan a **manual smoke test** on at least one real machine (e.g. Ubuntu LTS desktop): install deps, extract tarball, run `./RootRecord`, clock in/out, open reports.

---

## What the product is

**RootRecord Business Manager** is a Python desktop application using **CustomTkinter**. It covers time tracking, money/clients/inventory-style workflows, reporting (ReportLab, Matplotlib), **local SQLite** storage, optional plugins (e.g. power monitoring, USGS earthquake), and system tray behavior (`pystray`). **Optional subscription checks** use a small **remote license API** over HTTPS plus **Stripe** payment links—configure `LICENSE_API_BASE_URL` / `LICENSE_API_SECRET` in `.env` if you use that flow. There is **no** in-app GitHub updater and **no** bundled cloud identity provider; the app is local-first with optional licensing over HTTPS.

The main UI and logic live largely in `ui_main.py`, with data access in `data_api.py`, `db.py`, `storage.py`, migrations in `migrations.py`, and domain rules in `tracking_core.py`, `suite_data.py`, etc.

**Entry points**

- **Development:** `python desktop_app.py` → `ui_main.run_app()`.
- **Packaged:** PyInstaller outputs a one-folder bundle; the runnable is `RootRecordBusinessManager.exe` (Windows) or `RootRecordBusinessManager` (Linux).

---

## Tech stack (high level)

| Area | Notes |
|------|--------|
| Language | Python 3.12 recommended (match CI / local builders). |
| UI | `customtkinter` (needs **Tk** at runtime; on Linux install `python3-tk` or your distro’s equivalent). |
| PDF / charts | `reportlab`, `matplotlib` |
| HTTP | `httpx` (plugins / external APIs) |
| Config | `python-dotenv` (optional `.env`) |
| Tray | `pystray` (can be finicky on Linux Wayland). |
| Local DB | SQLite (`rootrecord.db` under app data—see paths below). |

Dependencies: `requirements.txt` (runtime) and `requirements-build.txt` (PyInstaller).

---

## Data paths (Windows vs Linux)

| Layout | Windows (frozen) | Linux (frozen) |
|--------|------------------|----------------|
| Default app data root | `%LOCALAPPDATA%\RootRecord` | `~/.rootrecord` |
| Override | Env `ROOTRECORD_HOME` and/or registry-backed `ROOTRECORD_HOME` (see `paths.py`) | Env `ROOTRECORD_HOME` |

Development mode (not frozen) resolves workspace paths from `paths.py` as documented there.

**Logs:** `data/logs/rootrecord.log` under the same app data tree (see `log_config.py`) — warnings/errors from startup and license checks; useful when debugging.

**Backups:** Database backups are **local** by default (Program Settings → backup folder). **Optional:** Account Settings can turn on a **secure online copy** of each new backup; the app handles connection details internally (operator notes in `cloudflare/README.md` if you ship your own online services).

---

## Repository layout (what matters)

| Path | Purpose |
|------|---------|
| `desktop_app.py` | Loads env, logging, launches UI |
| `log_config.py` | File logging under `data/logs/` |
| `ui_main.py` | Main window and most features |
| `app_version.py` | **`APP_VERSION`** — single source for shipped version in-app |
| `paths.py` | App data roots, `ROOTRECORD_HOME` |
| `db.py`, `data_api.py`, `migrations.py` | Database layer |
| `build_rootrecord.spec` | **Shared** PyInstaller spec (Windows version resource only on Windows) |
| `build/build_windows.ps1` | Windows: PyInstaller + Inno installer |
| `build/build_linux.sh` | Linux/WSL: PyInstaller only |
| `build/rootrecord.iss` | Inno Setup — **`MyAppVersion`** must match `app_version.py` |
| `build/build_msix.ps1` | Optional MSIX (Windows / Store) |
| `build/publish_github_release.ps1` | Upload **Windows installer** to GitHub Releases via `gh` |
| `.github/workflows/build-linux.yml` | CI: build Linux tarball artifact (no GUI test) |
| `.env.example` | Template — copy to `.env` (gitignored); optional `SQLITE_PATH` |

Ignored by git: `.gitignore` covers `dist/`, `build/output/`, `build/build_rootrecord/`, `build/msix/_stage/`, `.env`, venvs, local DBs under `data/`, etc.

---

## Local development (from source)

### Windows

```powershell
cd "…\Root Record Business Manager"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edit .env if needed (optional SQLITE_PATH)
python desktop_app.py
```

### Linux

```bash
cd "/path/to/Root Record Business Manager"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
sudo apt-get install -y python3-tk   # Debian/Ubuntu — required for Tk/CustomTkinter
python desktop_app.py
```

---

## Versioning (before any release)

1. **`app_version.py`** — set `APP_VERSION` (e.g. `"1.3.24"`).
2. **`build/rootrecord.iss`** — `#define MyAppVersion` must match (Windows installer).
3. **MSIX** (if used) — `build/build_msix.ps1` derives version from `app_version.py`.
4. **Commit and tag** so GitHub Releases and CI builds align with source.

PyInstaller’s **Windows** file version block in `build_rootrecord.spec` is generated from `app_version.py` on Windows only.

### Release history (builder)

**1.3.45**

- **Work Log:** Lists **session markers** (clock in/out, breaks, etc.) together with time blocks; click a `SESSION id=…` line to edit timestamp + detail or delete (with undo). Asset search for **About** includes `docs/assets` and extra filenames (`about.png`, …).

**1.3.44**

- **Cloud sync — time entries:** Migration **19** adds stable `client_uuid` on `rr_time_entries`; `sync_engine` pushes/pulls `time_entry` upserts/deletes (same Worker as sign-in). The Flutter companion under `mobile/rootrecord_business_manager` can show those rows and submit new ones.

**1.3.43**

- **Store / certification silent install:** `rootrecord.iss` — silent (`/VERYSILENT`) installs no longer leave Terms on “decline” by default; finish text documents launch + uninstall; extra Start-menu shortcut under Programs root; explicit `CreateUninstallRegKey=yes`; publisher string aligned to **Root Record** for ARP/Settings.

**1.3.42**

- **Smart App Control:** `sign_release_azure.ps1` signs **all** `*.exe`, `*.dll`, and `*.pyd` under `dist\RootRecordBusinessManager\` by default (opt out with **`-SkipBundledNative`**). File / product version bumped via **`app_version.py`** (PyInstaller version resource).

**1.3.40**

- **Trust / strict Windows:** With **`-Sign`** and **`artifact_signing_metadata.json`**, `build_windows.ps1` passes **`/DInnoSignAzure`** and **`/SAzureInno=...`** to ISCC so **`sign_inno_azure.ps1`** signs **Inno’s internal setup/uninstall binaries during compile**—not only the final `Setup.exe`. That addresses **Application Control Error 4551** (unsigned code under `%TEMP%`) and improves behavior on **default-security** PCs.
- Build order unchanged: **app exe signed before Inno**; when Inno SignTool runs, **no second post-Inno sign** of the installer.

**1.3.39**

- **Windows signing:** `Build RootRecord.bat` runs **Azure Trusted Signing** by default (`build\sign_release_azure.ps1` + `artifact_signing_metadata.json`, gitignored—copy from `artifact_signing_metadata.sample.json`). The **app exe is signed before Inno** so Authenticode is **inside** the installer. Use **`Build RootRecord.bat nosign`** for unsigned local builds.
- **Build reliability:** `build_windows.ps1` renames aside a locked `dist\RootRecordBusinessManager` tree before PyInstaller, checks Python exit code, and calls the sign script with real parameters (not string splats).
- **Cursor / locks:** `.cursorignore` excludes `dist/` so the IDE is less likely to hold file handles during rebuilds.

---

## Building — Windows (primary)

### Prerequisites

- Python + `pip install -r requirements.txt` and `requirements-build.txt` (PyInstaller).
- **Inno Setup 6** (`ISCC.exe`) for the installer.

### One-shot build (recommended)

From repo root:

- **`Build RootRecord.bat`** — default: **PyInstaller + Inno + Azure code signing** (needs `build\artifact_signing_metadata.json` and signing tools; stops running app images before build/sign).  
- **`Build RootRecord.bat nosign`** — same build **without** Azure signing (faster local iteration).
- **`build\build_windows.cmd`** — same defaults as `build_rootrecord.bat` (**`-Sign -StopRunningApp`**); **`nosign`** as first argument skips signing.
- Or from PowerShell:

```powershell
cd "…\Root Record Business Manager\build"
.\build_windows.ps1              # build + installer only
.\build_windows.ps1 -Sign -StopRunningApp   # same as default .bat (sign app, then Inno, then sign installer)
```

This:

1. Runs **PyInstaller** (`build_rootrecord.spec`) → `dist\RootRecordBusinessManager\` (gitignored).
2. When **`-Sign`**: signs `dist\…\RootRecordBusinessManager.exe`, then runs **Inno** so the **signed** exe is what gets packaged.
3. Runs **Inno** on `build\rootrecord.iss` → **`build\output\`**, typically `RootRecordSetup-<version>.exe`.
4. When **`-Sign`**: signs the installer `.exe` in `build\output\`.

If the output `.exe` is locked (Explorer preview), the script may retry with another filename—read the log.

### MSIX (optional, Windows)

Double-click **`Build RootRecord MSIX.bat`** at the repo root to run a **signed** `build_windows.ps1` pass, then **`build\build_msix.ps1`** (packages the signed `dist\` tree).

Or from PowerShell only (expects `dist\` already built, ideally signed):

```powershell
cd "…\Root Record Business Manager\build"
.\build_msix.ps1
```

Requires Windows SDK (`makeappx.exe`). See script and `build/msix/` templates.

### Windows: what to ship

- **End users:** the **Inno installer** `.exe` from `build\output\`.
- The raw `dist\RootRecordBusinessManager\` folder is optional (portable-style); not required if you only publish the installer.

---

## Building — Linux (mostly untested, CI-only smoke)

Same **`build_rootrecord.spec`**, but **no** Windows `version` resource; optional **`icon.png`** in the repo (or parent workspace) for the binary icon.

### Prerequisites (example: Debian/Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv python3-tk
```

Other distros: install the packages that provide **Tk** for your Python.

### Local / WSL build

```bash
cd "/path/to/Root Record Business Manager"
pip install -r requirements.txt -r requirements-build.txt
bash build/build_linux.sh
```

Run:

```bash
./dist/RootRecordBusinessManager/RootRecordBusinessManager
```

Keep the **entire** `dist/RootRecordBusinessManager/` directory together (shared libs and `_internal`).

### Continuous integration

- **Workflow:** `.github/workflows/build-linux.yml`
- **Trigger:** pushes to `main`, PRs, or manual **workflow_dispatch**
- **Runner:** `ubuntu-latest` — installs `python3-tk`, runs PyInstaller, creates **`RootRecord-linux-x64.tar.gz`**, uploads an **artifact** named **`RootRecord-linux-x64`**.

This confirms **build reproducibility on a clean Ubuntu image**, not product QA.

### Linux: what to ship

- Distribute **`RootRecord-linux-x64.tar.gz`** (or zip) with short instructions: extract, run `./RootRecordBusinessManager/RootRecordBusinessManager`, install `python3-tk` (or equivalent) if Tk is missing.
- Set expectations: **“Best effort; limited testing”** in public release notes unless you have done real desktop validation.

### Linux: known gaps / risks

- **Tray** and **global shortcuts** may behave differently; Wayland may need extra packages or fall back poorly.
- **No** Linux “installer” in this repo yet—only tarball/portable folder.

---

## GitHub Releases (Windows + optional Linux)

**Uploading to GitHub** (source + assets) is done with **git** and **GitHub CLI**—not affected by removing in-app update checks.

### Windows installer (scripted)

1. Install **GitHub CLI** (`winget install GitHub.cli` on Windows).
2. `gh auth login` (HTTPS; `repo` scope for private repos).
3. Build with `build_windows.ps1` so `build\output\RootRecordSetup-<version>.exe` exists.
4. Publish:

```powershell
cd "…\Root Record Business Manager\build"
.\publish_github_release.ps1
```

Uses `APP_VERSION` from `app_version.py`, tag `v<version>`, SHA256 in notes. Flags: `-Draft`, `-NoLatest`, `-Repo`, `-InstallerPath`.

### Adding the Linux tarball to the same Release

`publish_github_release.ps1` only uploads the **Windows** installer today. For a **public** Release that pairs both:

1. Create the release (draft or publish) with the Windows asset as you already do.
2. Download **`RootRecord-linux-x64.tar.gz`** from the **Actions** workflow artifact (successful run on the same commit/tag).
3. **Manually upload** the Linux tarball to that Release on GitHub (same tag, e.g. `v1.3.24`).
4. In the release description, state clearly that **Linux is minimally tested** and list requirements (`python3-tk`, glibc-ish x64, etc.).

Keeping version parity: use the **same tag** for both files; CI and local Linux builds should use the same `app_version.py` commit.

---

## Git conventions

- **Branch:** `main`
- **Remote:** `https://github.com/RootRecord/rootrecord-business-manager-builder.git`
- **Identity:** set `user.name` / `user.email` as you prefer for commit attribution.

---

## Security checklist (internal)

- [ ] `.env` never committed if it contains machine-specific paths you do not want shared.
- [ ] Windows installer: **Authenticode** for SmartScreen (see `build/rootrecord.iss` comments).
- [ ] `gh` token stored in OS credential store — protect machine access.

---

## Troubleshooting

### Windows build

| Symptom | What to check |
|--------|----------------|
| PyInstaller fails | Delete `build/build_rootrecord/` and `dist/`, reinstall deps, rerun. |
| PyInstaller `WinError 32` on `dist\…\exe` | Close Explorer on `dist\`, reload Cursor after `.cursorignore` includes `dist/`, or run the build from a plain **cmd** window outside the IDE. |
| Inno **Error 4551** / Application Control / “temporary directory” | Signing only the final `Setup.exe` after compile leaves **unsigned** code Inno drops under `%TEMP%`. Use **`Build RootRecord.bat`** (default **`-Sign`**) with **`build\artifact_signing_metadata.json`** so **`build_windows.ps1`** passes **`/DInnoSignAzure`** + **`/SAzureInno=...`** to ISCC; that runs **`sign_inno_azure.ps1`** for each binary Inno needs. |
| Inno Error 32 / locked file | Close Explorer preview of the output exe; use fallback name from log. |
| Wrong version in Properties | Rebuild after editing `app_version.py`; spec embeds version on Windows. |

### Linux build / runtime

| Symptom | What to check |
|--------|----------------|
| `ModuleNotFoundError` / Tk | Install `python3-tk` (or distro equivalent) **and** rebuild/run in an env that matches. |
| PyInstaller fails on CI but not locally | Pin Python version in Actions; compare `requirements.txt` lock. |
| App starts but no window | Display / Wayland; try X11 session; check `DISPLAY` / WSLg. |
| Tray missing | Wayland / AppIndicator packages; may be unsupported — document limitation. |

### Releases / git

| Symptom | What to check |
|--------|----------------|
| `gh release` wrong repo | Run from clone with `.git`, or `-Repo RootRecord/rootrecord-business-manager-builder`. |
| Linux tarball missing on Release | Artifacts are per-workflow — download and attach manually to the versioned Release. |

### Database

| Symptom | What to check |
|--------|----------------|
| Migration errors | `migrations.py`, `schema_migrations`; back up `.db` before experiments. |

---

## Naming summary

| Name | Meaning |
|------|---------|
| **RootRecord Business Manager** | Product name (`APP_DISPLAY_NAME` in `app_version.py`) |
| **rootrecord-business-manager-builder** | This **private** GitHub repo |
| **RootRecordBusinessManager.exe** | Windows PyInstaller entry binary |
| **RootRecord** | Linux PyInstaller entry binary (same role) |
| **RootRecordSetup-&lt;version&gt;.exe** | Windows Inno installer for end users |
| **RootRecord-linux-x64.tar.gz** | CI-produced Linux folder bundle (mostly untested) |

---

*Update this file when workflows, paths, or platform policy change.*
