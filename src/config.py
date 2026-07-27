from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field  # type: ignore[import-not-found]
from pydantic_settings import (  # type: ignore[import-not-found]
    BaseSettings,
    SettingsConfigDict,
)

logger = logging.getLogger(__name__)


class ModelSlot(str, Enum):
    ROUTER_TIER = "router_tier"
    SPECIALISTS_TIER = "specialists_tier"
    REFLECTION_TIER = "reflection_tier"


class ModelSlotConfig(BaseModel):
    base_url: str
    api_key_env: str
    model_id: str


class ModelsConfig(BaseModel):
    """Per-slot model configuration. All three slots are required.

    Defined in config.yaml under the ``models:`` key — there are no hardcoded
    defaults. See config.yaml.example for a working reference.
    """

    router: ModelSlotConfig
    specialists: ModelSlotConfig
    reflection: ModelSlotConfig


class MatrixConfig(BaseModel):
    homeserver: str = "http://localhost:8008"
    user_id: str = "@agent:example.test"
    password: str = ""
    room_id: str = ""


class MQTTConfig(BaseModel):
    host: str = "localhost"
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False


class FrigateConfig(BaseModel):
    host: str = "localhost"
    port: int = 5000
    camera_rooms: dict[str, str] = Field(default_factory=dict)
    entrance_cameras: list[str] = Field(default_factory=list)


class GiraX1Config(BaseModel):
    host: str = ""
    token: str = ""
    local_ip: str = ""
    callback_port: int = 8102
    callback_host: str = "127.0.0.1"
    callback_token: str = ""
    verify_tls: bool = True
    ca_bundle: str = ""


class TrackedDevice(BaseModel):
    ip: str
    name: str
    mac: str | None = None


class PresenceConfig(BaseModel):
    poll_interval_seconds: int = 30
    away_threshold: int = 3
    tracked_devices: dict[str, TrackedDevice] = Field(default_factory=dict)
    backend: Literal["owntracks", "arp"] = "owntracks"
    # OwnTracks settings
    owntracks_topic_prefix: str = "owntracks"
    owntracks_home_region: str = "Home"
    owntracks_geofence_radius_m: int = 150
    owntracks_stale_timeout_minutes: int = 30
    owntracks_tracked_users: dict[str, str] = Field(default_factory=dict)


class WhisperConfig(BaseModel):
    model: str = "small"
    language: str = "de"


class HomeConfig(BaseModel):
    family_name: str = "Family"
    city: str = ""
    country: str = "Germany"
    timezone: str = "Europe/Berlin"
    latitude: float = 0.0
    longitude: float = 0.0
    mobotix_password: str = ""
    door_station_host: str = ""
    door_station_webhook_host: str = "127.0.0.1"
    door_station_webhook_token: str = ""
    door_station_camera: str = ""


class ACUnit(BaseModel):
    name: str
    ip: str
    room: str


class MitsubishiACConfig(BaseModel):
    units: list[ACUnit] = Field(default_factory=list)
    poll_interval: int = 30


class CalDAVConfig(BaseModel):
    url: str = ""
    username: str = ""
    password: str = ""
    calendar_name: str = ""


class ProfileConfig(BaseModel):
    key: str = ""
    name: str = ""
    trust_level: str = "admin"
    language: str = "casual German"
    response_style: str = "friendly, concise"
    allowed_tools: list[str] = Field(default_factory=list)
    matrix_ids: list[str] = Field(default_factory=list)


class TrainingDataConfig(BaseModel):
    enabled: bool = False
    output_dir: str = "data/training"


class BrainConfig(BaseModel):
    home: HomeConfig = Field(default_factory=HomeConfig)
    models: ModelsConfig
    matrix: MatrixConfig = Field(default_factory=MatrixConfig)
    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    frigate: FrigateConfig = Field(default_factory=FrigateConfig)
    gira_x1: GiraX1Config = Field(default_factory=GiraX1Config)
    presence: PresenceConfig = Field(default_factory=PresenceConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    ollama_host: str = "http://localhost:11434"
    mitsubishi_ac: MitsubishiACConfig = Field(default_factory=MitsubishiACConfig)
    caldav: CalDAVConfig = Field(default_factory=CalDAVConfig)
    training_data: TrainingDataConfig = Field(default_factory=TrainingDataConfig)
    profiles: list[ProfileConfig] = Field(default_factory=list)
    platforms: list[str] = Field(
        default_factory=lambda: [
            "gira_x1",
            "frigate",
            "presence",
            "mitsubishi_ac",
            "weather",
            "door_station",
            "calendar",
        ]
    )

    def resolve_model(self, slot: "ModelSlot") -> "ModelSlotConfig":
        """Return the ModelSlotConfig for the given slot."""
        if slot is ModelSlot.ROUTER_TIER:
            return self.models.router
        if slot is ModelSlot.SPECIALISTS_TIER:
            return self.models.specialists
        if slot is ModelSlot.REFLECTION_TIER:
            return self.models.reflection
        raise ValueError(f"Unknown slot: {slot}")


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gira_x1_token: str = ""
    matrix_password: str = ""
    mobotix_password: str = ""
    caldav_password: str = ""
    mqtt_username: str = ""
    mqtt_password: str = ""
    door_station_webhook_token: str = ""
    gira_x1_callback_token: str = ""


def load_config(config_path: str | Path | None = None) -> BrainConfig:
    """Load config from config.yaml, then overlay environment variables from .env."""
    from dotenv import load_dotenv

    load_dotenv()

    if config_path is None:
        config_path = Path(__file__).parent.parent / "config.yaml"

    yaml_data: dict[str, Any] = {}
    config_path = Path(config_path)
    if config_path.exists():
        try:
            with config_path.open(encoding="utf-8") as config_file:
                yaml_data = yaml.safe_load(config_file) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"Could not load configuration from {config_path}") from exc
        logger.debug("Loaded config from %s", config_path)
    else:
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. See config.yaml.example for a template."
        )

    if "models" not in yaml_data:
        raise RuntimeError(
            "config.yaml is missing the required 'models:' block. "
            "See config.yaml.example for a template."
        )

    config = BrainConfig.model_validate(yaml_data)

    # Overlay env vars (secrets never go in config.yaml)
    env = EnvSettings()
    if env.gira_x1_token:
        config.gira_x1.token = env.gira_x1_token
    if env.matrix_password:
        config.matrix.password = env.matrix_password
    if env.mobotix_password:
        config.home.mobotix_password = env.mobotix_password
    if env.caldav_password:
        config.caldav.password = env.caldav_password
    if env.mqtt_username:
        config.mqtt.username = env.mqtt_username
    if env.mqtt_password:
        config.mqtt.password = env.mqtt_password
    if env.door_station_webhook_token:
        config.home.door_station_webhook_token = env.door_station_webhook_token
    if env.gira_x1_callback_token:
        config.gira_x1.callback_token = env.gira_x1_callback_token

    return config
