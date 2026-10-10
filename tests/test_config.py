import pytest

from temperature_pqc.config import (
    CloudSettings,
    GatewaySettings,
    SensorSettings,
    env_bool,
    env_positive_float,
    env_positive_int,
)


@pytest.mark.parametrize('value', ['1', 'true', ' YES ', 'on'])
def test_boolean_environment_accepts_true_values(monkeypatch, value):
    monkeypatch.setenv('TEST_FLAG', value)
    assert env_bool('TEST_FLAG', False) is True


@pytest.mark.parametrize('value', ['0', 'false', ' NO ', 'off'])
def test_boolean_environment_accepts_false_values(monkeypatch, value):
    monkeypatch.setenv('TEST_FLAG', value)
    assert env_bool('TEST_FLAG', True) is False


def test_boolean_environment_rejects_ambiguous_values(monkeypatch):
    monkeypatch.setenv('TEST_FLAG', 'sometimes')
    with pytest.raises(ValueError, match='boolean'):
        env_bool('TEST_FLAG', True)
    monkeypatch.delenv('TEST_FLAG')
    assert env_bool('TEST_FLAG', False) is False


@pytest.mark.parametrize('parser', [env_positive_float, env_positive_int])
@pytest.mark.parametrize('value', ['0', '-1', 'invalid'])
def test_positive_environment_rejects_invalid_values(monkeypatch, parser, value):
    monkeypatch.setenv('TEST_NUMBER', value)
    with pytest.raises(ValueError):
        parser('TEST_NUMBER', 1)


@pytest.mark.parametrize(('overrides', 'message'), [
    ({'crypto_mode': 'auto'}, 'CRYPTO_MODE'),
    ({'crypto_mode': 'mlkem', 'expected_mlkem_key_id': 'a' * 63}, '64-character'),
    ({'outbox_max_pending': 0}, 'MAX_PENDING'),
    ({'retry_initial_seconds': 0}, 'retry intervals'),
    ({'retry_max_seconds': 0}, 'retry intervals'),
    ({'retry_initial_seconds': 10, 'retry_max_seconds': 1}, 'greater than or equal'),
    ({'outbox_poll_seconds': 0}, 'POLL_SECONDS'),
    ({'outbox_warning_threshold': 0}, 'WARNING_THRESHOLD'),
    ({'delivery_failure_warning_threshold': 0}, 'WARNING_THRESHOLD'),
])
def test_gateway_rejects_unsafe_configuration(overrides, message):
    with pytest.raises(ValueError, match=message):
        GatewaySettings(cloud_url='http://cloud.test', timeout_seconds=1, **overrides)


def test_environment_loads_explicit_migration_policy(monkeypatch, tmp_path):
    monkeypatch.setenv('CRYPTO_MODE', ' MLKEM ')
    monkeypatch.setenv('EXPECTED_MLKEM_KEY_ID', 'A' * 64)
    monkeypatch.setenv('CLOUD_URL', 'http://cloud.test/')
    monkeypatch.setenv('GATEWAY_URL', 'http://gateway.test/')
    monkeypatch.setenv('ALLOW_RSA_INGEST', 'false')
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'cloud.db'))
    monkeypatch.setenv('SENSOR_INTERVAL_SECONDS', '0.5')
    gateway = GatewaySettings.from_env()
    assert gateway.crypto_mode == 'mlkem'
    assert gateway.expected_mlkem_key_id == 'a' * 64
    assert gateway.cloud_url == 'http://cloud.test'
    assert CloudSettings.from_env().allow_rsa_ingest is False
    assert CloudSettings.from_env().database_path == str(tmp_path / 'cloud.db')
    sensor = SensorSettings.from_env()
    assert sensor.gateway_url == 'http://gateway.test'
    assert sensor.interval_seconds == 0.5
