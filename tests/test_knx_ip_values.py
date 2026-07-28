"""Value conversion tests for native KNX commands and state."""

from __future__ import annotations

import math

import pytest  # type: ignore[import-not-found]

from src.hal.platforms.knx_ip_values import (  # type: ignore[import-not-found]
    binary_value,
    temperature,
)


@pytest.mark.parametrize("raw", [False, 0, "0", "false", "off"])
def test_binary_value_parses_explicit_false_values(raw: object):
    assert not binary_value(raw)


@pytest.mark.parametrize("raw", [True, 1, "1", "true", "on"])
def test_binary_value_parses_explicit_true_values(raw: object):
    assert binary_value(raw)


@pytest.mark.parametrize("raw", [2, "yes", "", None])
def test_binary_value_rejects_ambiguous_values(raw: object):
    with pytest.raises(ValueError, match="binary"):
        binary_value(raw)


@pytest.mark.parametrize("raw", [math.nan, math.inf, -math.inf])
def test_temperature_rejects_non_finite_values(raw: float):
    with pytest.raises(ValueError, match="finite"):
        temperature(raw)
