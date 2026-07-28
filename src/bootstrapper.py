"""Bootstrapper — lifecycle orchestrator for KNX Agent.

Extracts the 23-step wiring sequence from main.py into named lifecycle
phases. Each phase has clear error handling and can be tested independently.

Phases:
  configure()    — Load config, load profiles, open MemoryLayer
  wire_hal()     — Create StateStore, HAL, add all 7 platforms, topology, rules
  wire_tools()   — Create PresenceManager, EnergyAwareness, ToolContext,
                   call set_context() on all 9 tool modules
  wire_agents()  — Create Strands agents, supervisor, router client,
                   ScheduleEngine, set supervisor_fn
  start()        — bus.start_all(), Gira start_runtime, populate_initial_state,
                   connect PresenceManager, register StateStore listeners,
                   start background tasks, start Matrix
  stop()         — Cancel watcher, stop background tasks with timeout,
                   send shutdown Matrix message, stop HAL, stop Matrix,
                   stop bus, close memory
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from strands.types.exceptions import (  # type: ignore[import-not-found]
    ContextWindowOverflowException,
)

from src.core.platform_health import PlatformHealthMonitor

logger = logging.getLogger(__name__)


class Bootstrapper:
    """Lifecycle orchestrator for KNX Agent.

    Usage::

        bootstrapper = Bootstrapper(Path("config.yaml"))
        await bootstrapper.configure()
        await bootstrapper.wire_hal()
        await bootstrapper.wire_tools()
        await bootstrapper.wire_agents()
        await bootstrapper.start()
        # ... await shutdown_event ...
        await bootstrapper.stop()
    """

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

        # Phase 1 outputs
        self.config: Any = None
        self.memory_layer: Any = None

        # Phase 2 outputs
        self.bus: Any = None
        self.hal: Any = None
        self._hal_state_store: Any = None
        self.topology: Any = None
        self.energy_awareness: Any = None
        self.awareness: Any = None

        # Phase 3 outputs
        self.presence_manager: Any = None
        self.tool_context: Any = None

        # Phase 4 outputs
        self.agents_dict: dict[str, Any] = {}
        self.supervisor: Any = None
        self.schedule_engine: Any = None
        self.offline_adapters: dict[str, Any] = {}
        self.router_agent: Any = None
        self.agent_dispatch: dict[str, Any] = {}
        self.conversation: Any = None
        self.guardrail_provider: Any = None
        self.training_hook_provider: Any = None
        self._training_writer: Any = None

        # Phase 5 outputs
        self.matrix: Any = None

        # Background task objects (hold .start()/.stop() API and ._task handle)
        self._monitor: Any = None
        self._pattern_detector: Any = None
        self._reflection_agent: Any = None
        self._schedule_engine_obj: Any = None  # alias: same as self.schedule_engine

        # Background task handles collected for the watcher
        self._background_tasks: list[tuple[str, asyncio.Task]] = []
        self._watcher_task: asyncio.Task | None = None

        # Short-lived, single-use confirmations keyed by (room, authenticated sender).
        self._pending_security_actions: dict[tuple[str, str], tuple[str, float]] = {}

    # ── Phase 1: Configure ──────────────────────────────────────────────────

    async def configure(self) -> None:
        """Phase 1: Load config, load family profiles, open MemoryLayer."""
        from src.config import load_config

        self.config = load_config(self._config_path)
        logger.info("Config loaded from %s", self._config_path)

        from src.core.profiles import load_profiles_from_config

        if self.config.profiles:
            load_profiles_from_config([p.model_dump() for p in self.config.profiles])
            logger.info("Loaded %d family profiles from config", len(self.config.profiles))
        else:
            logger.debug("No family profiles configured")

        from src.memory.layer import MemoryLayer

        self.memory_layer = MemoryLayer(
            db_path="data/brain.db",
            chromadb_path="./data/chromadb",
            ollama_host=self.config.ollama_host,
        )
        await self.memory_layer.open()
        logger.info("MemoryLayer opened (episodic + semantic + procedural)")

    # ── Phase 2: Wire HAL ───────────────────────────────────────────────────

    async def wire_hal(self) -> None:
        """Phase 2: Create IntegrationBus, StateStore, HAL, add all 7 platforms."""
        from src.core.bus import IntegrationBus

        self.bus = IntegrationBus()

        from src.core.awareness import HouseAwareness
        from src.hal import HAL, StateStore
        from src.hal.platforms import PLATFORM_REGISTRY

        self._hal_state_store = StateStore(db_path=Path("data/hal_state.db"))
        await self._hal_state_store.start()
        self.awareness = HouseAwareness(self._hal_state_store)

        self.hal = HAL(self._hal_state_store)

        self.platform_health_monitor = PlatformHealthMonitor(self.hal)
        logger.info("PlatformHealthMonitor created")

        # Load platforms from registry in config order
        for name in self.config.platforms:
            platform_cls = PLATFORM_REGISTRY.get(name)
            if platform_cls is None:
                logger.warning("Unknown platform %r in config.platforms, skipping", name)
                continue
            platform = platform_cls.create_from_config(self.config)
            if platform is None:
                logger.debug("Platform %r not configured, skipping", name)
                continue
            await self.hal.add_platform(platform)
            logger.info("Platform %r added to HAL (%d devices)", name, self.hal.device_count)

        logger.info("HAL total devices: %d", self.hal.device_count)

        # Seed device knowledge from whichever configured platforms discovered devices.
        if self.hal.device_count:
            try:
                await self.memory_layer.procedural.seed_from_devices(self.hal.get_all_devices())
                logger.info("Device knowledge seeded from HAL devices")
            except Exception as e:
                logger.warning("Device knowledge seeding failed: %s", e)

        # House Topology
        from src.core.topology import HouseTopology

        config_dir = Path(self._config_path).parent
        topology_path = config_dir / "house_topology.yaml"
        self.topology = HouseTopology.load(topology_path)
        if self.topology:
            mismatches = self.topology.validate_rooms(self.hal.get_all_rooms())
            if mismatches["in_topology_only"]:
                logger.warning("Topology rooms not in HAL: %s", mismatches["in_topology_only"])
            if mismatches["in_registry_only"]:
                logger.info("HAL rooms without topology: %s", mismatches["in_registry_only"])
            logger.info("HouseTopology loaded (%d rooms)", len(self.topology.graph.nodes))
            self.hal.set_topology(self.topology)
            logger.info("HAL: topology injected (room filter active)")
            self.hal.rules.set_topology(self.topology)
            logger.info("HAL rules: topology injected")
        else:
            logger.info("No house_topology.yaml found — spatial reasoning disabled")

        # Weather → RuleEngine
        weather = self.hal.get_platform("weather")
        if weather is not None:
            self.hal.rules.set_weather_source(weather)
            logger.info("HAL rules: weather injected (WeatherPlatform)")

        # Energy Awareness
        from src.core.awareness import EnergyAwareness

        self.energy_awareness = EnergyAwareness(state_store=self._hal_state_store, hal=self.hal)
        logger.info("EnergyAwareness created")
        self.hal.rules.set_energy(self.energy_awareness)
        logger.info("HAL rules: energy injected")

    # ── Phase 3: Wire Tools ─────────────────────────────────────────────────

    async def wire_tools(self) -> Any:
        """Phase 3: Create PresenceManager, ToolContext, call set_context on all 8 modules.

        Returns the ToolContext instance.
        """
        from src.core.presence import PresenceManager

        self.presence_manager = PresenceManager(hal=self.hal)
        logger.info("PresenceManager created (HAL-backed)")

        from src.tools.context import ToolContext

        self.tool_context = ToolContext(
            hal=self.hal,
            presence_manager=self.presence_manager,
            energy_awareness=self.energy_awareness,
            rule_engine=self.hal.rules,
            memory_layer=self.memory_layer,
            schedule_engine=self.schedule_engine,  # May be None until wire_agents()
            topology=self.topology,
            awareness=self.awareness,
            platform_health=self.platform_health_monitor,
            latitude=self.config.home.latitude,
            longitude=self.config.home.longitude,
            timezone=self.config.home.timezone,
        )

        # Call set_context on all 8 tool modules
        from src.tools.comfort import set_context as comfort_set_ctx
        from src.tools.security import set_context as security_set_ctx
        from src.tools.energy import set_context as energy_set_ctx
        from src.tools.presence import set_context as presence_set_ctx
        from src.tools.memory import set_context as memory_set_ctx
        from src.tools.topology import set_context as topology_set_ctx
        from src.tools.rules import set_context as rules_set_ctx
        from src.tools.schedule import set_context as schedule_set_ctx
        from src.tools.scenes import set_context as scenes_set_ctx

        comfort_set_ctx(self.tool_context)
        security_set_ctx(self.tool_context)
        energy_set_ctx(self.tool_context)
        presence_set_ctx(self.tool_context)
        memory_set_ctx(self.tool_context)
        topology_set_ctx(self.tool_context)
        rules_set_ctx(self.tool_context)
        schedule_set_ctx(self.tool_context)
        scenes_set_ctx(self.tool_context)
        logger.info("ToolContext injected into all 9 tool modules")

        return self.tool_context

    # ── Phase 4: Wire Agents ────────────────────────────────────────────────

    async def wire_agents(self) -> None:
        """Phase 4: Create ScheduleEngine, Strands agents, supervisor, router client."""
        # Build tool registry for ScheduleEngine direct-dispatch
        from src.tools.comfort import lights_control, blinds_control, heating_control
        from src.tools.comfort import execute_batch, get_room_state
        from src.tools.security import alarm_control, door_open, get_security_status
        from src.tools.presence import who_is_home

        tool_registry: dict[str, Any] = {
            "lights_control": lights_control,
            "blinds_control": blinds_control,
            "heating_control": heating_control,
            "execute_batch": execute_batch,
            "get_room_state": get_room_state,
            "alarm_control": alarm_control,
            "door_open": door_open,
            "get_security_status": get_security_status,
            "who_is_home": who_is_home,
        }
        logger.info("Tool registry built (%d tools)", len(tool_registry))

        # ScheduleEngine (supervisor_fn=None initially — circular dep resolved below)
        from src.background.scheduler import ScheduleEngine

        self.schedule_engine = ScheduleEngine(
            memory_layer=self.memory_layer,
            tool_registry=tool_registry,
            config=self.config,
            supervisor_fn=None,
            matrix=None,  # Wired after MatrixBot creation in start()
        )
        self._schedule_engine_obj = self.schedule_engine

        # Re-create ToolContext with schedule_engine (was None in wire_tools())
        from src.tools.context import ToolContext

        self.tool_context = ToolContext(
            hal=self.hal,
            presence_manager=self.presence_manager,
            energy_awareness=self.energy_awareness,
            rule_engine=self.hal.rules,
            memory_layer=self.memory_layer,
            schedule_engine=self.schedule_engine,
            topology=self.topology,
            awareness=self.awareness,
            platform_health=self.platform_health_monitor,
            latitude=self.config.home.latitude,
            longitude=self.config.home.longitude,
            timezone=self.config.home.timezone,
        )
        # Update schedule tool with the new context (which now has schedule_engine)
        from src.tools.schedule import set_context as schedule_set_ctx

        schedule_set_ctx(self.tool_context)
        logger.info("ToolContext updated with ScheduleEngine")

        # Strands agents
        from src.agents.comfort import create_comfort_agent
        from src.agents.energy import create_energy_agent
        from src.agents.schedule import create_schedule_agent
        from src.agents.security import create_security_agent
        from src.agents.supervisor import create_supervisor
        from src.core.guardrails import GuardrailContext, GuardrailProvider

        guardrail_context = GuardrailContext(presence_manager=self.presence_manager)
        self.guardrail_provider = GuardrailProvider(context=guardrail_context)
        logger.info("GuardrailProvider created")

        # Training data hooks (AD-11: router excluded; all other agents get training hooks)
        training_hooks: list = []
        if self.config.training_data.enabled:
            from src.training.writer import TrainingDataWriter
            from src.training.hooks import TrainingDataHookProvider
            from src.agents.router import set_training_writer as router_set_writer

            self._training_writer = TrainingDataWriter(self.config.training_data.output_dir)
            self.training_hook_provider = TrainingDataHookProvider(self._training_writer)
            training_hooks = [self.guardrail_provider, self.training_hook_provider]
            router_set_writer(self._training_writer)
            logger.info(
                "TrainingDataWriter + hooks created (output_dir=%s)",
                self.config.training_data.output_dir,
            )
        else:
            training_hooks = [self.guardrail_provider]

        self.agents_dict = {
            "comfort": create_comfort_agent(self.config, hooks=training_hooks),
            "security": create_security_agent(self.config, hooks=training_hooks),
            "schedule": create_schedule_agent(self.config, hooks=training_hooks),
            "energy": create_energy_agent(self.config, hooks=training_hooks),
        }
        self.supervisor = create_supervisor(
            self.config,
            agents=self.agents_dict,
            memory_layer=self.memory_layer,
            hooks=training_hooks,
        )
        logger.info("Strands agents created (%d specialists)", len(self.agents_dict))

        # Conversation window
        from src.core.conversation import ConversationWindow

        self.conversation = ConversationWindow(max_exchanges=5, ttl_minutes=15)

        # Resolve circular dependency: supervisor ← ScheduleEngine
        self.schedule_engine.supervisor_fn = self.supervisor

        # Offline adapters
        _gira = self.hal.get_platform("gira_x1")
        _knx_ip = self.hal.get_platform("knx_ip")
        self.offline_adapters = {
            "gira": _gira.gira_adapter if _gira and hasattr(_gira, "gira_adapter") else None,
            "home_control_available": _gira is not None or _knx_ip is not None,
            "schedule": self.schedule_engine,
        }

        # Strands Agent for router classification (provider-agnostic)
        from src.agents import create_model_for_slot
        from src.config import ModelSlot
        from src.agents.prompts import ROUTER_PROMPT
        from strands import Agent as StrandsAgent

        router_model = create_model_for_slot(self.config, ModelSlot.ROUTER_TIER, max_tokens=512)
        # AD-11: Router agent gets guardrail hooks ONLY — no training hooks.
        # Router runs via asyncio.to_thread() → temporary event loop → create_task() silently dropped.
        self.router_agent = StrandsAgent(
            model=router_model,
            system_prompt=ROUTER_PROMPT,
            tools=[],
            hooks=[self.guardrail_provider],
        )
        logger.info("Router agent created with slot ROUTER_TIER (guardrail hooks only)")

        self.agent_dispatch = dict(self.agents_dict)

    # ── Phase 5: Start ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Phase 5: Start all platforms, populate state, register listeners,
        start background tasks, start Matrix.
        """
        # Matrix Bot (needed before StateStore listeners that reference it)
        from src.bot.matrix import MatrixBot

        allowed_user_ids = [
            matrix_id for profile in self.config.profiles for matrix_id in profile.matrix_ids
        ]
        admin_user_ids = [
            matrix_id
            for profile in self.config.profiles
            if profile.trust_level == "admin"
            for matrix_id in profile.matrix_ids
        ]
        if self.config.matrix.password and (
            not self.config.matrix.room_id or not allowed_user_ids or not admin_user_ids
        ):
            raise RuntimeError(
                "Matrix requires an allowed room and at least one configured admin profile"
            )
        self.matrix = MatrixBot(
            homeserver=self.config.matrix.homeserver,
            user_id=self.config.matrix.user_id,
            password=self.config.matrix.password,
            allowed_room_ids=([self.config.matrix.room_id] if self.config.matrix.room_id else []),
            allowed_user_ids=allowed_user_ids,
        )

        # Wire Matrix into ScheduleEngine (was not available at engine creation time)
        self.schedule_engine._matrix = self.matrix

        # WhisperSTT
        from src.voice.stt import WhisperSTT

        try:
            stt = WhisperSTT(model=self.config.whisper.model, language=self.config.whisper.language)
            self.matrix.set_whisper(stt)
            logger.info("WhisperSTT initialized (%s model)", self.config.whisper.model)
        except RuntimeError as e:
            logger.warning("WhisperSTT not available: %s", e)

        # Register StateStore listeners
        from src.hal.models import DeviceType as _DeviceType

        async def _on_entrance_detection(
            info: Any, old_state: Any, new_state: Any, source: str
        ) -> None:
            if info.device_type != _DeviceType.CAMERA:
                return
            camera_name = info.platform_data.get("camera_name", "")
            if camera_name not in self.config.frigate.entrance_cameras:
                return
            old_detecting = old_state.get("is_detecting", "false") if old_state else "false"
            new_detecting = new_state.get("is_detecting", "false")
            if new_detecting == "true" and old_detecting == "false":
                for adapter in self.hal.find_room(info.room):
                    if adapter.info.device_type in (
                        _DeviceType.DIMMABLE_LIGHT,
                        _DeviceType.SWITCH,
                    ):
                        try:
                            await self.hal.execute(adapter.device_id, "turn_on")
                        except Exception as e:
                            logger.warning("Entrance light trigger failed: %s", e)

        self.hal.state_store.on_change(_on_entrance_detection)
        logger.info("Entrance light trigger listener registered")

        async def _on_door_bell_state(
            info: Any, old_state: Any, new_state: Any, source: str
        ) -> None:
            if info.device_type != _DeviceType.DOOR_STATION:
                return
            old_bell = old_state.get("last_bell") if old_state else None
            new_bell = new_state.get("last_bell")
            if new_bell and new_bell != old_bell:
                try:
                    if self.config.matrix.room_id:
                        await self.matrix.send(
                            self.config.matrix.room_id, "Es hat jemand geklingelt."
                        )
                except Exception as exc:
                    logger.warning("Door bell Matrix notification failed: %s", exc)

        self.hal.state_store.on_change(_on_door_bell_state)
        logger.info("Door bell → Matrix notification listener registered")

        async def _on_camera_state_for_presence(
            info: Any, old_state: Any, new_state: Any, source: str
        ) -> None:
            if info.device_type == _DeviceType.CAMERA:
                camera_name = info.platform_data.get("camera_name", "")
                zones_str = new_state.get("zones", "")
                if zones_str:
                    self.presence_manager.update_camera_zone(camera_name, zones_str.split(","))

        self.hal.state_store.on_change(_on_camera_state_for_presence)
        logger.info("Camera state → PresenceManager zone tracking listener registered")

        async def _on_presence_for_schedule(
            info: Any, old_state: Any, new_state: Any, source: str
        ) -> None:
            if info.device_type == _DeviceType.PRESENCE_SENSOR:
                is_home = str(new_state.get("is_home", "false")).lower() in ("true", "1")
                old_home = (
                    str(old_state.get("is_home", "false")).lower() in ("true", "1")
                    if old_state
                    else False
                )
                if is_home != old_home:
                    await self.schedule_engine.on_presence_event(info.name, is_home)

        self.hal.state_store.on_change(_on_presence_for_schedule)
        logger.info("Presence → ScheduleEngine listener registered")

        async def _on_weather_for_schedule(
            info: Any, old_state: Any, new_state: Any, source: str
        ) -> None:
            if info.device_type == _DeviceType.WEATHER_SENSOR:
                is_raining = new_state.get("is_raining", False)
                was_raining = old_state.get("is_raining", False) if old_state else False
                if is_raining and not was_raining:
                    await self.schedule_engine.on_weather_event("raining", True)
                elif was_raining and not is_raining:
                    await self.schedule_engine.on_weather_event("raining", False)

        self.hal.state_store.on_change(_on_weather_for_schedule)
        logger.info("Weather → ScheduleEngine listener registered")

        # Wire on_message handler
        self.matrix.set_message_handler(self._on_message)

        # Start all integrations (lifecycle bus)
        await self.bus.start_all()
        logger.info("All integrations started")

        # HAL Phase B: initial state population
        gira = self.hal.get_platform("gira_x1")
        if gira is not None and hasattr(gira, "start_runtime"):
            await gira.start_runtime()
        await self.hal.populate_initial_state()
        logger.info("HAL: initial state populated (%d devices)", self.hal.device_count)
        await self.hal.rules.load_custom_rules()
        logger.info(
            "HAL rules: custom rules loaded, %d violations",
            len(self.hal.rules.get_current_violations()),
        )

        # PresenceManager: sync window cache from StateStore
        await self.presence_manager.connect_hal(self.hal)

        # Start background tasks
        from src.background.monitor import ProactiveMonitor
        from src.background.patterns import PatternDetector
        from src.background.reflection import ReflectionAgent

        self._monitor = ProactiveMonitor(
            rule_engine=self.hal.rules,
            matrix=self.matrix,
            config=self.config,
        )
        self._pattern_detector = PatternDetector(memory_layer=self.memory_layer, config=self.config)
        self._reflection_agent = ReflectionAgent(
            memory_layer=self.memory_layer,
            hal=self.hal,
            matrix=self.matrix,
            config=self.config,
            rule_engine=self.hal.rules,
        )

        if self.config.training_data.enabled and self._training_writer is not None:
            from src.background.patterns import set_training_writer as patterns_set_writer
            from src.background.reflection import set_training_writer as reflection_set_writer

            patterns_set_writer(self._training_writer)
            reflection_set_writer(self._training_writer)
            logger.info("TrainingDataWriter wired into PatternDetector and ReflectionAgent")

        self._monitor.start()
        self._pattern_detector.start()
        self._reflection_agent.start()
        self.schedule_engine.start()
        self.platform_health_monitor.start()
        logger.info(
            "Background tasks started "
            "(monitor, pattern_detector, reflection_agent, schedule_engine, platform_health_monitor)"
        )

        # Collect internal task handles for the watcher
        self._background_tasks = []
        if self._monitor and self._monitor._task:
            self._background_tasks.append(("proactive_monitor", self._monitor._task))
        if self._pattern_detector and self._pattern_detector._task:
            self._background_tasks.append(("pattern_detector", self._pattern_detector._task))
        if self._reflection_agent and self._reflection_agent._task:
            self._background_tasks.append(("reflection_agent", self._reflection_agent._task))
        if self.schedule_engine and self.schedule_engine._task:
            self._background_tasks.append(("schedule_engine", self.schedule_engine._task))
        if self.platform_health_monitor and self.platform_health_monitor._task:
            self._background_tasks.append(
                ("platform_health_monitor", self.platform_health_monitor._task)
            )

        # Start the task watcher
        self._watcher_task = asyncio.create_task(
            self._monitor_background_tasks(), name="task_watcher"
        )
        logger.info("Background task watcher started (%d tasks)", len(self._background_tasks))

        # Start Matrix bot
        if self.config.matrix.password:
            try:
                await self.matrix.start()
                room_count = (
                    len(self.topology.graph.nodes)
                    if self.topology
                    else len(self.hal.get_all_rooms())
                )
                await self.matrix.send(
                    self.config.matrix.room_id,
                    f"KNX Agent gestartet. {room_count} Räume geladen.",
                )
            except Exception as e:
                logger.error("Matrix bot failed to start: %s", e)
        else:
            logger.warning("Matrix password not configured — bot not started")

    # ── Phase 6: Stop ───────────────────────────────────────────────────────

    async def stop(self) -> None:
        """Phase 6: Graceful shutdown in reverse order with timeout protection."""
        logger.info("Shutting down...")

        # Send shutdown Matrix message
        if (
            self.matrix is not None
            and self.config is not None
            and self.config.matrix.password
            and self.config.matrix.room_id
        ):
            try:
                await self.matrix.send(
                    self.config.matrix.room_id, "KNX Agent wird heruntergefahren."
                )
            except Exception:
                pass

        # Cancel the watcher first
        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()

        # Stop background tasks via their .stop() methods (5s timeout each)
        for name, obj in [
            ("proactive_monitor", self._monitor),
            ("pattern_detector", self._pattern_detector),
            ("reflection_agent", self._reflection_agent),
            ("schedule_engine", self.schedule_engine),
            ("platform_health_monitor", self.platform_health_monitor),
        ]:
            if obj is not None:
                obj.stop()
                if obj._task and not obj._task.done():
                    try:
                        await asyncio.wait_for(obj._task, timeout=5.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        logger.warning("Background task %s did not stop cleanly", name)
        logger.info("Background tasks stopped")

        # Stop HAL (10s timeout)
        if self.hal:
            try:
                await asyncio.wait_for(self.hal.stop(), timeout=10.0)
            except asyncio.TimeoutError as timeout_error:
                logger.error("HAL shutdown timed out")
                del timeout_error
        logger.info("HAL stopped")

        # Stop Matrix
        if self.matrix:
            try:
                await self.matrix.stop()
            except Exception as e:
                logger.warning("Matrix stop failed: %s", e)

        # Stop bus
        if self.bus:
            try:
                await self.bus.stop_all()
            except Exception as e:
                logger.warning("Bus stop failed: %s", e)

        # Close memory layer
        if self.memory_layer:
            try:
                await self.memory_layer.close()
                logger.info("MemoryLayer closed")
            except Exception as e:
                logger.warning("MemoryLayer close failed: %s", e)

    # ── Health Check ────────────────────────────────────────────────────────

    async def health(self) -> dict[str, bool]:
        """Return health status of all platforms and services."""
        result: dict[str, bool] = {}
        if self.hal:
            for name, platform in self.hal._platforms.items():
                try:
                    result[f"platform:{name}"] = await platform.health_check()
                except Exception:
                    result[f"platform:{name}"] = False
        return result

    # ── Background task watcher ─────────────────────────────────────────────

    async def _monitor_background_tasks(self) -> None:
        """Periodically check background tasks for unexpected completion."""
        while True:
            await asyncio.sleep(30)
            for name, task in self._background_tasks:
                if task.done():
                    exc = task.exception() if not task.cancelled() else None
                    if exc:
                        logger.error("Background task %s crashed: %s", name, exc, exc_info=exc)
                    elif task.cancelled():
                        logger.info("Background task %s was cancelled", name)
                    else:
                        logger.warning("Background task %s completed unexpectedly", name)

    # ── Message handler (extracted from main() closure) ─────────────────────

    async def _dispatch_with_overflow_handling(
        self,
        agent: Any,
        augmented_text: str,
        agent_name: str,
        sender: str,
        intent: str,
    ) -> str:
        """Dispatch to agent with retry on transient errors and context truncation on overflow.

        Transient API errors (timeouts, 429, 500/502/503) are retried with backoff.
        First overflow: truncate context and retry once.
        Second overflow: use minimal context fallback.
        Logs all overflow and retry events for monitoring.
        """
        from src.agents.prompts import build_embodied_context
        from src.agents.retry import invoke_with_retry

        try:
            return await invoke_with_retry(agent, augmented_text, label=agent_name)
        except ContextWindowOverflowException as overflow_error:
            del overflow_error
            logger.warning(
                "Context window overflow for %s agent, attempting truncated retry",
                agent_name,
            )

            context = ""
            try:
                context = await build_embodied_context(
                    awareness=self.awareness,
                    hal=self.hal,
                    rule_engine=self.hal.rules,
                    presence=self.presence_manager,
                    memory_layer=self.memory_layer,
                    topology=self.topology,
                    user=sender,
                    intent=intent,
                    agent=agent_name,
                    platform_health=self.platform_health_monitor,
                    truncate_changes=True,
                    truncate_memories=True,
                )
            except Exception as e:
                logger.warning("Truncated context assembly failed: %s", e)

            truncated_text = augmented_text
            if context:
                lines = augmented_text.split("\n\n")
                if len(lines) > 1 and "Aktuelle Zeit" in lines[0]:
                    truncated_text = f"{context}\n\n" + "\n\n".join(lines[1:])
                else:
                    truncated_text = f"{context}\n\n{augmented_text}"

            try:
                return await invoke_with_retry(
                    agent, truncated_text, label=f"{agent_name}_truncated"
                )
            except ContextWindowOverflowException as overflow_error:
                del overflow_error
                logger.error(
                    "Context window overflow persists for %s agent after truncation, "
                    "using minimal fallback",
                    agent_name,
                )

                minimal_context = ""
                try:
                    minimal_context = await build_embodied_context(
                        awareness=self.awareness,
                        hal=self.hal,
                        rule_engine=self.hal.rules,
                        presence=self.presence_manager,
                        memory_layer=self.memory_layer,
                        topology=self.topology,
                        user=sender,
                        intent=intent,
                        agent=agent_name,
                        platform_health=self.platform_health_monitor,
                        minimal_mode=True,
                    )
                except Exception as e:
                    logger.warning("Minimal context assembly failed: %s", e)

                if minimal_context:
                    fallback_text = f"{minimal_context}\n\nAktuelle Anfrage:\n{augmented_text.split('Aktuelle Anfrage:')[-1].strip() if 'Aktuelle Anfrage:' in augmented_text else augmented_text}"
                else:
                    fallback_text = (
                        f"Benutzer: {sender}\nAnfrage: {intent}\n\nAktuelle Anfrage:\n"
                        f"{augmented_text.split('Aktuelle Anfrage:')[-1].strip() if 'Aktuelle Anfrage:' in augmented_text else augmented_text}"
                    )

                try:
                    return await invoke_with_retry(
                        agent, fallback_text, label=f"{agent_name}_minimal"
                    )
                except ContextWindowOverflowException as overflow_error:
                    del overflow_error
                    logger.critical(
                        "Context window overflow even with minimal context for %s agent, "
                        "request cannot be processed",
                        agent_name,
                    )
                    return (
                        "Die Anfrage ist zu umfangreich für meine aktuelle Verarbeitungskapazität. "
                        "Bitte versuchen Sie es mit einer kürzeren Anfrage."
                    )
        except Exception as e:
            logger.error("%s agent error: %s", agent_name, e)
            return (
                "Entschuldigung, bei der Verarbeitung ist ein Fehler aufgetreten. "
                "Bitte versuche es erneut."
            )

    async def _on_message(self, room_id: str, sender: str, text: str) -> str:
        """Authorize a Matrix principal and bind immutable request security state."""
        from src.core.guardrails import RequestSource, request_security_context
        from src.core.profiles import resolve_user

        profile = resolve_user(sender)
        if profile is None:
            logger.warning("Rejected message from unknown Matrix principal")
            return "Nicht autorisiert."

        key = (room_id.lower(), sender.lower())
        pending = self._pending_security_actions.get(key)
        normalized = text.strip().casefold()
        affirmative = normalized in {"ja", "ja, bitte", "bestätigen", "bestaetigen"}
        negative = normalized in {"nein", "abbrechen", "stopp"}
        if pending is not None:
            action, expires_at = pending
            if time.monotonic() > expires_at:
                self._pending_security_actions.pop(key, None)
            elif negative:
                self._pending_security_actions.pop(key, None)
                return "Aktion abgebrochen."
            elif affirmative:
                self._pending_security_actions.pop(key, None)
                with request_security_context(
                    user_profile=profile,
                    source=RequestSource.USER,
                    confirmed_actions={action},
                ):
                    if action == "door_open":
                        from src.tools.security import door_open

                        return await door_open()
                    if action == "alarm_disarm":
                        from src.tools.security import alarm_control

                        return await alarm_control("disarm")

        with request_security_context(user_profile=profile, source=RequestSource.USER):
            return await self._handle_message(room_id, sender, text)

    async def _handle_message(self, room_id: str, sender: str, text: str) -> str:
        """Brain message handler: route → execute → respond.

        Routing tiers:
        1. Offline regex (instant, free)
        2. Haiku classification (fast, cheap)
        3. Direct agent dispatch when single agent identified (skip supervisor)
        4. Supervisor only for multi-agent or ambiguous requests
        """
        from src.agents.prompts import build_embodied_context
        from src.agents.router import route

        logger.info("Processing authorized Matrix request")

        # Set training data contextvars (AD-15, AD-12, AD-10)
        if self.config.training_data.enabled:
            import uuid as _uuid
            from src.training.context import set_request_id, set_user, set_agent_name, set_model_id

            _request_id = _uuid.uuid4().hex[:12]
            set_request_id(_request_id)
            set_user(sender)
            # agent_name and model_id are set conservatively here;
            # direct dispatch paths below will refine if needed
            set_agent_name("supervisor")
            set_model_id("")

        routing = await route(
            text,
            router_agent=self.router_agent,
            check_internet=False,
        )
        logger.info(
            "Routed: intent=%s, agents=%s, model=%s",
            routing.intent,
            routing.agents,
            routing.model,
        )

        # Build augmented text with conversation history for LLM-routed messages only.
        augmented_text = text
        if routing.model != "offline":
            history = self.conversation.get_context(room_id, sender)
            if history:
                augmented_text = f"{history}\n\nAktuelle Anfrage:\n{text}"

        async def _build_ctx(agent_name: str, user: str, intent: str) -> str:
            kalender = ""
            calendar = self.hal.get_platform("calendar")
            if calendar is not None and hasattr(calendar, "format_for_prompt"):
                kalender = calendar.format_for_prompt()
            weather_summary = ""
            try:
                weather = self.hal.get_platform("weather")
                if weather is not None and hasattr(weather, "get_summary"):
                    weather_summary = await weather.get_summary()
            except Exception as e:
                logger.debug("Weather summary unavailable: %s", e)
            return await build_embodied_context(
                awareness=self.awareness,
                hal=self.hal,
                rule_engine=self.hal.rules,
                presence=self.presence_manager,
                memory_layer=self.memory_layer,
                topology=self.topology,
                user=user,
                intent=intent,
                kalender=kalender,
                agent=agent_name,
                weather=weather_summary,
                platform_health=self.platform_health_monitor,
            )

        response = ""
        agent_name = "supervisor"
        if routing.model == "offline" and routing.offline_action:
            from src.agents.offline import execute_offline

            response = await execute_offline(
                routing.offline_action, routing.offline_params, self.offline_adapters
            )
            if routing.offline_action == "door_open":
                from src.core.guardrails import get_request_security_context

                request = get_request_security_context()
                if (
                    request is not None
                    and getattr(request.user_profile, "trust_level", "") == "admin"
                ):
                    key = (room_id.lower(), sender.lower())
                    self._pending_security_actions[key] = (
                        "door_open",
                        time.monotonic() + 60.0,
                    )
                    response = (
                        "Soll ich die Tür wirklich öffnen? Antworte innerhalb 60 Sekunden mit 'Ja'."
                    )
                else:
                    response = "Du hast keine Berechtigung für diese Aktion."
            if response.startswith("offline:"):
                context = ""
                try:
                    agent_name = routing.agents[0] if routing.agents else "supervisor"
                    context = await _build_ctx(agent_name, sender, routing.intent)
                except Exception as e:
                    logger.warning("Embodied context assembly failed: %s", e)
                if context:
                    augmented_text = f"{context}\n\n{augmented_text}"
                target = self.agent_dispatch.get(routing.agents[0]) if routing.agents else None
                if target:
                    response = await self._dispatch_with_overflow_handling(
                        target, augmented_text, agent_name, sender, routing.intent
                    )
                else:
                    response = await self._dispatch_with_overflow_handling(
                        self.supervisor, augmented_text, "supervisor", sender, routing.intent
                    )
        elif len(routing.agents) == 1 and routing.agents[0] in self.agent_dispatch:
            agent_name = routing.agents[0]
            logger.info("Direct dispatch to %s agent (skipping supervisor)", agent_name)
            context = ""
            try:
                context = await _build_ctx(agent_name, sender, routing.intent)
            except Exception as e:
                logger.warning("Embodied context assembly failed: %s", e)
            if context:
                augmented_text = f"{context}\n\n{augmented_text}"
            response = await self._dispatch_with_overflow_handling(
                self.agent_dispatch[agent_name], augmented_text, agent_name, sender, routing.intent
            )
        else:
            context = ""
            try:
                context = await _build_ctx("supervisor", sender, routing.intent)
            except Exception as e:
                logger.warning("Embodied context assembly failed: %s", e)
            if context:
                augmented_text = f"{context}\n\n{augmented_text}"
            response = await self._dispatch_with_overflow_handling(
                self.supervisor, augmented_text, "supervisor", sender, routing.intent
            )

        # Convert deterministic security prompts into short-lived, single-use grants.
        from src.core.guardrails import get_request_security_context

        request = get_request_security_context()
        is_admin = (
            request is not None and getattr(request.user_profile, "trust_level", "") == "admin"
        )
        pending_action: str | None = None
        if is_admin and "Soll ich die Tür wirklich öffnen?" in response:
            pending_action = "door_open"
        elif is_admin and "erfordert eine ausdrückliche Bestätigung" in response:
            pending_action = "alarm_disarm"
            response = (
                "Soll ich die Alarm-Anlage wirklich unscharf stellen? "
                "Antworte innerhalb 60 Sekunden mit 'Ja'."
            )
        if pending_action is not None:
            key = (room_id.lower(), sender.lower())
            self._pending_security_actions[key] = (
                pending_action,
                time.monotonic() + 60.0,
            )

        # Record exchange in conversation window
        self.conversation.add(room_id, sender, text, response)

        # Log interaction to memory (fire and forget)
        try:
            await self.memory_layer.log_interaction(
                user=sender,
                text=text,
                response=response,
                tool_calls=None,
            )
        except Exception as e:
            logger.warning("Memory log_interaction failed: %s", e)

        return response
