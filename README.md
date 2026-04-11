# RootRecord Business Manager — private builder repository

**Audience:** you only. This document is internal notes for maintaining source, builds, and releases—not end-user documentation.

**Repository purpose:** This GitHub repo (`rootrecord-business-manager-builder`) holds **source code and build tooling** for the Windows desktop app. It is **private**. Built artifacts (`dist/`, PyInstaller work dirs, installers under `build/output/`) are **not** committed; what you ship to anyone else is normally the **Inno installer** (and optionally MSIX), published via **GitHub Releases** or another channel—not this repo’s file tree.

---

## What the product is

**RootRecord Business Manager (Beta)** is a Python desktop application using **CustomTkinter**. It covers time tracking, money/clients/inventory-style workflows, reporting (e.g. ReportLab, Matplotlib), optional **Firebase Auth** and **encrypted full-database cloud backup** to Firebase Storage, plugins (e.g. power monitoring, USGS earthquake), and system tray behavior (`pystray`).

The main UI and logic live largely in `ui_main.py`, with data access in `data_api.py`, `db.py`, `storage.py`, migrations in `migrations.py`, and domain rules in `tracking_core.py`, `suite_data.py`, etc.

Entry point for the packaged app: PyInstaller builds `RootRecord.exe`; development runs through `desktop_app.py` → `ui_main.run_app()`.

---

## Tech stack (high level)

| Area | Notes |
|------|--------|
| Language | Python 3 (match whatever you use locally for builds) |
| UI | `customtkinter` |
| PDF / charts | `reportlab`, `matplotlib` |
| HTTP | `httpx` |
| Secrets / OS store | `keyring`, `python-dotenv` |
| Crypto | `cryptography` |
| Google / Firebase | `google-auth`, `google-auth-oauthlib`; Firebase pieces in `firebase_*.py` |
| Tray | `pystray` |
| Local DB | SQLite (`rootrecord.db` under app data—see paths below) |

Full dependencies: `requirements.txt` (runtime) and `requirements-build.txt` if present for packaging tools.

---

## Repository layout (what matters)

| Path | Purpose |
|------|---------|
| `desktop_app.py` | Loads env, launches UI |
| `ui_main.py` | Main window and most features |
| `app_version.py` | **`APP_VERSION`** string—single source for shipped version in the app UI |
| `paths.py` | `%LOCALAPPDATA%\RootRecord`, optional `ROOTRECORD_HOME`, per-Firebase UID scoping |
| `db.py`, `data_api.py`, `migrations.py`, `schema.sql` | Database layer and migrations |
| `firebase_auth_client.py`, `firebase_cloud_backup.py`, `firebase_embedded.py` | Cloud auth and backup |
| `build_rootrecord.spec` | PyInstaller spec (version read from `app_version.py`) |
| `build/build_windows.ps1` | One-shot: PyInstaller + Inno compile |
| `build/rootrecord.iss` | Inno Setup script; **`MyAppVersion`** must match `app_version.py` before release |
| `build/build_msix.ps1` | Optional MSIX for Store-style packaging |
| `build/publish_github_release.ps1` | Uploads **installer .exe** to GitHub Releases via `gh` |
| `firebase/FIREBASE_SETUP.md` | Firebase / OAuth / linking notes |
| `.env.example` | Template—copy to `.env` locally (`.env` is gitignored) |

Ignored by git (see `.gitignore`): virtualenvs, `__pycache__`, local `data/*.db` and `data/*.json`, `dist/`, `build/output/`, `build/build_rootrecord/`, `build/msix/_stage/`, `.env`.

---

## Local development

1. **Clone** this repo (or use your existing working copy).

2. **Python environment** (recommended):

   ```powershell
   cd "…\Root Record Business Manager"
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Environment variables:** Copy `.env.example` to `.env` and fill in values. Never commit `.env`. If you use Firebase, follow `firebase/FIREBASE_SETUP.md` and keep API keys out of public repos (this repo is private, but treat secrets as sensitive anyway).

4. **Run from source:**

   ```powershell
   python desktop_app.py
   ```

5. **Data location:** Packaged builds use `%LOCALAPPDATA%\RootRecord` (see `paths.py`). You can override with the `ROOTRECORD_HOME` environment variable. When a Firebase user is signed in, data can be scoped under a UID-specific subtree—see `set_desktop_firebase_account_uid` and related helpers.

---

## Versioning (do this before every release)

1. **`app_version.py`** — set `APP_VERSION` (e.g. `"1.3.22"`).

2. **`build/rootrecord.iss`** — set `#define MyAppVersion` to the **same** string (comment at top of `app_version.py` reminds you).

3. **MSIX** — `build/build_msix.ps1` derives the four-part version from `app_version.py`; rebuild MSIX after bumping.

4. **Commit** the version bump on `main` (or your release branch) and push so the tag/release align with source.

PyInstaller’s Windows file version in `build_rootrecord.spec` is generated from `app_version.py` at build time—no need to duplicate by hand there.

---

## Building the Windows executable and installer

### Prerequisites

- Python with dependencies installed (and `pip install pyinstaller` / see `requirements-build.txt` if listed).
- **Inno Setup 6** (`ISCC.exe`) for the installer—typical paths are under `%LOCALAPPDATA%\Programs\Inno Setup 6\` or `Program Files (x86)\Inno Setup 6\`.

### Full build (recommended)

From the project root:

- **`Build RootRecord.bat`** — wrapper that calls the build batch under `build\`.

Or run PowerShell explicitly:

```powershell
cd "…\Root Record Business Manager\build"
.\build_windows.ps1
```

This will:

1. Run **PyInstaller** with `build_rootrecord.spec` → output under `dist\RootRecord\` (gitignored).
2. Run **Inno** on `build\rootrecord.iss` → installer under **`build\output\`** (gitignored), typically named like `RootRecordSetup-Beta-<version>.exe`.

If the default output `.exe` is locked (e.g. Explorer preview), `build_windows.ps1` may retry with a timestamped filename—check the script output.

### MSIX (optional)

After a successful PyInstaller `dist\RootRecord\` build:

```powershell
cd "…\Root Record Business Manager\build"
.\build_msix.ps1
```

Requires Windows SDK (`makeappx.exe`). Partner Center identity placeholders are documented in the script and template.

### What not to commit

Do not add `dist/`, `build/output/`, or `build/build_rootrecord/` to git—the `.gitignore` already excludes them. The **installer** is what you distribute, not the raw PyInstaller folder (unless you intentionally offer a portable zip).

---

## GitHub Releases (publishing installers only)

This repo is configured so **releases attach the Inno installer**, not the whole builder tree.

1. Install **GitHub CLI**: `winget install GitHub.cli`.

2. One-time login:  
   `& "$env:ProgramFiles\GitHub CLI\gh.exe" auth login`  
   Use HTTPS; ensure `repo` scope for private repositories.

3. After `build_windows.ps1` produces `build\output\RootRecordSetup-Beta-<version>.exe`:

   ```powershell
   cd "…\Root Record Business Manager\build"
   .\publish_github_release.ps1
   ```

   The script reads `APP_VERSION` from `app_version.py`, creates tag `v<version>`, uploads the installer, marks **latest** by default, and adds **SHA256** to the release notes.

   Useful flags: `-Draft`, `-NoLatest`, `-Repo "Owner/name"` if `gh` cannot infer the repo, `-InstallerPath` for a custom file path.

4. **Private repo:** Releases are still private to collaborators; grant access on GitHub for anyone who must download the asset.

---

## Cloud / Firebase (short pointer)

- **Auth:** Email/password and Google linking flow—see `firebase_auth_client.py` and setup doc.
- **Backup:** Full encrypted SQLite snapshot to Firebase Storage, with metadata/hash consistency notes in `firebase_cloud_backup.py` module docstring.
- **Configuration:** Prefer `.env` + `firebase_embedded.example.py` patterns; avoid committing live `firebase_embedded.py` contents if they embed secrets (rotate keys if they were ever exposed).

Details: **`firebase/FIREBASE_SETUP.md`**.

---

## Git conventions for this repo

- **Default branch:** `main`.
- **Remote:** `origin` → `https://github.com/RootRecord/rootrecord-business-manager-builder.git`.
- **Commit identity:** If you set `user.name` / `user.email` only locally, that’s fine; use your real GitHub email if you want commits attributed on your profile.

---

## Security checklist (internal)

- [ ] `.env` never committed; rotate any key that was ever in a tracked file.
- [ ] Confirm `firebase_embedded.py` does not contain irreplaceable production secrets—or rotate after sanitizing.
- [ ] Installers: consider **Authenticode** signing for SmartScreen reputation (`build/rootrecord.iss` comments reference this).
- [ ] GitHub token via `gh` is stored in OS credential manager—protect your Windows login.

---

## Troubleshooting

| Symptom | Things to check |
|--------|-------------------|
| Build fails in PyInstaller | Clean `build/build_rootrecord/` and `dist/`, reinstall deps, rerun spec. |
| Inno “file in use” / Error 32 | Close Explorer preview of the output exe; script may fall back to another name. |
| `gh release` wrong repo | Run from a directory under the git root, or pass `-Repo RootRecord/rootrecord-business-manager-builder`. |
| Cloud backup / auth oddities | Firebase console rules, `.env`, clock skew, and logs in the app; see Firebase doc. |
| DB migration errors | `migrations.py` and `schema_migrations` table; back up `.db` before experimenting. |

---

## Naming summary

| Name | Meaning |
|------|---------|
| **RootRecord Business Manager (Beta)** | Product / window title (`APP_DISPLAY_NAME` in `app_version.py`) |
| **rootrecord-business-manager-builder** | This **private** GitHub repo—source + builders |
| **RootRecord.exe** | PyInstaller output |
| **RootRecordSetup-Beta-&lt;version&gt;.exe** | Inno installer for end users |

---

*Last updated to match repo layout and scripts as of the README creation; bump sections when workflows change.*
