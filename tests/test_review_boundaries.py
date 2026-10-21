import asyncio
import importlib
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from tests_support import Event, make_plugin

from minecraft_multi_monitor.clients import unavailable_snapshot
from minecraft_multi_monitor.models import DisplayOptions
from minecraft_multi_monitor.rendering import CardRenderer
from minecraft_multi_monitor.subscriptions import GroupSubscription


def bind(plugin, keys=("s", "hidden"), **options):
    group = GroupSubscription("bot:GroupMessage:123", keys, **options)
    plugin.subscriptions.put(group)
    plugin.status_client.fetch_status = AsyncMock(
        side_effect=lambda server, source: unavailable_snapshot(server)
    )
    return group


def test_partial_revoke_and_render_failure_filters_text_fallback():
    async def run():
        plugin = make_plugin()
        group = bind(plugin)

        async def render(*args):
            plugin.subscriptions.put(replace(group, server_keys=("s",)))
            raise RuntimeError("renderer unavailable")

        plugin.renderer.render_pages = render
        results = [item async for item in plugin._query(Event())]
        assert len(results) == 1
        assert "生存服" in results[0] and "隐藏服" not in results[0]
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("style", ["text", "image"])
def test_revoke_while_waiting_for_quote_suppresses_query(style):
    async def run():
        plugin = make_plugin()
        group = bind(plugin)
        plugin.display_options.show_hitokoto = True
        module = importlib.import_module(type(plugin).__module__)

        async def quote():
            plugin.subscriptions.put(replace(group, server_keys=()))
            return "quote"

        with patch.object(module, "get_hitokoto", quote):
            assert [item async for item in plugin._query(Event(), style=style)] == []
        await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["unsubscribe", "off", "status"])
def test_notification_rechecks_permission_after_failed_render(change):
    async def run():
        plugin = make_plugin()
        group = bind(plugin, ("s",), notification_style="image")
        module = importlib.import_module(type(plugin).__module__)
        changes = {
            "unsubscribe": {"server_keys": ()},
            "off": {"notify_mode": "off"},
            "status": {"notify_mode": "status"},
        }

        async def render(*args):
            plugin.subscriptions.put(replace(group, **changes[change]))
            raise RuntimeError("renderer unavailable")

        plugin.renderer.render_pages = render
        plugin.notifications.enqueue(
            group.session, "s", "players changed", unavailable_snapshot(plugin.servers[0]), "players"
        )
        sent = []

        async def send(context, session, message, image=None):
            sent.append((session, message))
            return True

        with patch.object(module, "notify_session", send):
            await plugin.notifications.flush(plugin._send_notification, plugin.subscriptions.get)
        assert not sent and not plugin.notifications.pending
        await plugin.terminate()

    asyncio.run(run())


def test_waiting_notification_rechecks_revocation_without_affecting_other_bot():
    async def run():
        plugin = make_plugin()
        group = bind(plugin, ("s",))
        other = replace(group, session="another-bot:GroupMessage:123")
        plugin.subscriptions.put(other)
        queue = plugin.notifications
        queue._semaphore = asyncio.Semaphore(0)
        for session in (group.session, other.session):
            queue.enqueue(session, "s", "changed", unavailable_snapshot(plugin.servers[0]), "status")
        sent = []

        async def send(item):
            sent.append(item.session)
            return True

        flush = asyncio.create_task(queue.flush(send, plugin.subscriptions.get))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        plugin.subscriptions.put(replace(group, server_keys=()))
        queue._semaphore.release()
        await flush
        assert sent == [other.session] and not queue.pending
        await plugin.terminate()

    asyncio.run(run())


def test_cancelling_one_render_releases_lock_for_next_caller():
    async def run():
        plugin = make_plugin()
        snapshot = unavailable_snapshot(plugin.servers[0])
        entered = asyncio.Event()
        calls = 0

        async def render(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await asyncio.Event().wait()
            return "https://renderer/second.png"

        renderer = CardRenderer(render)
        first = asyncio.create_task(renderer.render_pages([snapshot], DisplayOptions()))
        await entered.wait()
        second = asyncio.create_task(renderer.render_pages([snapshot], DisplayOptions()))
        first.cancel()
        cancelled = await asyncio.gather(first, return_exceptions=True)
        assert isinstance(cancelled[0], asyncio.CancelledError)
        assert await asyncio.wait_for(second, timeout=1) == ["https://renderer/second.png"]
        assert not renderer.lock.locked() and not renderer.tasks
        await renderer.close()
        await plugin.terminate()

    asyncio.run(run())
