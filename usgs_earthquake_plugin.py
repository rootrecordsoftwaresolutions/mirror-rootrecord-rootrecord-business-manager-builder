"""USGS Earthquake Alerts plugin runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Any
from urllib import parse, request

from data_api import (
    has_usgs_quake_alert,
    insert_usgs_quake_alert,
    insert_usgs_quake_event,
    list_recent_usgs_quake_matches,
    plugin_setting_get,
)
from db import DbConfig, now_utc_iso_text

UTC = timezone.utc
PLUGIN_ID = "usgs_earthquake_alerts"


@dataclass
class UsgsSettings:
    enabled: bool = False
    poll_interval_sec: int = 300
    lat: float = 0.0
    lon: float = 0.0
    radius_miles: float = 100.0
    min_magnitude: float = 2.5
    quiet_start: str = ""
    quiet_end: str = ""


class UsgsEarthquakePlugin:
    def __init__(self, cfg: DbConfig) -> None:
        self.cfg = cfg
        self._next_poll_utc: datetime | None = None
        self._status_line = "Idle (disabled)"
        self._last_poll_utc = ""
        self._last_success_utc = ""
        self._last_error = ""
        self._last_matches = 0
        self._last_popup_utc: datetime | None = None

    def load_settings(self) -> UsgsSettings:
        s = UsgsSettings()
        s.enabled = bool(plugin_setting_get(self.cfg, PLUGIN_ID, "enabled", False))
        s.poll_interval_sec = _int_setting(self.cfg, "poll_interval_sec", 300, min_value=60)
        s.lat = _float_setting(self.cfg, "lat", 0.0)
        s.lon = _float_setting(self.cfg, "lon", 0.0)
        s.radius_miles = _float_setting(self.cfg, "radius_miles", 100.0)
        s.min_magnitude = _float_setting(self.cfg, "min_magnitude", 2.5)
        s.quiet_start = str(plugin_setting_get(self.cfg, PLUGIN_ID, "quiet_start", "") or "").strip()
        s.quiet_end = str(plugin_setting_get(self.cfg, PLUGIN_ID, "quiet_end", "") or "").strip()
        return s

    def tick(self) -> list[str]:
        now = datetime.now(UTC)
        cfg = self.load_settings()
        if not cfg.enabled:
            self._status_line = "Idle (disabled)"
            return []
        if self._next_poll_utc and now < self._next_poll_utc:
            return []
        self._next_poll_utc = now + timedelta(seconds=max(60, cfg.poll_interval_sec))
        self._last_poll_utc = now_utc_iso_text()
        return self._poll(cfg)

    def status_lines(self) -> list[str]:
        return [
            f"USGS runtime: {self._status_line}",
            f"Last poll: {self._last_poll_utc or '-'}",
            f"Last success: {self._last_success_utc or '-'}",
            f"Recent matches: {self._last_matches}",
            f"Latest error: {self._last_error or '-'}",
        ]

    def recent_matches(self, limit: int = 8) -> list[dict[str, Any]]:
        return list_recent_usgs_quake_matches(self.cfg, limit=limit)

    def _poll(self, cfg: UsgsSettings) -> list[str]:
        if abs(cfg.lat) < 0.001 and abs(cfg.lon) < 0.001:
            self._status_line = "Set latitude/longitude first"
            return []
        err = ""
        events: list[dict[str, Any]] = []
        for _delay in (0, 2, 5):
            try:
                events = self._fetch_usgs_events(cfg)
                err = ""
                break
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
        if err:
            self._last_error = err[:220]
            self._status_line = "Poll failed"
            return []

        self._last_success_utc = now_utc_iso_text()
        self._last_error = ""
        alerts: list[str] = []
        match_count = 0
        for e in events:
            event_id = str(e.get("id") or "").strip()
            if not event_id:
                continue
            props = e.get("properties") or {}
            geom = e.get("geometry") or {}
            coords = list(geom.get("coordinates") or [None, None, None])
            lon = _num(coords[0] if len(coords) > 0 else None)
            lat = _num(coords[1] if len(coords) > 1 else None)
            depth = _num(coords[2] if len(coords) > 2 else None)
            mag = _num(props.get("mag"))
            if mag is None or mag < cfg.min_magnitude:
                continue
            if lat is None or lon is None:
                continue
            distance = _haversine_miles(cfg.lat, cfg.lon, lat, lon)
            if distance > max(1.0, cfg.radius_miles):
                continue

            event_time_utc = _usgs_ms_to_iso(props.get("time"))
            place = str(props.get("place") or "Unknown location")
            detail_url = str(props.get("url") or "")
            inserted = insert_usgs_quake_event(
                self.cfg,
                event_id=event_id,
                event_time_utc=event_time_utc,
                magnitude=mag,
                place=place,
                latitude=lat,
                longitude=lon,
                depth_km=depth,
                detail_url=detail_url,
                raw_json=json.dumps(e),
            )
            if inserted or not has_usgs_quake_alert(self.cfg, event_id):
                snap = {
                    "radius_miles": cfg.radius_miles,
                    "min_magnitude": cfg.min_magnitude,
                    "lat": cfg.lat,
                    "lon": cfg.lon,
                }
                created = insert_usgs_quake_alert(
                    self.cfg,
                    event_id=event_id,
                    distance_miles=distance,
                    rule_snapshot_json=json.dumps(snap),
                    acknowledged=False,
                )
                if created:
                    match_count += 1
                    alerts.append(f"M{mag:.1f} quake {distance:.1f}mi away: {place}")
        self._last_matches = match_count
        self._status_line = "OK" if not alerts else f"OK ({len(alerts)} new)"
        return alerts

    def should_suppress_popup(self) -> bool:
        cfg = self.load_settings()
        if _in_quiet_hours(cfg.quiet_start, cfg.quiet_end):
            return True
        now = datetime.now(UTC)
        if self._last_popup_utc and (now - self._last_popup_utc).total_seconds() < 45:
            return True
        self._last_popup_utc = now
        return False

    def test_alert_text(self) -> str:
        return "USGS test alert: simulated nearby quake event."

    def _fetch_usgs_events(self, cfg: UsgsSettings) -> list[dict[str, Any]]:
        start_time = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")
        params = {
            "format": "geojson",
            "starttime": start_time,
            "latitude": str(cfg.lat),
            "longitude": str(cfg.lon),
            "maxradiuskm": str(max(1.0, cfg.radius_miles) * 1.60934),
            "minmagnitude": str(max(0.0, cfg.min_magnitude)),
            "orderby": "time",
            "limit": "200",
        }
        url = "https://earthquake.usgs.gov/fdsnws/event/1/query?" + parse.urlencode(params)
        req = request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return []
        features = parsed.get("features")
        if not isinstance(features, list):
            return []
        return [f for f in features if isinstance(f, dict)]


def _usgs_ms_to_iso(v: Any) -> str:
    ms = _num(v)
    if ms is None:
        return now_utc_iso_text()
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=UTC).replace(tzinfo=None)
        return dt.isoformat()
    except Exception:
        return now_utc_iso_text()


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _float_setting(cfg: DbConfig, key: str, default: float) -> float:
    try:
        return float(plugin_setting_get(cfg, PLUGIN_ID, key, default))
    except (TypeError, ValueError):
        return default


def _int_setting(cfg: DbConfig, key: str, default: int, *, min_value: int = 0) -> int:
    try:
        return max(min_value, int(plugin_setting_get(cfg, PLUGIN_ID, key, default)))
    except (TypeError, ValueError):
        return default


def _in_quiet_hours(start_hhmm: str, end_hhmm: str) -> bool:
    def parse_hhmm(v: str) -> tuple[int, int] | None:
        parts = v.split(":")
        if len(parts) != 2:
            return None
        try:
            h = int(parts[0])
            m = int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return None
            return h, m
        except (TypeError, ValueError):
            return None

    s = parse_hhmm(start_hhmm)
    e = parse_hhmm(end_hhmm)
    if s is None or e is None:
        return False
    now = datetime.now().time()
    s_t = now.replace(hour=s[0], minute=s[1], second=0, microsecond=0)
    e_t = now.replace(hour=e[0], minute=e[1], second=0, microsecond=0)
    if s_t <= e_t:
        return s_t <= now <= e_t
    return now >= s_t or now <= e_t
