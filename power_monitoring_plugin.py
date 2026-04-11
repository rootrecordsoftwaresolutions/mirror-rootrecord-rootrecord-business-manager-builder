"""Runtime worker for the built-in Power Monitoring plugin."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import request

from data_api import (
    aggregate_power_snapshots_to_buckets,
    get_latest_power_snapshot,
    insert_power_alert,
    insert_power_snapshot,
    plugin_setting_get,
)
from db import DbConfig


UTC = timezone.utc
PLUGIN_ID = "power_monitoring"


@dataclass
class PowerMonitoringSettings:
    enabled: bool = False
    base_url: str = "https://api.ecoflow.com"
    access_key: str = ""
    secret_key: str = ""
    device_serials: list[str] | None = None
    selected_device_serial: str = "__all__"
    poll_interval_sec: int = 5


@dataclass
class DeviceRuntimeHolder:
    serial: str
    failure_streak: int = 0
    last_status: str = ""


class PowerMonitoringPlugin:
    """Small polling runtime with resilient, non-fatal behavior."""

    def __init__(self, cfg: DbConfig, user_id: int) -> None:
        self.cfg = cfg
        self.user_id = user_id
        self._next_poll_utc: datetime | None = None
        self._status_line = "Idle (disabled)"
        self._next_cleanup_utc: datetime | None = None
        self._device_holders: dict[str, DeviceRuntimeHolder] = {}

    def load_settings(self) -> PowerMonitoringSettings:
        s = PowerMonitoringSettings()
        s.enabled = bool(plugin_setting_get(self.cfg, PLUGIN_ID, "enabled", False))
        s.base_url = "https://api.ecoflow.com"
        s.access_key = str(plugin_setting_get(self.cfg, PLUGIN_ID, "access_key", "") or "").strip()
        s.secret_key = str(plugin_setting_get(self.cfg, PLUGIN_ID, "secret_key", "") or "").strip()
        serial_raw = str(plugin_setting_get(self.cfg, PLUGIN_ID, "device_serials", "") or "")
        s.device_serials = [x.strip() for x in serial_raw.replace(",", "\n").splitlines() if x.strip()]
        s.selected_device_serial = str(
            plugin_setting_get(self.cfg, PLUGIN_ID, "selected_device_serial", "__all__") or "__all__"
        ).strip()
        if s.selected_device_serial and s.selected_device_serial != "__all__" and s.device_serials and s.selected_device_serial not in s.device_serials:
            s.selected_device_serial = "__all__"
        if not s.selected_device_serial:
            s.selected_device_serial = "__all__"
        s.poll_interval_sec = 5
        return s

    def runtime_status(self) -> str:
        return self._status_line

    def latest_snapshot(self) -> dict[str, Any] | None:
        return get_latest_power_snapshot(self.cfg, self.user_id)

    def tick(self) -> None:
        now = datetime.now(UTC)
        cfg = self.load_settings()
        if not cfg.enabled:
            self._status_line = "Idle (disabled)"
            return
        if self._next_poll_utc and now < self._next_poll_utc:
            return
        if not cfg.access_key or not cfg.secret_key or not cfg.device_serials:
            self._status_line = "Missing AccessKey/SecretKey or device serial(s)"
            self._next_poll_utc = now + timedelta(seconds=5)
            return

        self._next_poll_utc = now + timedelta(seconds=5)
        device_ids = (
            list(cfg.device_serials)
            if cfg.selected_device_serial == "__all__"
            else [cfg.selected_device_serial]
        )
        for device_id in device_ids:
            if not device_id:
                continue
            holder = self._holder_for(device_id)
            self._poll_and_store(cfg, device_id)
            self._status_line = holder.last_status or self._status_line
        if self._next_cleanup_utc is None or now >= self._next_cleanup_utc:
            aggregate_power_snapshots_to_buckets(self.cfg, self.user_id, bucket_seconds=300, keep_raw_buckets=3)
            self._next_cleanup_utc = now + timedelta(seconds=900)

    def _poll_and_store(self, cfg: PowerMonitoringSettings, device_id: str) -> None:
        holder = self._holder_for(device_id)
        payload: dict[str, Any] | None = None
        err: str = ""
        for backoff in (0, 2, 5):
            if backoff:
                self._next_poll_utc = datetime.now(UTC) + timedelta(seconds=backoff)
            try:
                payload = self._fetch_ecoflow(cfg, device_id)
                err = ""
                break
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
        if payload is None:
            holder.failure_streak += 1
            snap_id = insert_power_snapshot(
                self.cfg,
                self.user_id,
                provider="ecoflow",
                device_id=device_id,
                battery_pct=None,
                input_watts=None,
                ac_input_watts=None,
                dc_input_watts=None,
                output_watts=None,
                state_text="",
                runtime_minutes=None,
                poll_ok=False,
                error_text=err[:500],
            )
            if holder.failure_streak == 1 or holder.failure_streak % 5 == 0:
                insert_power_alert(
                    self.cfg,
                    self.user_id,
                    snapshot_id=snap_id,
                    alert_type="poll_failure",
                    severity="warn",
                    message=f"Power monitor [{device_id}] poll failed ({holder.failure_streak}): {err[:220]}",
                )
            holder.last_status = f"{device_id}: poll failed ({holder.failure_streak})"
            self._status_line = holder.last_status
            return

        holder.failure_streak = 0
        battery, ac_in_w, dc_in_w, out_w = self._extract_device_values(payload)
        in_w = float((ac_in_w or 0.0) + (dc_in_w or 0.0)) if (ac_in_w is not None or dc_in_w is not None) else None

        insert_power_snapshot(
            self.cfg,
            self.user_id,
            provider="ecoflow",
            device_id=device_id,
            battery_pct=battery,
            input_watts=in_w,
            ac_input_watts=ac_in_w,
            dc_input_watts=dc_in_w,
            output_watts=out_w,
            state_text="",
            runtime_minutes=None,
            poll_ok=True,
            error_text="",
        )

        b_txt = "?" if battery is None else f"{battery:.0f}%"
        ac_txt = "?" if ac_in_w is None else f"{ac_in_w:.0f}W"
        dc_txt = "?" if dc_in_w is None else f"{dc_in_w:.0f}W"
        o_txt = "?" if out_w is None else f"{out_w:.0f}W"
        holder.last_status = f"{device_id}: batt {b_txt} AC {ac_txt} DC {dc_txt} out {o_txt}"
        self._status_line = holder.last_status

    def _fetch_ecoflow(self, cfg: PowerMonitoringSettings, device_id: str) -> dict[str, Any]:
        url = f"{cfg.base_url.rstrip('/')}/iot-open/sign/device/quota?sn={device_id}"
        req = request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("accessKey", cfg.access_key)
        req.add_header("secretKey", cfg.secret_key)
        with request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Unexpected response payload")
        if isinstance(parsed.get("data"), dict):
            return parsed["data"]
        return parsed

    def _holder_for(self, device_id: str) -> DeviceRuntimeHolder:
        holder = self._device_holders.get(device_id)
        if holder is None:
            holder = DeviceRuntimeHolder(serial=device_id)
            self._device_holders[device_id] = holder
        return holder

    def _extract_device_values(
        self, payload: dict[str, Any]
    ) -> tuple[float | None, float | None, float | None, float | None]:
        """
        Device-specific handlers:
        - flat payload handler
        - nested metrics/quota map handler
        """
        for parser in (self._parse_flat_payload, self._parse_nested_payload):
            vals = parser(payload)
            if any(v is not None for v in vals):
                return vals
        return (None, None, None, None)

    def _parse_flat_payload(
        self, payload: dict[str, Any]
    ) -> tuple[float | None, float | None, float | None, float | None]:
        battery = _num(_pick(payload, "battery_pct", "battery", "soc", "batteryPercent"))
        ac_in_w = _num(_pick(payload, "ac_input_watts", "acInputPower", "ac_in", "line_in"))
        dc_in_w = _num(_pick(payload, "dc_input_watts", "pvPower", "solar_input_watts", "pv_power", "dc_in"))
        out_w = _num(_pick(payload, "output_watts", "outputPower", "ac_output_watts", "output"))
        return (battery, ac_in_w, dc_in_w, out_w)

    def _parse_nested_payload(
        self, payload: dict[str, Any]
    ) -> tuple[float | None, float | None, float | None, float | None]:
        nested = payload.get("params")
        if not isinstance(nested, dict):
            nested = payload.get("quota")
        if not isinstance(nested, dict):
            return (None, None, None, None)
        battery = _num(_pick(nested, "battery_pct", "soc", "batteryPercent"))
        ac_in_w = _num(_pick(nested, "ac_input_watts", "acInputPower"))
        dc_in_w = _num(_pick(nested, "dc_input_watts", "pvPower", "solar_input_watts"))
        out_w = _num(_pick(nested, "output_watts", "outputPower", "ac_output_watts"))
        return (battery, ac_in_w, dc_in_w, out_w)


def _pick(src: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in src:
            return src[k]
    return default


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
