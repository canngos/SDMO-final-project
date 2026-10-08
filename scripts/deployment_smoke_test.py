"""Verify that the Docker Compose test deployment is working end to end."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_SECONDS = float(os.getenv("DEPLOYMENT_TEST_TIMEOUT_SECONDS", "90"))
POLL_SECONDS = float(os.getenv("DEPLOYMENT_TEST_POLL_SECONDS", "2"))

CLOUD_URL = os.getenv("CLOUD_URL", "http://localhost:8000").rstrip("/")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8001").rstrip("/")
SENSOR_URL = os.getenv("SENSOR_URL", "http://localhost:8002").rstrip("/")


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_json(url: str, description: str) -> Any:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            payload = _get_json(url)
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = str(exc)
        else:
            print(f"ok: {description}")
            return payload
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"timed out waiting for {description}: {last_error}")


def _wait_for_cloud_message(message_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        readings = _get_json(f"{CLOUD_URL}/v1/readings?limit=20")
        for reading in readings:
            if reading.get("message_id") == message_id:
                return reading
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"sensor message {message_id} did not reach the cloud")


def _require_monitoring_ok(service: str, payload: dict[str, Any]) -> None:
    if payload.get("status") != "ok":
        raise RuntimeError(f"{service} monitoring reported warnings: {payload}")


def main() -> int:
    try:
        _wait_for_json(f"{CLOUD_URL}/health", "cloud health endpoint")
        gateway_health = _wait_for_json(
            f"{GATEWAY_URL}/health",
            "gateway health endpoint",
        )
        _wait_for_json(f"{SENSOR_URL}/health", "sensor health endpoint")
        _wait_for_json(f"{CLOUD_URL}/ready", "cloud readiness endpoint")
        _wait_for_json(f"{GATEWAY_URL}/ready", "gateway readiness endpoint")
        _wait_for_json(f"{SENSOR_URL}/ready", "sensor readiness endpoint")

        latest = _wait_for_json(
            f"{SENSOR_URL}/v1/readings/latest",
            "latest sensor reading endpoint",
        )
        message_id = latest["message_id"]
        print(f"checking delivery for message_id={message_id}")

        delivered = _wait_for_cloud_message(message_id)
        outbox = _wait_for_json(
            f"{GATEWAY_URL}/v1/outbox/status",
            "gateway outbox status endpoint",
        )
        cloud_metrics = _wait_for_json(f"{CLOUD_URL}/metrics", "cloud metrics endpoint")
        gateway_metrics = _wait_for_json(
            f"{GATEWAY_URL}/metrics",
            "gateway metrics endpoint",
        )
        sensor_metrics = _wait_for_json(
            f"{SENSOR_URL}/metrics",
            "sensor metrics endpoint",
        )
        _require_monitoring_ok(
            "cloud",
            _wait_for_json(f"{CLOUD_URL}/monitoring", "cloud monitoring endpoint"),
        )
        _require_monitoring_ok(
            "gateway",
            _wait_for_json(f"{GATEWAY_URL}/monitoring", "gateway monitoring endpoint"),
        )
        _require_monitoring_ok(
            "sensor",
            _wait_for_json(f"{SENSOR_URL}/monitoring", "sensor monitoring endpoint"),
        )

        print(
            "ok: reading delivered "
            f"message_id={delivered['message_id']} "
            f"sensor_id={delivered['sensor_id']} "
            f"security_mode={delivered['security_mode']}"
        )
        print(
            "ok: gateway reports "
            f"crypto_mode={gateway_health.get('crypto_mode')} "
            f"outbox_pending={outbox.get('pending')}"
        )
        print(
            "ok: metrics visible "
            f"cloud_readings={cloud_metrics.get('readings_stored_current')} "
            f"gateway_successes={gateway_metrics.get('cloud_delivery_successes_total')} "
            f"sensor_successes={sensor_metrics.get('gateway_send_successes_total')}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"deployment smoke test failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
