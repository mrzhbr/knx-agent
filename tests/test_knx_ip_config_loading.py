"""Loading rules for KNX/IP runtime-only credentials."""

from __future__ import annotations

from pathlib import Path

import pytest  # type: ignore[import-not-found]
import yaml

from src.config import load_config


_MODELS = {
    "router": {"base_url": "https://example.test", "api_key_env": "A", "model_id": "a"},
    "specialists": {
        "base_url": "https://example.test",
        "api_key_env": "B",
        "model_id": "b",
    },
    "reflection": {"base_url": "https://example.test", "api_key_env": "C", "model_id": "c"},
}


def _write_config(path: Path, knx_ip: dict[str, object]) -> None:
    path.write_text(
        yaml.safe_dump({"models": _MODELS, "platforms": ["knx_ip"], "knx_ip": knx_ip}),
        encoding="utf-8",
    )


def test_load_config_rejects_inline_knx_password_fields(tmp_path: Path):
    path = tmp_path / "config.yaml"
    _write_config(path, {"keyfile_password": ""})

    with pytest.raises(RuntimeError, match="environment variables"):
        load_config(path)


def test_knx_file_paths_are_resolved_relative_to_config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    _write_config(
        path,
        {
            "device_map": "devices.yaml",
            "project_file": "project.knxproj",
        },
    )

    config = load_config(path)

    assert config.knx_ip.device_map == str(tmp_path / "devices.yaml")
    assert config.knx_ip.project_file == str(tmp_path / "project.knxproj")


def test_knx_keyfile_requires_runtime_password_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("KNX_KEYFILE_PASSWORD", raising=False)
    path = tmp_path / "config.yaml"
    _write_config(path, {"secure_keyfile": "secrets/data-secure.knxkeys"})

    with pytest.raises(RuntimeError, match="KNX_KEYFILE_PASSWORD"):
        load_config(path)


def test_secure_knx_config_requires_runtime_password_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("KNX_KEYFILE_PASSWORD", raising=False)
    path = tmp_path / "config.yaml"
    _write_config(
        path,
        {
            "connection_type": "tunneling",
            "gateway_ip": "192.0.2.10",
            "secure": True,
            "secure_keyfile": "secrets/installation.knxkeys",
        },
    )

    with pytest.raises(RuntimeError, match="KNX_KEYFILE_PASSWORD"):
        load_config(path)
