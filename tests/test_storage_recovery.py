import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from temperature_pqc.models import TemperatureReading
from temperature_pqc.observability import MetricsRegistry
from temperature_pqc.outbox import GatewayOutbox
from temperature_pqc.storage import ReadingStore


def reading():
    return TemperatureReading(
        sensor_id='sensor-01', temperature_c=21.5, measured_at='2026-10-09T10:00:00Z'
    )


def test_legacy_timestamp_schema_migrates_without_losing_data(tmp_path):
    path = tmp_path / 'legacy.db'
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('''CREATE TABLE readings (
            message_id TEXT PRIMARY KEY, sensor_id TEXT NOT NULL,
            temperature_c REAL NOT NULL, measured_at TEXT NOT NULL,
            security_mode TEXT NOT NULL, received_at TEXT NOT NULL)''')
        connection.execute('INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?)', (
            'legacy-id', 'sensor-01', 21.5, '2026-10-09 10:00:00', 'rsa',
            '2026-10-09 10:00:03',
        ))
    store = ReadingStore(str(path))
    store.initialize()
    store.initialize()  # Migration must also be safe to repeat.
    stored = store.list_recent(10)
    assert len(stored) == 1
    assert stored[0]['message_id'] == 'legacy-id'
    assert stored[0]['cloud_received_at'] == '2026-10-09 10:00:03'
    assert stored[0]['delivery_delay_seconds'] == 3.0
    assert 'received_at' not in stored[0]


def test_cloud_duplicate_preserves_original_data(tmp_path):
    store = ReadingStore(str(tmp_path / 'readings.db'))
    store.initialize()
    original = reading()
    assert store.insert(original, 'mlkem') is True
    assert store.insert(original.model_copy(update={'temperature_c': 99.0}), 'rsa') is False
    assert store.list_recent(1)[0]['temperature_c'] == 21.5
    assert store.metrics() == {
        'readings_stored_current': 1,
        'rsa_readings_stored_current': 0,
        'mlkem_readings_stored_current': 1,
    }


def test_database_readiness_reports_unusable_path(tmp_path):
    assert ReadingStore(str(tmp_path)).ready() is False
    assert GatewayOutbox(str(tmp_path), 10).ready() is False


def test_outbox_duplicate_is_accepted_even_at_capacity(tmp_path):
    outbox = GatewayOutbox(str(tmp_path / 'outbox.db'), 1)
    outbox.initialize()
    original = reading()
    assert outbox.ready() is True
    assert outbox.enqueue(original) is True
    assert outbox.enqueue(original) is False
    assert outbox.status()['pending'] == 1
    assert outbox.next_due().reading == original


def test_outbox_retry_backoff_caps_and_missing_items_are_noop(tmp_path, monkeypatch):
    monkeypatch.setattr('temperature_pqc.outbox.time.time', lambda: 1000.0)
    outbox = GatewayOutbox(str(tmp_path / 'outbox.db'), 1)
    outbox.initialize()
    original = reading()
    outbox.enqueue(original)
    for attempt in range(1, 24):
        assert outbox.schedule_retry(str(original.message_id), 'http_503', 1, 4) == (
            attempt, min(2 ** (attempt - 1), 4)
        )
    assert outbox.next_due(now=1003) is None
    assert outbox.next_due(now=1004).attempt_count == 23
    outbox.mark_delivered(str(original.message_id))
    assert outbox.schedule_retry(str(original.message_id), 'http_503', 1, 4) is None
    assert outbox.status()['total_retries'] == 23
    assert outbox.status()['pending'] == 0


def test_outbox_permission_failure_is_visible_but_does_not_discard_data(
    tmp_path, monkeypatch, caplog
):
    def deny_permission(*args):
        raise PermissionError('unsupported permission change')

    monkeypatch.setattr('temperature_pqc.outbox.os.chmod', deny_permission)
    outbox = GatewayOutbox(str(tmp_path / 'outbox.db'), 10)
    outbox.initialize()
    original = reading()
    outbox.enqueue(original)
    assert 'outbox_permission_update_failed' in caplog.text
    assert outbox.next_due().reading == original


def test_metrics_counter_is_thread_safe_and_snapshot_is_independent():
    metrics = MetricsRegistry(['requests'])
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: metrics.increment('requests'), range(1000)))
    assert metrics.value('requests') == 1000
    assert metrics.value('missing') == 0
    snapshot = metrics.snapshot()
    snapshot['requests'] = -1
    assert metrics.value('requests') == 1000
