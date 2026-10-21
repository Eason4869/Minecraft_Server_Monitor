import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tests_support import make_plugin


def test_queued_fetch_does_not_open_session_after_terminate():
    async def run():
        plugin = make_plugin()
        plugin.fetch_semaphore = asyncio.Semaphore(0)
        request = asyncio.create_task(plugin._fetch_server_data(plugin.servers[0], "test"))
        await asyncio.sleep(0)
        await plugin.terminate()
        plugin.fetch_semaphore.release()
        assert await request is None
        plugin.status_client.fetch_status.assert_not_awaited()

    asyncio.run(run())


def test_active_http_request_is_cancelled_before_client_close():
    async def run():
        plugin = make_plugin()
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def fetch(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def close():
            assert cancelled.is_set()

        plugin.status_client.fetch_status = fetch
        plugin.status_client.close = close
        request = asyncio.create_task(plugin._fetch_server_data(plugin.servers[0], "test"))
        await entered.wait()
        await plugin.terminate()
        await asyncio.gather(request, return_exceptions=True)
        assert cancelled.is_set()

    asyncio.run(run())


def test_delivery_rechecks_subscription_after_waiting_for_slot():
    from minecraft_multi_monitor.delivery import NotificationQueue

    async def run():
        queue = NotificationQueue()
        queue._semaphore = asyncio.Semaphore(0)
        queue.enqueue("g", "s", "changed", None, "status", now=0)
        group = SimpleNamespace(server_keys=("s",), notify_mode="all")
        send = AsyncMock(return_value=True)
        flush = asyncio.create_task(queue.flush(send, lambda _: group, now=0))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        group.server_keys = ()
        queue._semaphore.release()
        await flush
        send.assert_not_awaited()

    asyncio.run(run())


def test_unsubscribe_during_render_prevents_send():
    from minecraft_multi_monitor.clients import unavailable_snapshot
    from minecraft_multi_monitor.delivery import PendingNotification
    from minecraft_multi_monitor.subscriptions import GroupSubscription

    async def run():
        plugin = make_plugin()
        session = "bot:GroupMessage:123"
        group = GroupSubscription(session, ("s",), notification_style="image")
        plugin.subscriptions.put(group)

        async def render(*args):
            plugin.subscriptions.put(replace(group, server_keys=()))
            return ["https://renderer/test.png"]

        plugin.renderer.render_pages = render
        plugin.context.send_message = AsyncMock(return_value=True)
        item = PendingNotification(
            session, "s", "changed", unavailable_snapshot(plugin.servers[0]), "status", 300, 0
        )
        await plugin._send_notification(item)
        plugin.context.send_message.assert_not_awaited()
        await plugin.terminate()

    asyncio.run(run())


def test_stop_requested_during_reset_remains_stopped():
    from tests_support import Event

    async def run():
        plugin = make_plugin()
        cancelling = asyncio.Event()
        release = asyncio.Event()

        async def old_monitor():
            try:
                await asyncio.Event().wait()
            finally:
                cancelling.set()
                await release.wait()

        plugin.task = asyncio.create_task(old_monitor())
        await asyncio.sleep(0)
        reset = asyncio.create_task(anext(plugin.reset_monitor(Event(admin=True))))
        await cancelling.wait()
        stop = asyncio.create_task(anext(plugin.stop_server_monitor_task(Event(admin=True))))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(reset, stop)
        assert plugin.task is None or plugin.task.done()
        await plugin.terminate()

    asyncio.run(run())
