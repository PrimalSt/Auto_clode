from autogenerator.runner import LocalEventBus
from autogenerator.runner import events as ev


def test_replay_after_reconnect():
    bus = LocalEventBus(buffer=3)
    for i in range(5):
        bus.publish("job", {"n": i})
    sub = bus.subscribe(after=3)
    got = sub.get(0.1)
    assert got is not None and got.id == 4 and got.data == {"n": 3}
    assert sub.get(0.1).id == 5 and sub.get(0.05) is None
    bus.publish("changed", {"what": "sources"})
    assert sub.get(0.1).kind == "changed"
    assert bus.last_id == 6
    sub.close()
    assert sub.get(0.05) is None


def test_lagging_subscriber_is_dropped(monkeypatch):
    monkeypatch.setattr(ev, "SUBSCRIBER_QUEUE", 2)
    bus = LocalEventBus()
    sub = bus.subscribe()
    for i in range(3):
        bus.publish("job", {"n": i})
    assert sub.closed
    assert [sub.get(0.01).id, sub.get(0.01).id] == [1, 2]
