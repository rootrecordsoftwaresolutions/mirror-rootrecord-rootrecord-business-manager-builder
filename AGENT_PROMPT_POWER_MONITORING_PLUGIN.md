You are a senior Python desktop engineer working on RootRecord Business Manager (Beta) (CustomTkinter + SQLite + PyInstaller + Inno Setup).

Goal:
1) Evaluate the current plugin architecture and project state.
2) Implement a new "Power Monitoring" plugin (EcoFlow API poller first).
3) Integrate it into the existing Plugins tab/catalog.
4) Build and verify a new Windows .exe and installer.

Important constraints:
- Preserve existing user data and migrations.
- Do not break current time tracking and reporting flows.
- Use additive changes and safe defaults (plugin disabled by default).
- Handle API/network failures gracefully (non-fatal).
- Keep UI readable in dark theme.

Repository/context:
- Main UI: `scripts/time_tracker/ui_main.py`
- Data layer: `scripts/time_tracker/data_api.py`
- Migrations: `scripts/time_tracker/migrations.py`
- Build scripts:
  - `scripts/time_tracker/build/build_windows.ps1`
  - `scripts/time_tracker/build/rootrecord.iss`
  - `scripts/time_tracker/build_rootrecord.spec`
- Version file: `scripts/time_tracker/app_version.py`

Requested plugin behavior (v1):
- Plugin ID: `power_monitoring`
- Name: `Power Monitoring`
- First provider: EcoFlow.
- Poll interval configurable (default 120s, min 30s).
- Settings fields:
  - enabled
  - provider (`ecoflow`)
  - api/base URL and credentials/token (as required by provider docs)
  - device identifier
  - low-battery threshold (default 20%)
  - high-output threshold watts (default 600W)
- Poll data captured:
  - timestamp
  - battery %
  - input watts
  - output watts
  - state/status
  - estimated runtime (if available)
- Store snapshots in SQLite with migration(s).
- Provide a compact plugin status UI in Plugins tab:
  - last poll time
  - last battery %
  - last input/output watts
  - last poll result/error
- Add basic alert event logging in SQLite for:
  - battery below threshold
  - sustained high output (at least two consecutive polls)
  - offline/poll failures

Plugin architecture requirements:
- Add a lightweight plugin contract so future plugins can be added without editing many files:
  - plugin manifest metadata
  - enable/disable state
  - optional background tick/poll hook
  - optional settings panel renderer
- Keep plugin registry centralized and easy to extend.

Implementation guidance:
- Add new migration version for plugin tables (snapshots + alerts + plugin settings if needed).
- Use robust retries/backoff for polling.
- Never block app startup on plugin failures.
- Surface plugin errors in status area, not modal spam.
- Respect dark-theme styling.

Testing/verification checklist:
- Migration runs cleanly on existing DB.
- App launches with plugin disabled (no regressions).
- Enabling plugin starts polling and persists snapshots.
- Network/API failure does not crash app.
- Plugins tab reflects latest poll status.
- Existing dashboard/work-log/report features still function.
- Build succeeds for exe and installer.

Build steps to run:
1) Bump version in:
   - `scripts/time_tracker/app_version.py`
   - `scripts/time_tracker/build/rootrecord.iss`
2) Build:
   - `powershell -ExecutionPolicy Bypass -File "build/build_windows.ps1"` (from `scripts/time_tracker`)
3) Confirm artifacts:
   - `scripts/time_tracker/dist/RootRecord`
   - `scripts/time_tracker/build/output/RootRecordSetup-<version>.exe`

Deliverables expected:
- Code changes with clear file-level summary.
- Migration notes.
- Any new dependencies and why.
- Exact build artifact filenames/paths.
- Brief risk list and follow-up improvements for plugin v2.
