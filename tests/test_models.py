import pytest
from pydantic import ValidationError

from temperature_pqc.models import TemperatureReading


def test_temperature_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        TemperatureReading(
            sensor_id="sensor-01",
            temperature_c=151,
            measured_at="2026-09-22T10:00:00Z",
        )
def test_temperature_lower_bound_is_enforced() -> None:
    with pytest.raises(ValidationError):
        TemperatureReading(
            sensor_id="sensor-01",
            temperature_c=-81,
            measured_at="2026-09-22T10:00:00Z",
        )
def test_temperature_reading_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        TemperatureReading(
            sensor_id="sensor-01",
            temperature_c=21.5,
            measured_at="2026-09-22T10:00:00",
        )
def test_valid_temperature_reading_is_accepted() -> None:
    reading = TemperatureReading(
        sensor_id="sensor-01",
        temperature_c=21.5,
        measured_at="2026-09-22T10:00:00Z",
    )

    assert reading.temperature_c == 21.5
    assert reading.sensor_id == "sensor-01"
        