import asyncio
import importlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tests_support import Event, make_plugin

from minecraft_multi_monitor.clients import unavailable_snapshot
from minecraft_multi_monitor.delivery import NotificationQueue
from minecraft_multi_monitor.subscriptions import GroupSubscription


def bind(plugin, keys=("s",), **options):
    group = GroupSubscription("bot:GroupMessage:123", keys, **options)
    plugin.subscriptions.put(group)
    plugin.status_client.fetch_status = AsyncMock(
        side_effect=lambda server, source: unavailable_snapshot(server)
    )
    return group


@pytest.mark.parametrize("style", ["text", "image"])
def test_revoke_during_fetch_suppresses_query(style):
    async def run():
        plugin = make_plugin()
        group = bind(plugin)

        async def fetch(server, source):
            plugin.subscriptions.put(replace(group, server_keys=()))
            return unavailable_snapshot(server)

        plugin.status_client.fetch_status = fetch
        plugin.renderer.render_pages = AsyncMock(return_value=["https://renderer/old.png"])
        results = [item async for item in plugin._query(Event(), style=style)]
        assert results == []
        plugin.renderer.render_pages.assert_not_awaited()
        await plugin.terminate()

    asyncio.run(run())


def test_revoke_during_render_suppresses_image_and_keeps_other_group():
    async def run():
        plugin = make_plugin()
        group = bind(plugin)
        other = GroupSubscription("another-bot:GroupMessage:123", ("s",))
        plugin.subscriptions.put(other)

        async def render(*args):
            plugin.subscriptions.put(replace(group, server_keys=()))
            return ["https://renderer/old.png"]

        plugin.renderer.render_pages = render
        assert [item async for item in plugin._query(Event())] == []
        assert plugin.subscriptions.get(other.session).server_keys == ("s",)
        await plugin.terminate()

    asyncio.run(run())


def test_revoke_between_image_pages_suppresses_remaining_pages():
    async def run():
        plugin = make_plugin()
        first = plugin.servers[0]
        servers = [replace(first, key=f"s{index}") for index in range(5)]
        plugin.servers = servers
        plugin.server_locks = {server.key: asyncio.Lock() for server in servers}
        plugin.subscriptions.server_keys = {server.key for server in servers}
        group = bind(plugin, tuple(server.key for server in servers))
        plugin.renderer.render_pages = AsyncMock(
            return_value=["https://renderer/1.png", "https://renderer/2.png"]
        )
        query = plugin._query(Event())
        assert await anext(query) == "IMAGE:https://renderer/1.png"
        plugin.subscriptions.put(replace(group, server_keys=group.server_keys[:4]))
        assert [item async for item in query] == []
        await plugin.terminate()

    asyncio.run(run())


def test_disabled_server_can_be_unsubscribed_and_default_cleared():
    async def run():
        plugin = make_plugin()
        bind(plugin, default_server="s")
        plugin.config["server_entries"][0]["enabled"] = False
        restarted = type(plugin)(plugin.context, plugin.config)
        reply = await anext(restarted.mc_command(Event(admin=True), "退订", "s"))
        assert "已退订" in reply
        group = restarted.subscriptions.get("bot:GroupMessage:123")
        assert group.server_keys == () and group.default_server == ""
        assert restarted.config.saved
        await plugin.terminate()
        await restarted.terminate()

    asyncio.run(run())


def test_backoff_starts_when_slow_failure_finishes():
    async def run():
        delivery = importlib.import_module(NotificationQueue.__module__)
        clock = [0.0]
        with patch.object(delivery, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            queue = NotificationQueue()
            queue.enqueue("g", "s", "changed", None, "status")
            starts = []

            async def fail(item):
                starts.append(clock[0])
                clock[0] += 20
                return False

            def resolve(_):
                return SimpleNamespace(server_keys=("s",), notify_mode="all")

            await queue.flush(fail, resolve)
            assert queue.pending[("g", "s")].next_attempt_at == 25
            clock[0] = 21
            await queue.flush(fail, resolve)
            assert starts == [0]
            clock[0] = 25
            await queue.flush(fail, resolve)
            assert starts == [0, 25]
            assert queue.pending[("g", "s")].next_attempt_at == 60

    asyncio.run(run())


def test_expiry_is_rechecked_after_waiting_for_send_slot():
    async def run():
        delivery = importlib.import_module(NotificationQueue.__module__)
        clock = [0.0]
        with patch.object(delivery, "time", SimpleNamespace(monotonic=lambda: clock[0])):
            queue = NotificationQueue()
            queue._semaphore = asyncio.Semaphore(0)
            queue.enqueue("g", "s", "changed", None, "status")
            send = AsyncMock(return_value=True)
            flush = asyncio.create_task(
                queue.flush(send, lambda _: SimpleNamespace(server_keys=("s",), notify_mode="all"))
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            clock[0] = 301
            queue._semaphore.release()
            await flush
            send.assert_not_awaited()
            assert not queue.pending

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["slow_image", "locked_renderer"])
def test_image_budget_always_leaves_time_for_text_fallback(mode):
    async def run():
        plugin = make_plugin()
        group = bind(plugin, notification_style="image")
        module = importlib.import_module(type(plugin).__module__)
        real_wait_for = asyncio.wait_for
        attempts = []

        async def render(*args):
            if mode == "locked_renderer":
                await asyncio.Event().wait()
            await asyncio.sleep(0.18)
            return ["https://renderer/card.png"]

        async def send(context, session, message, image=None):
            attempts.append("image" if image else "text")
            if image:
                await asyncio.sleep(0.14)
                return False
            return True

        async def scaled_wait_for(awaitable, timeout):
            return await real_wait_for(awaitable, timeout=timeout / 100)

        plugin.renderer.render_pages = render
        plugin.notifications.enqueue(
            group.session, "s", "changed", unavailable_snapshot(plugin.servers[0]), "status"
        )
        with patch.object(module, "notify_session", send), patch.object(asyncio, "wait_for", scaled_wait_for):
            await plugin.notifications.flush(plugin._send_notification, plugin.subscriptions.get)
        assert attempts[-1:] == ["text"]
        assert not plugin.notifications.pending
        await plugin.terminate()

    asyncio.run(run())


def test_unload_cancels_active_query_render_and_prevents_cache_repopulation():
    async def run():
        plugin = make_plugin()
        bind(plugin)
        entered = asyncio.Event()
        release = asyncio.Event()
        cancelled = asyncio.Event()

        async def render(*args, **kwargs):
            entered.set()
            try:
                await release.wait()
                return "https://renderer/after-stop.png"
            except asyncio.CancelledError:
                cancelled.set()
                raise

        plugin.html_render = render

        async def collect():
            return [item async for item in plugin._query(Event())]

        query = asyncio.create_task(collect())
        await entered.wait()
        await plugin.terminate()
        was_cancelled = cancelled.is_set()
        release.set()
        result = await asyncio.gather(query, return_exceptions=True)
        assert was_cancelled
        assert not plugin.renderer.cache
        assert isinstance(result[0], asyncio.CancelledError) or result[0] == []
        assert [item async for item in plugin._query(Event())] == []

    asyncio.run(run())


def test_renderer_close_cancels_lock_waiters_and_rejects_future_calls():
    from minecraft_multi_monitor.models import DisplayOptions
    from minecraft_multi_monitor.rendering import CardRenderer

    async def run():
        plugin = make_plugin()
        render = AsyncMock(return_value="https://renderer/card.png")
        renderer = CardRenderer(render)
        await renderer.lock.acquire()
        waiting = asyncio.create_task(
            renderer.render_pages([unavailable_snapshot(plugin.servers[0])], DisplayOptions())
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await renderer.close()
        result = await asyncio.gather(waiting, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        render.assert_not_awaited()
        assert not renderer.cache
        with pytest.raises(RuntimeError, match="关闭"):
            await renderer.render_pages([], DisplayOptions())
        renderer.lock.release()
        await plugin.terminate()

    asyncio.run(run())


def test_card_render_requests_small_initial_viewport_for_content_height():
    from minecraft_multi_monitor.models import DisplayOptions
    from minecraft_multi_monitor.rendering import CardRenderer

    async def run():
        plugin = make_plugin()
        render = AsyncMock(return_value="https://renderer/card.png")
        renderer = CardRenderer(render)
        await renderer.render_pages([unavailable_snapshot(plugin.servers[0])], DisplayOptions())
        options = render.call_args.kwargs["options"]
        assert options.get("viewport_height") == 1
        assert options.get("viewport_width") == 800
        assert options["full_page"] is True
        await plugin.terminate()

    asyncio.run(run())
