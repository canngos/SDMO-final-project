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
