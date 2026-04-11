# RootRecord Business Manager — private builder repository

**Audience:** internal (you). This is maintainer documentation for source, **Windows** and **Linux** builds, and releases—not polished end-user help.

**Repository purpose:** This GitHub repo (`rootrecord-business-manager-builder`) holds **source code and build tooling**. It is **private**. Built artifacts (`dist/`, PyInstaller work dirs, Windows installers under `build/output/`) are **not** committed. What you ship publicly is normally:

- **Windows:** Inno **installer** `.exe` (primary, tested).
- **Linux (optional):** **tarball** of the PyInstaller folder bundle—treat as **best-effort** (see below).

---

## Platform status (read this first)

| Platform | Role in this project | Testing / confidence |
|----------|----------------------|----------------------|
| **Windows** | **Primary** target. Daily use, installer, SmartScreen/signing notes, most UI paths. | **High** — this is what you build and run day to day. |
| **Linux** | **Secondary / experimental.** Same Python codebase and `build_rootrecord.spec`, but **not** used as the main desktop in this workflow. | **Low — mostly untested.** The spec and a **GitHub Actions** job prove “it builds on Ubuntu.” That is **not** the same as validating every screen, Firebase, cloud backup, tray, PDF export, plugins, or edge cases on real distros. |

### Linux disclaimer (important)

- The **Linux artifact exists so you can pair a tarball with the Windows `.exe` on a public Release**, not because Linux is a fully supported tier yet.
- **CI** (`ubuntu-latest`) only verifies **PyInstaller completes** and packages **`RootRecord-linux-x64.tar.gz`**. It does **not** launch the GUI in a headed environment or run an automated test suite.
- Expect **rough edges**: system **Tk** packages, **Wayland vs X11**, **tray (`pystray`)**, **keyring**, fonts, and file paths may differ from Windows. Some features (e.g. **start-on-login** via Windows registry) have **no Linux equivalent** in-tree unless you add them later.
- **Before** advertising Linux on a public repo, plan a **manual smoke test** on at least one real machine (e.g. Ubuntu LTS desktop): install deps, extract tarball, run `./RootRecord`, clock in/out, open reports, try Firebase if you care.

---

## What the product is

**RootRecord Business Manager (Beta)** is a Python desktop application using **CustomTkinter**. It covers time tracking, money/clients/inventory-style workflows, reporting (e.g. ReportLab, Matplotlib), optional **Firebase Auth** and **encrypted full-database cloud backup** to Firebase Storage, plugins (e.g. power monitoring, USGS earthquake), and system tray behavior (`pystray`).

The main UI and logic live largely in `ui_main.py`, with data access in `data_api.py`, `db.py`, `storage.py`, migrations in `migrations.py`, and domain rules in `tracking_core.py`, `suite_data.py`, etc.

**Entry points**

- **Development:** `python desktop_app.py` → `ui_main.run_app()`.
- **Packaged:** PyInstaller outputs a one-folder bundle; the runnable is `RootRecord.exe` (Windows) or `RootRecord` (Linux).

---

## Tech stack (high level)

| Area | Notes |
|------|--------|
| Language | Python 3.12 recommended (match CI / local builders). |
| UI | `customtkinter` (needs **Tk** at runtime; on Linux install `python3-tk` or your distro’s equivalent). |
| PDF / charts | `reportlab`, `matplotlib` |
| HTTP | `httpx` |
| Secrets / OS store | `keyring`, `python-dotenv` |
| Crypto | `cryptography` |
| Google / Firebase | `google-auth`, `google-auth-oauthlib`; Firebase in `firebase_*.py` |
| Tray | `pystray` (can be finicky on Linux Wayland). |
| Local DB | SQLite (`rootrecord.db` under app data—see paths below). |

Dependencies: `requirements.txt` (runtime) and `requirements-build.txt` (PyInstaller).

---

## Data paths (Windows vs Linux)

| Layout | Windows (frozen) | Linux (frozen) |
|--------|------------------|----------------|
| Default app data root | `%LOCALAPPDATA%\RootRecord` | `~/.rootrecord` |
| Override | Env `ROOTRECORD_HOME` and/or registry-backed `ROOTRECORD_HOME` (see `paths.py`) | Env `ROOTRECORD_HOME` |
| Per-Firebase UID | Under `…/accounts/<uid>/` when signed in | Same relative layout under `~/.rootrecord` |

Development mode (not frozen) still resolves workspace paths from `paths.py` as before.

---

## Repository layout (what matters)

| Path | Purpose |
|------|---------|
| `desktop_app.py` | Loads env, launches UI |
| `ui_main.py` | Main window and most features |
| `app_version.py` | **`APP_VERSION`** — single source for shipped version in-app |
| `paths.py` | App data roots, `ROOTRECORD_HOME`, per-Firebase UID scoping |
| `db.py`, `data_api.py`, `migrations.py`, `schema.sql` | Database layer |
| `firebase_auth_client.py`, `firebase_cloud_backup.py`, `firebase_embedded.py` | Cloud auth and backup |
| `build_rootrecord.spec` | **Shared** PyInstaller spec (Windows version resource only on Windows) |
| `build/build_windows.ps1` | Windows: PyInstaller + Inno installer |
| `build/build_linux.sh` | Linux/WSL: PyInstaller only |
| `build/rootrecord.iss` | Inno Setup — **`MyAppVersion`** must match `app_version.py` |
| `build/build_msix.ps1` | Optional MSIX (Windows / Store) |
| `build/publish_github_release.ps1` | Upload **Windows installer** to GitHub Releases via `gh` |
| `.github/workflows/build-linux.yml` | CI: build Linux tarball artifact (no GUI test) |
| `firebase/FIREBASE_SETUP.md` | Firebase / OAuth / linking |
| `.env.example` | Template — copy to `.env` (gitignored) |

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
# edit .env
python desktop_app.py
```

### Linux

```bash
cd "/path/to/Root Record Business Manager"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env
sudo apt-get install -y python3-tk   # Debian/Ubuntu — required for Tk/CustomTkinter
python desktop_app.py
```

---

## Versioning (before any release)

1. **`app_version.py`** — set `APP_VERSION` (e.g. `"1.3.22"`).
2. **`build/rootrecord.iss`** — `#define MyAppVersion` must match (Windows installer).
3. **MSIX** (if used) — `build/build_msix.ps1` derives version from `app_version.py`.
4. **Commit and tag** so GitHub Releases and CI builds align with source.

PyInstaller’s **Windows** file version block in `build_rootrecord.spec` is generated from `app_version.py` on Windows only.

---

## Building — Windows (primary)

### Prerequisites

- Python + `pip install -r requirements.txt` and `requirements-build.txt` (PyInstaller).
- **Inno Setup 6** (`ISCC.exe`) for the installer.

### One-shot build (recommended)

From repo root:

- **`Build RootRecord.bat`**, or  
- PowerShell:

```powershell
cd "…\Root Record Business Manager\build"
.\build_windows.ps1
```

This:

1. Runs **PyInstaller** (`build_rootrecord.spec`) → `dist\RootRecord\` (gitignored).
2. Runs **Inno** on `build\rootrecord.iss` → **`build\output\`**, typically `RootRecordSetup-Beta-<version>.exe`.

If the output `.exe` is locked (Explorer preview), the script may retry with another filename—read the log.

### MSIX (optional, Windows)

```powershell
cd "…\Root Record Business Manager\build"
.\build_msix.ps1
```

Requires Windows SDK (`makeappx.exe`). See script and `build/msix/` templates.

### Windows: what to ship

- **End users:** the **Inno installer** `.exe** from `build\output\`.
- The raw `dist\RootRecord\` folder is optional (portable-style); not required if you only publish the installer.

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
./dist/RootRecord/RootRecord
```

Keep the **entire** `dist/RootRecord/` directory together (shared libs and `_internal`).

### Continuous integration

- **Workflow:** `.github/workflows/build-linux.yml`
- **Trigger:** pushes to `main`, PRs, or manual **workflow_dispatch**
- **Runner:** `ubuntu-latest` — installs `python3-tk`, runs PyInstaller, creates **`RootRecord-linux-x64.tar.gz`**, uploads an **artifact** named **`RootRecord-linux-x64`**.

This confirms **build reproducibility on a clean Ubuntu image**, not product QA.

### Linux: what to ship

- Distribute **`RootRecord-linux-x64.tar.gz`** (or zip) with short instructions: extract, run `./RootRecord/RootRecord`, install `python3-tk` (or equivalent) if Tk is missing.
- Set expectations: **“Best effort; limited testing”** in public release notes unless you have done real desktop validation.

### Linux: known gaps / risks

- **Tray** and **global shortcuts** may behave differently; Wayland may need extra packages or fall back poorly.
- **OAuth / browser** flows for Google may differ from Windows.
- **Keyring** backends vary; failures may push you toward file-based secrets only on some setups.
- **No** Linux “installer” in this repo yet—only tarball/portable folder.

---

## GitHub Releases (Windows + optional Linux)

### Windows installer (scripted)

1. Install **GitHub CLI** (`winget install GitHub.cli` on Windows).
2. `gh auth login` (HTTPS; `repo` scope for private repos).
3. Build with `build_windows.ps1` so `build\output\RootRecordSetup-Beta-<version>.exe` exists.
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
3. **Manually upload** the Linux tarball to that Release on GitHub (same tag, e.g. `v1.3.22`).
4. In the release description, state clearly that **Linux is minimally tested** and list requirements (`python3-tk`, glibc-ish x64, etc.).

Keeping version parity: use the **same tag** for both files; CI and local Linux builds should use the same `app_version.py` commit.

---

## Cloud / Firebase

- **Auth:** email/password and Google linking — see `firebase_auth_client.py` and **`firebase/FIREBASE_SETUP.md`**.
- **Backup:** encrypted DB snapshot — `firebase_cloud_backup.py` (includes operational notes).
- **Secrets:** prefer `.env`; do not commit live `firebase_embedded.py` credentials.

---

## Git conventions

- **Branch:** `main`
- **Remote:** `https://github.com/RootRecord/rootrecord-business-manager-builder.git`
- **Identity:** set `user.name` / `user.email` as you prefer for commit attribution.

---

## Security checklist (internal)

- [ ] `.env` never committed; rotate leaked keys.
- [ ] `firebase_embedded.py` — no irrecoverable secrets in git history if avoidable.
- [ ] Windows installer: **Authenticode** for SmartScreen (see `build/rootrecord.iss` comments).
- [ ] `gh` token stored in OS credential store — protect machine access.

---

## Troubleshooting

### Windows build

| Symptom | What to check |
|--------|----------------|
| PyInstaller fails | Delete `build/build_rootrecord/` and `dist/`, reinstall deps, rerun. |
| Inno Error 32 / locked file | Close Explorer preview of the output exe; use fallback name from log. |
| Wrong version in Properties | Rebuild after editing `app_version.py`; spec embeds version on Windows. |

### Linux build / runtime

| Symptom | What to check |
|--------|----------------|
| `ModuleNotFoundError` / Tk | Install `python3-tk` (or distro equivalent) **and** rebuild/run in an env that matches. |
| PyInstaller fails on CI but not locally | Pin Python version in Actions; compare `requirements.txt` lock. |
| App starts but no window | Display / Wayland; try X11 session; check `DISPLAY` / WSLg. |
| Tray missing | Wayland / AppIndicator packages; may be unsupported — document limitation. |
| Firebase / keyring errors | Linux keyring daemon; fall back to testing with `.env` only. |

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
| **RootRecord Business Manager (Beta)** | Product name (`APP_DISPLAY_NAME` in `app_version.py`) |
| **rootrecord-business-manager-builder** | This **private** GitHub repo |
| **RootRecord.exe** | Windows PyInstaller entry binary |
| **RootRecord** | Linux PyInstaller entry binary (same role) |
| **RootRecordSetup-Beta-&lt;version&gt;.exe** | Windows Inno installer for end users |
| **RootRecord-linux-x64.tar.gz** | CI-produced Linux folder bundle (mostly untested) |

---

*Update this file when workflows, paths, or platform policy change.*
