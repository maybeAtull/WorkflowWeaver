import asyncio

from app.events import EventBus, RunEvent


async def _collect(bus: EventBus, run_id: str) -> list[RunEvent]:
    return [event async for event in bus.subscribe(run_id)]


def test_events_are_numbered_per_run_in_order() -> None:
    bus = EventBus()
    bus.publish("a", "run_started")
    bus.publish("b", "run_started")
    second = bus.publish("a", "step_started", step_id="s1")
    assert second.seq == 2 and second.data == {"step_id": "s1"}
    assert [e.seq for e in bus.history("b")] == [1]


async def test_a_late_subscriber_gets_the_history_then_stops_at_completion() -> None:
    bus = EventBus()
    bus.publish("r", "run_started")
    bus.publish("r", "run_completed", status="completed")
    events = await asyncio.wait_for(_collect(bus, "r"), timeout=1)
    assert [e.type for e in events] == ["run_started", "run_completed"]


async def test_a_live_subscriber_receives_replay_then_new_events_without_duplicates() -> (
    None
):
    bus = EventBus()
    bus.publish("r", "run_started")
    task = asyncio.create_task(_collect(bus, "r"))
    await asyncio.sleep(0)  # let the subscriber attach
    bus.publish("r", "step_started", step_id="s1")
    bus.publish("r", "run_completed", status="completed")
    events = await asyncio.wait_for(task, timeout=1)
    assert [e.seq for e in events] == [1, 2, 3]


async def test_two_subscribers_each_get_every_event() -> None:
    bus = EventBus()
    tasks = [asyncio.create_task(_collect(bus, "r")) for _ in range(2)]
    await asyncio.sleep(0)
    bus.publish("r", "run_started")
    bus.publish("r", "run_completed", status="failed")
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
    assert all([e.type for e in r] == ["run_started", "run_completed"] for r in results)


async def test_a_subscriber_is_unregistered_when_it_stops_listening() -> None:
    bus = EventBus()
    bus.publish("r", "run_started")
    stream = bus.subscribe("r")
    await stream.__anext__()  # attached
    await stream.aclose()
    assert bus._channels["r"].queues == []


def test_discard_forgets_a_run() -> None:
    bus = EventBus()
    bus.publish("r", "run_started")
    bus.discard("r")
    assert bus.history("r") == []


def test_events_serialize_to_json() -> None:
    event = EventBus().publish("r", "step_retry", step_id="s1", attempt=2)
    assert RunEvent.model_validate_json(event.model_dump_json()) == event


def test_a_run_is_known_to_the_bus_from_open_until_discard() -> None:
    bus = EventBus()
    assert not bus.has_run("r")
    bus.open("r")
    assert bus.has_run("r") and bus.history("r") == []
    bus.discard("r")
    assert not bus.has_run("r")
