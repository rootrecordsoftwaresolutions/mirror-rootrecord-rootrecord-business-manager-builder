You are a senior Python desktop engineer working on RootRecord Business Manager (Beta) (CustomTkinter + SQLite + PyInstaller + Inno Setup).

Goal:
1) Evaluate current plugin runtime/catalog implementation.
2) Implement a new plugin: `USGS Earthquake Alerts`.
3) Integrate plugin settings + status into existing Plugins tab system.
4) Add local alerting for new earthquakes near configured business location(s).
5) Build and verify a new Windows .exe and installer.

Project context:
- Main UI: `scripts/time_tracker/ui_main.py`
- Plugin runtime scaffold: `scripts/time_tracker/plugin_runtime.py`
- Data layer: `scripts/time_tracker/data_api.py`
- Migrations: `scripts/time_tracker/migrations.py`
- Build:
  - `scripts/time_tracker/build/build_windows.ps1`
  - `scripts/time_tracker/build/rootrecord.iss`
  - `scripts/time_tracker/build_rootrecord.spec`
- Version: `scripts/time_tracker/app_version.py`

Core requirements:
- Plugin ID: `usgs_earthquake_alerts`
- Plugin name: `USGS Earthquake Alerts`
- Source API: USGS Earthquake GeoJSON feed and/or query endpoint.
- Plugin must be optional and disabled by default.
- No app crashes on network/API errors.

Functional behavior (v1):
1) Configurable settings:
   - enable/disable
   - polling interval seconds (default 300, min 60)
   - business latitude/longitude
   - alert radius (miles/km; choose one and keep consistent)
   - minimum magnitude for alert (default 2.5)
   - optional quiet hours (start/end local time)
2) Poll USGS for recent events.
3) Filter to events that are:
   - new (not already seen),
   - within radius,
   - above min magnitude.
4) Store results and alerts in SQLite.
5) Surface plugin status:
   - last poll time
   - last successful poll
   - count of recent matching events
   - latest error (if any)
6) Alert delivery (desktop-safe):
   - in-app notification/messagebox (rate-limited)
   - log alert row in DB
   - never spam repeatedly for same quake id

Data model requirements:
- Add migration(s) for:
  - `usgs_quake_events` (event id, time, mag, place, lat/lon, depth, url, raw json, received_at)
  - `usgs_quake_alerts` (event id, alerted_at, distance, rule snapshot, acknowledged flag)
- Ensure unique constraint on USGS event id to prevent duplicates.

Plugin architecture requirements:
- Register plugin in runtime catalog (builtin entry and/or manifest support).
- Use existing plugin enabled setting pattern: `plugin_enabled_<id>`.
- Add plugin-specific settings keys under clear namespace, e.g.:
  - `plugin_usgs_lat`
  - `plugin_usgs_lon`
  - `plugin_usgs_radius_miles`
  - `plugin_usgs_min_magnitude`
  - `plugin_usgs_poll_interval_sec`
- Keep implementation modular for future hazard plugins.

UI requirements:
- In Plugins tab, expose a compact config panel for USGS plugin when selected/enabled.
- Show recent quake matches list (time, mag, place, distance).
- Add “Test Alert” action (simulated local notification) for validation.
- Keep dark-theme readability consistent.

Reliability + safety:
- Apply retry with short backoff for temporary API failures.
- Respect poll interval; avoid aggressive request loops.
- If offline/error, update plugin status text but continue app operation.
- Keep all failures non-fatal.

Testing checklist:
- Migration applies cleanly on existing DB.
- Enabling plugin begins polling; disabling stops polling.
- Duplicate events are not re-alerted.
- Distance/magnitude filtering works.
- Quiet hours suppress popup alerts but still log events.
- Existing RootRecord features remain unaffected.

Build checklist:
1) Bump version in:
   - `scripts/time_tracker/app_version.py`
   - `scripts/time_tracker/build/rootrecord.iss`
2) Build from `scripts/time_tracker`:
   - `powershell -ExecutionPolicy Bypass -File "build/build_windows.ps1"`
3) Verify artifacts:
   - `scripts/time_tracker/dist/RootRecord`
   - `scripts/time_tracker/build/output/RootRecordSetup-<version>.exe`

Expected deliverables:
- File-by-file change summary.
- Migration summary and schema notes.
- Any new dependencies and rationale.
- Test notes + known limitations.
- Final artifact filenames and paths.
