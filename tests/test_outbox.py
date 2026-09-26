from temperature_pqc.models import TemperatureReading
from temperature_pqc.outbox import GatewayOutbox, OutboxFullError


def _reading(sensor_id: str = "sensor-01") -> TemperatureReading:
    return TemperatureReading(
        sensor_id=sensor_id,
        temperature_c=20.5,
        measured_at="2026-09-26T08:00:00Z",
    )


def test_pending_reading_survives_outbox_reinitialization(tmp_path) -> None:
    path = str(tmp_path / "gateway-outbox.db")
    first = GatewayOutbox(path, max_pending=10)
    first.initialize()
    reading = _reading()
    assert first.enqueue(reading) is True

    reopened = GatewayOutbox(path, max_pending=10)
    reopened.initialize()
    pending = reopened.next_due()

    assert pending is not None
    assert pending.reading.message_id == reading.message_id
    assert reopened.status()["pending"] == 1


def test_retry_is_counted_and_reading_remains_pending(tmp_path) -> None:
    outbox = GatewayOutbox(str(tmp_path / "gateway-outbox.db"), max_pending=10)
    outbox.initialize()
    reading = _reading()
    outbox.enqueue(reading)

    retry = outbox.schedule_retry(
        str(reading.message_id),
        "http_403",
        initial_seconds=1.0,
        maximum_seconds=30.0,
    )

    assert retry == (1, 1.0)
    assert outbox.next_due() is None
    assert outbox.status()["pending"] == 1
    assert outbox.status()["total_retries"] == 1


def test_capacity_limit_rejects_new_data_without_deleting_pending_data(tmp_path) -> None:
    outbox = GatewayOutbox(str(tmp_path / "gateway-outbox.db"), max_pending=1)
    outbox.initialize()
    first = _reading("sensor-01")
    second = _reading("sensor-02")
    outbox.enqueue(first)

    try:
        outbox.enqueue(second)
    except OutboxFullError:
        pass
    else:
        raise AssertionError("outbox accepted more than its configured capacity")

    pending = outbox.next_due()
    assert pending is not None
    assert pending.reading.message_id == first.message_id
