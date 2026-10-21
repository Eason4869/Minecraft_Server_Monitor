import asyncio
from types import SimpleNamespace

import pytest


class Config(dict):
    def save_config(self):
        self.saved = True


def test_sessions_are_isolated_and_empty_means_no_access():
    from minecraft_multi_monitor.subscriptions import SubscriptionStore

    store = SubscriptionStore(
        Config(
            group_entries=[
                {"session": "bot-a:GroupMessage:123", "server_keys": ["a"]},
                {"session": "bot-b:GroupMessage:123", "server_keys": []},
            ]
        ),
        {"a", "b"},
    )
    assert store.get("bot-a:GroupMessage:123").server_keys == ("a",)
    assert store.get("bot-b:GroupMessage:123").server_keys == ()
    assert store.get("bot-c:GroupMessage:123") is None


def test_save_survives_reload_and_rolls_back_failure():
    from minecraft_multi_monitor.subscriptions import GroupSubscription, SubscriptionStore

    config = Config()
    store = SubscriptionStore(config, {"a"})
    group = GroupSubscription("bot:GroupMessage:123", ("a",))
    store.put(group)
    assert config["group_entries"][0]["__template_key"] == "group_subscription"
    assert SubscriptionStore(config, {"a"}).get(group.session).server_keys == ("a",)
    original = config["group_entries"]

    def fail():
        raise OSError("disk full")

    config.save_config = fail
    with pytest.raises(OSError):
        store.put(GroupSubscription(group.session, ()))
    assert config["group_entries"] == original
    assert store.get(group.session).server_keys == ("a",)


@pytest.mark.parametrize(
    "entry",
    [
        {"session": "bot:FriendMessage:123", "server_keys": ["a"]},
        {"session": "bad", "server_keys": ["a"]},
        {"session": "bot:GroupMessage:123", "server_keys": ["missing"]},
        {"session": "bot:GroupMessage:123", "server_keys": "a"},
    ],
)
def test_invalid_subscription_fails_closed(entry):
    from minecraft_multi_monitor.subscriptions import SubscriptionStore

    with pytest.raises(ValueError):
        SubscriptionStore(Config(group_entries=[entry]), {"a"})


def test_retry_is_per_group_and_does_not_repeat_success():
    from minecraft_multi_monitor.delivery import NotificationQueue

    async def run():
        queue = NotificationQueue()
        queue.enqueue("g1", "s", "changed", None, "status", now=0)
        queue.enqueue("g2", "s", "changed", None, "status", now=0)
        sent = []

        async def send(item):
            sent.append(item.session)
            return item.session != "g2" or sent.count("g2") > 1

        def resolve(session):
            return SimpleNamespace(server_keys=("s",), notify_mode="all")

        await queue.flush(send, resolve, now=0)
        assert sent.count("g1") == 1
        assert len(queue.pending) == 1
        await queue.flush(send, resolve, now=30)
        assert sent.count("g1") == 1
        assert sent.count("g2") == 2
        assert not queue.pending

    asyncio.run(run())


def test_unsubscribe_and_retry_limit_discard_pending():
    from minecraft_multi_monitor.delivery import NotificationQueue

    async def run():
        queue = NotificationQueue()
        queue.enqueue("g1", "s", "changed", None, "players", now=0)
        calls = []

        async def fail(item):
            calls.append(item)
            return False

        await queue.flush(fail, lambda session: None, now=0)
        assert not calls and not queue.pending
        queue.enqueue("g1", "s", "changed", None, "status", now=0)

        def resolve(session):
            return SimpleNamespace(server_keys=("s",), notify_mode="all")

        for now in (0, 30, 60, 90):
            await queue.flush(fail, resolve, now=now)
        assert len(calls) == 3 and not queue.pending

    asyncio.run(run())


def test_newer_event_is_not_removed_by_inflight_delivery():
    from minecraft_multi_monitor.delivery import NotificationQueue

    async def run():
        queue = NotificationQueue()
        queue.enqueue("g", "s", "old", None, "status", now=0)

        async def send(item):
            queue.enqueue("g", "s", "new", None, "status", now=1)
            return True

        await queue.flush(send, lambda _: SimpleNamespace(server_keys=("s",), notify_mode="all"), now=0)
        assert queue.pending[("g", "s")].message == "new"

    asyncio.run(run())
