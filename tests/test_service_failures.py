import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from temperature_pqc.config import CloudSettings, GatewaySettings, SensorSettings
from temperature_pqc.crypto import (
    LegacyCloudKeyPair,
    MlKemCloudKeyPair,
    MlKemPublicKeyError,
    encrypt_for_cloud,
)
from temperature_pqc.models import (
    PublicKeyDocument,
    RsaEncryptedEnvelope,
    TemperatureReading,
)
from temperature_pqc.outbox import PendingReading
from temperature_pqc.services import gateway as gateway_service
from temperature_pqc.services.cloud import create_app as cloud_app
from temperature_pqc.services.sensor import create_app as sensor_app


def reading():
    return TemperatureReading(
        sensor_id='sensor-01', temperature_c=21.5, measured_at='2026-10-09T10:00:00Z'
    )


def database_unavailable(*args, **kwargs):
    raise sqlite3.OperationalError('simulated disk failure')


def gateway_settings(tmp_path, **overrides):
    values = {
        'cloud_url': 'http://cloud.test', 'timeout_seconds': 1,
        'outbox_database_path': str(tmp_path / 'outbox.db'),
        'outbox_poll_seconds': 0.001, 'retry_initial_seconds': 0.001,
        'retry_max_seconds': 0.002,
    }
    values.update(overrides)
    return GatewaySettings(**values)


def test_cloud_database_failure_is_reported_by_metrics_and_monitoring(tmp_path, monkeypatch):
    app = cloud_app(CloudSettings(
        database_path=str(tmp_path / 'cloud.db'), mlkem_seed_path=str(tmp_path / 'key.seed'),
    ))
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.store, '_connect', database_unavailable)
        assert client.get('/ready').status_code == 503
        metrics = client.get('/metrics').json()
        assert metrics['database_metrics_available'] is False
        assert 'readings_stored_current' not in metrics
        assert client.get('/monitoring').json()['warnings'][0]['code'] == 'service_not_ready'
        assert client.get('/health').status_code == 200


def test_cloud_rejects_inner_outer_id_mismatch_and_deduplicates_valid_delivery(tmp_path):
    app = cloud_app(CloudSettings(
        database_path=str(tmp_path / 'cloud.db'), mlkem_seed_path=str(tmp_path / 'key.seed'),
    ))
    with TestClient(app) as client:
        document = PublicKeyDocument.model_validate(client.get('/v1/crypto/public-key').json())
        original = reading()
        payload = original.model_dump_json().encode()
        mismatched = encrypt_for_cloud(document, uuid4(), payload)
        assert client.post('/v1/readings', json=mismatched.model_dump(mode='json')).status_code == 400
        assert client.get('/v1/readings').json() == []
        envelope = encrypt_for_cloud(document, original.message_id, payload)
        assert client.post('/v1/readings', json=envelope.model_dump(mode='json')).status_code == 200
        assert client.post('/v1/readings', json=envelope.model_dump(mode='json')).status_code == 409
        assert len(client.get('/v1/readings').json()) == 1
        metrics = client.get('/metrics').json()
        assert metrics['validation_failures_total'] == 1
        assert metrics['duplicate_deliveries_total'] == 1
        assert metrics['readings_persisted_total'] == 1


def test_gateway_rejects_full_queue_without_losing_first_reading(tmp_path):
    app = gateway_service.create_app(gateway_settings(tmp_path, outbox_max_pending=1))
    app.state.outbox.initialize()
    app.state.outbox_wakeup = asyncio.Event()
    # No lifespan worker: keep the queued item stationary while testing admission.
    client = TestClient(app)
    original = reading()
    assert client.post('/v1/readings', json=original.model_dump(mode='json')).status_code == 202
    duplicate = client.post('/v1/readings', json=original.model_dump(mode='json'))
    assert duplicate.json()['already_queued'] is True
    rejected = client.post('/v1/readings', json=reading().model_dump(mode='json'))
    assert rejected.status_code == 503
    assert rejected.json()['detail'] == 'gateway outbox is full'
    assert app.state.outbox.next_due().reading == original
    assert app.state.metrics.value('readings_rejected_total') == 1


def test_gateway_disk_failure_rejects_admission_and_reports_unready(tmp_path, monkeypatch):
    app = gateway_service.create_app(gateway_settings(tmp_path))
    app.state.outbox.initialize()
    monkeypatch.setattr(app.state.outbox, '_connect', database_unavailable)
    client = TestClient(app)
    response = client.post('/v1/readings', json=reading().model_dump(mode='json'))
    assert response.status_code == 503
    assert response.json()['detail'] == 'gateway outbox is unavailable'
    assert app.state.metrics.value('readings_accepted_total') == 0
    assert app.state.metrics.value('readings_rejected_total') == 1
    ready = client.get('/ready')
    assert ready.status_code == 503
    assert ready.json()['checks']['outbox_accessible'] is False
    assert 'outbox' not in ready.json()
    metrics = client.get('/metrics').json()
    assert metrics['outbox_metrics_available'] is False
    assert 'outbox_pending' not in metrics
    assert client.get('/monitoring').json()['status'] == 'warning'


@pytest.mark.parametrize('body', [b'not-json', b'{}', b'{"version": 1}'])
def test_gateway_rejects_malformed_key_documents_without_posting_or_downgrading(tmp_path, body):
    paths = []

    def cloud(request):
        paths.append(request.url.path)
        return httpx.Response(200, content=body)

    app = gateway_service.create_app(gateway_settings(
        tmp_path, crypto_mode='mlkem', expected_mlkem_key_id='0' * 64,
    ))

    async def deliver():
        async with httpx.AsyncClient(transport=httpx.MockTransport(cloud)) as client:
            with pytest.raises(MlKemPublicKeyError, match='invalid ML-KEM public-key response'):
                await gateway_service._deliver_reading(app, client, reading())

    asyncio.run(deliver())
    assert paths == ['/v2/crypto/public-key']
    assert app.state.settings.crypto_mode == 'mlkem'


def test_gateway_defensive_pin_check_stops_delivery_if_runtime_pin_is_missing(tmp_path):
    pair = MlKemCloudKeyPair.load_or_create(str(tmp_path / 'key.seed'))
    app = gateway_service.create_app(gateway_settings(tmp_path))
    # Emulate corrupted in-memory state bypassing the normal startup validator.
    app.state.settings = SimpleNamespace(
        crypto_mode='mlkem', expected_mlkem_key_id=None, cloud_url='http://cloud.test',
    )
    paths = []

    def cloud(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=pair.public_document().model_dump())

    async def deliver():
        async with httpx.AsyncClient(transport=httpx.MockTransport(cloud)) as client:
            with pytest.raises(MlKemPublicKeyError, match='pin is not configured'):
                await gateway_service._deliver_reading(app, client, reading())

    asyncio.run(deliver())
    assert paths == ['/v2/crypto/public-key']


def test_retry_for_already_removed_item_is_harmless(tmp_path):
    app = gateway_service.create_app(gateway_settings(tmp_path))
    app.state.outbox.initialize()
    pending = PendingReading(reading=reading(), attempt_count=0, created_at=time.time())
    asyncio.run(gateway_service._schedule_retry(app, pending, ValueError('invalid key')))
    assert app.state.outbox.status()['total_retries'] == 0
    assert app.state.outbox.status()['pending'] == 0


@pytest.mark.parametrize('failed_method', ['next_due', 'schedule_retry', 'mark_delivered'])
def test_worker_recovers_from_transient_database_failure_without_losing_reading(
    tmp_path, monkeypatch, failed_method, caplog
):
    pair = LegacyCloudKeyPair()
    received_ids = []
    stored_ids = set()

    def cloud(request):
        if request.method == 'GET':
            return httpx.Response(200, json=pair.public_document().model_dump())
        envelope = RsaEncryptedEnvelope.model_validate_json(request.content)
        decoded = TemperatureReading.model_validate_json(pair.decrypt(envelope))
        received_ids.append(decoded.message_id)
        if failed_method == 'schedule_retry' and len(received_ids) <= 2:
            return httpx.Response(503)
        duplicate = decoded.message_id in stored_ids
        stored_ids.add(decoded.message_id)
        return httpx.Response(409 if duplicate else 200)

    app = gateway_service.create_app(
        gateway_settings(tmp_path), transport=httpx.MockTransport(cloud),
    )
    outbox = app.state.outbox
    outbox.initialize()
    original = reading()
    outbox.enqueue(original)
    real_method = getattr(outbox, failed_method)
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError('transient disk failure')
        return real_method(*args, **kwargs)

    monkeypatch.setattr(outbox, failed_method, fail_once)

    async def run_worker():
        app.state.outbox_wakeup = asyncio.Event()
        task = asyncio.create_task(gateway_service._outbox_worker(app))

        async def wait_for_delivery():
            while app.state.metrics.value('cloud_delivery_successes_total') == 0:
                if task.done():
                    task.result()
                    raise AssertionError('worker exited before delivering the reading')
                await asyncio.sleep(0.001)

        try:
            await asyncio.wait_for(wait_for_delivery(), timeout=3)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run_worker())
    assert stored_ids == {original.message_id}
    assert set(received_ids) == {original.message_id}
    assert outbox.status()['pending'] == 0
    assert calls >= 2
    expected_event = {
        'next_due': 'outbox_read_failed',
        'schedule_retry': 'retry_schedule_failed',
        'mark_delivered': 'outbox_delivery_commit_failed',
    }[failed_method]
    assert expected_event in caplog.text
    if failed_method == 'schedule_retry':
        assert outbox.status()['total_retries'] == 1
    if failed_method == 'mark_delivered':
        assert app.state.metrics.value('duplicate_deliveries_total') == 1


def test_sensor_before_startup_reports_unready():
    app = sensor_app(SensorSettings('http://gateway.test', 'sensor-01', 1, 1))
    client = TestClient(app)
    assert client.get('/health').json() == {'status': 'ok'}
    assert client.get('/ready').status_code == 503
    assert client.get('/monitoring').json()['warnings'][0]['code'] == 'service_not_ready'


def test_sensor_generates_next_reading_after_success_and_exposes_latest():
    ids = []

    def gateway(request):
        ids.append(json.loads(request.content)['message_id'])
        return httpx.Response(202)

    app = sensor_app(
        SensorSettings('http://gateway.test', 'sensor-01', 0.005, 1),
        transport=httpx.MockTransport(gateway),
    )
    with TestClient(app) as client:
        deadline = time.monotonic() + 3
        while len(ids) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(ids) >= 2
        assert ids[0] != ids[1]
        latest = client.get('/v1/readings/latest')
        assert latest.status_code == 200
        value = TemperatureReading.model_validate(latest.json())
        assert value.sensor_id == 'sensor-01'
        assert 10 <= value.temperature_c <= 35
        assert value.message_id != ids[0]
        assert client.get('/metrics').json()['readings_generated_total'] >= 2
