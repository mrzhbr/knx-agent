from __future__ import annotations


from src.core.bus import IntegrationBus


class MockIntegration:
    name = "mock"
    started = False
    stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def health_check(self) -> bool:
        return True


async def test_register_and_get():
    bus = IntegrationBus()
    integration = MockIntegration()
    bus.register(integration)
    assert bus.get("mock") is integration


async def test_start_all():
    bus = IntegrationBus()
    integration = MockIntegration()
    bus.register(integration)
    await bus.start_all()
    assert integration.started is True


async def test_stop_all():
    bus = IntegrationBus()
    integration = MockIntegration()
    bus.register(integration)
    await bus.stop_all()
    assert integration.stopped is True


async def test_health():
    bus = IntegrationBus()
    integration = MockIntegration()
    bus.register(integration)
    health = await bus.health()
    assert health == {"mock": True}


async def test_unregister():
    bus = IntegrationBus()
    integration = MockIntegration()
    bus.register(integration)
    bus.unregister("mock")
    assert bus.get("mock") is None


async def test_no_on_emit_off_methods():
    """IntegrationBus must not have on/emit/off methods after Plan 04 refactor."""
    bus = IntegrationBus()
    assert not hasattr(bus, "on")
    assert not hasattr(bus, "emit")
    assert not hasattr(bus, "off")
