import asyncio
import importlib
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from aiohttp import web
from tests_support import make_plugin

from minecraft_multi_monitor.clients import MCStatusClient, build_status_client, unavailable_snapshot
from minecraft_multi_monitor.models import ServerConfig
from minecraft_multi_monitor.subscriptions import GroupSubscription


@asynccontextmanager
async def upstream(handler):
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_recovers_with_diagnostic_log(status, caplog):
    async def run():
        hits = []

        async def handler(request):
            hits.append(request.path)
            if len(hits) == 1:
                return web.Response(status=status, headers={"Retry-After": "0"})
            return web.json_response({"online": True, "players": {"online": 3}})

        client = MCStatusClient()
        async with upstream(handler) as url:
            client.BASE_URL = url
            try:
                result = await client.fetch_status(
                    ServerConfig("s", True, "Test", "example.com", 25565, "java", 45), "monitor"
                )
                assert result is not None and result.status == "online" and result.online == 3
                assert len(hits) == 2
                assert all(
                    word in caplog.text for word in ("mcstatus.io", str(status), "attempt=1/2", "elapsed=")
                )
            finally:
                await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure,expected_hits", [("503", 2), ("400", 1), ("json", 1), ("schema", 1), ("limit", 1)]
)
def test_failed_primary_is_bounded_and_falls_back(failure, expected_hits, caplog):
    async def run():
        hits = []

        async def handler(request):
            hits.append(request.path)
            if request.path.startswith("/backup"):
                return web.json_response({"online": True})
            if failure == "json":
                return web.Response(text="<html>unavailable</html>")
            if failure == "schema":
                return web.json_response({"error": "unavailable"})
            if failure == "limit":
                return web.Response(status=429, headers={"Retry-After": "60"})
            return web.Response(status=int(failure))

        client = build_status_client(True)
        async with upstream(handler) as url:
            client.primary.BASE_URL = url + "/primary"
            client.secondary.BASE_URL = url + "/backup"
            try:
                result = await client.fetch_status(
                    ServerConfig("s", True, "Test", "example.com", 25565, "java", 45), "monitor"
                )
                assert result.status == "online" and result.source == "mcstatus.io"
                assert hits.count("/primary") == expected_hits
                assert len(hits) == expected_hits + 1
                assert "MineBBS" in caplog.text and "切换备用源" in caplog.text
            finally:
                await client.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["rejected", "exception", "render"])
def test_image_retry_recovers_without_sending_text(failure, caplog):
    async def run():
        plugin = make_plugin()
        group = GroupSubscription("bot:GroupMessage:123", ("s",), notification_style="image")
        plugin.subscriptions.put(group)
        sends = []
        renders = []

        async def render(*args, **kwargs):
            renders.append(1)
            if failure == "render" and len(renders) == 1:
                raise RuntimeError("renderer unavailable")
            return "https://renderer/card.png"

        async def send(session, chain):
            kind = "image" if any(part[0] == "image" for part in chain.chain) else "text"
            sends.append((session, kind))
            if len(sends) == 1 and failure != "render":
                if failure == "exception":
                    raise OSError("adapter unavailable")
                return False
            return True

        plugin.html_render = render
        plugin.context.send_message = send
        queue = plugin.notifications
        queue.enqueue(group.session, "s", "changed", unavailable_snapshot(plugin.servers[0]), "status", now=0)
        try:
            await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=0)
            assert len(queue.pending) == 1
            assert all(kind == "image" for _, kind in sends)
            assert "图片" in caplog.text and "attempt=1/3" in caplog.text and "elapsed=" in caplog.text
            await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=5)
            assert not queue.pending and sends[-1] == (group.session, "image")
            assert all(kind == "image" for _, kind in sends)
        finally:
            await plugin.terminate()

    asyncio.run(run())


@pytest.mark.parametrize("text_success", [True, False])
def test_image_failure_falls_back_only_on_final_attempt(text_success, caplog):
    async def run():
        plugin = make_plugin()
        group = GroupSubscription("bot:GroupMessage:123", ("s",), notification_style="image")
        plugin.subscriptions.put(group)
        sends = []

        async def render(*args, **kwargs):
            return "https://renderer/card.png"

        async def send(session, chain):
            kind = "image" if any(part[0] == "image" for part in chain.chain) else "text"
            sends.append(kind)
            return text_success if kind == "text" else False

        plugin.html_render = render
        plugin.context.send_message = send
        queue = plugin.notifications
        queue.enqueue(group.session, "s", "changed", unavailable_snapshot(plugin.servers[0]), "status", now=0)
        try:
            for now in (0, 5):
                await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=now)
                assert queue.pending and "text" not in sends
            await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=20)
            await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=60)
            assert sends == ["image", "image", "image", "text"] and not queue.pending
            assert "通知图片降级" in caplog.text and "attempt=3/3" in caplog.text
            assert "返回 False" in caplog.text
        finally:
            await plugin.terminate()

    asyncio.run(run())


def test_image_retry_uses_current_group_style_and_permissions():
    async def run():
        plugin = make_plugin()
        group = GroupSubscription("bot:GroupMessage:123", ("s",), notification_style="image")
        other = replace(group, session="other-bot:GroupMessage:123")
        sends = []

        async def render(*args, **kwargs):
            raise RuntimeError("renderer unavailable")

        async def send(session, chain):
            sends.append(session)
            return True

        plugin.html_render = render
        plugin.context.send_message = send
        queue = plugin.notifications
        for current in (group, other):
            plugin.subscriptions.put(current)
            queue.enqueue(
                current.session, "s", "changed", unavailable_snapshot(plugin.servers[0]), "status", now=0
            )
        try:
            await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=0)
            assert not sends and len(queue.pending) == 2
            plugin.subscriptions.put(replace(group, server_keys=()))
            plugin.subscriptions.put(replace(other, notification_style="text"))
            await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=5)
            assert sends == [other.session] and not queue.pending
        finally:
            await plugin.terminate()

    asyncio.run(run())


def test_render_lock_timeout_has_stage_and_cleans_up(monkeypatch):
    async def run():
        plugin = make_plugin()
        module = importlib.import_module(type(plugin.renderer).__module__)
        monkeypatch.setattr(module, "LOCK_TIMEOUT", 0.02, raising=False)
        await plugin.renderer.lock.acquire()
        try:
            with pytest.raises(Exception, match="stage=lock"):
                await asyncio.wait_for(
                    plugin.renderer.render_pages(
                        [unavailable_snapshot(plugin.servers[0])], plugin.display_options
                    ),
                    timeout=0.3,
                )
            assert not plugin.renderer.tasks
        finally:
            plugin.renderer.lock.release()
            await plugin.terminate()

    asyncio.run(run())


def test_rejected_image_is_regenerated_before_retry():
    async def run():
        plugin = make_plugin()
        group = GroupSubscription("bot:GroupMessage:123", ("s",), notification_style="image")
        plugin.subscriptions.put(group)
        paths = iter(("https://renderer/expired.png", "https://renderer/fresh.png"))
        sent = []

        async def render(*args, **kwargs):
            return next(paths)

        async def send(session, chain):
            path = next((value for kind, value in chain.chain if kind == "image"), "text")
            sent.append(path)
            return path.endswith("fresh.png")

        plugin.html_render = render
        plugin.context.send_message = send
        queue = plugin.notifications
        queue.enqueue(group.session, "s", "changed", unavailable_snapshot(plugin.servers[0]), "status", now=0)
        try:
            for now in (0, 5):
                await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=now)
            assert sent == ["https://renderer/expired.png", "https://renderer/fresh.png"]
            assert not queue.pending
        finally:
            await plugin.terminate()

    asyncio.run(run())


def test_http_timeout_recovers_without_unknown_snapshot(monkeypatch, caplog):
    async def run():
        module = importlib.import_module(MCStatusClient.__module__)
        monkeypatch.setattr(module, "HTTP_TIMEOUT", 0.04)
        monkeypatch.setattr(module, "QUERY_BUDGET", 0.2)
        monkeypatch.setattr(module, "RETRY_DELAY", 0.01)
        hits = []

        async def handler(request):
            hits.append(1)
            if len(hits) == 1:
                await asyncio.sleep(0.08)
            return web.json_response({"online": True})

        client = MCStatusClient()
        async with upstream(handler) as url:
            client.BASE_URL = url
            try:
                result = await client.fetch_status(
                    ServerConfig("s", True, "Test", "example.com", 25565, "java", 45), "monitor"
                )
                assert result.status == "online" and len(hits) == 2
                assert "TimeoutError" in caplog.text and "elapsed=" in caplog.text
            finally:
                await client.close()

    asyncio.run(run())


def test_unload_cancels_http_backoff_before_second_request(monkeypatch):
    async def run():
        plugin = make_plugin()
        client = build_status_client(False)
        plugin.status_client = client
        module = importlib.import_module(MCStatusClient.__module__)
        monkeypatch.setattr(module, "RETRY_DELAY", 1)
        retrying = asyncio.Event()
        original_warning = module.logger.warning

        def warning(message):
            original_warning(message)
            if "retry=True" in message:
                retrying.set()

        monkeypatch.setattr(module.logger, "warning", warning)
        hits = []

        async def handler(request):
            hits.append(1)
            return web.Response(status=503)

        async with upstream(handler) as url:
            client.primary.BASE_URL = url
            client.secondary.BASE_URL = url
            request = asyncio.create_task(plugin._fetch_server_data(plugin.servers[0], "monitor"))
            try:
                await asyncio.wait_for(retrying.wait(), timeout=1)
                await plugin.terminate()
                result = await asyncio.gather(request, return_exceptions=True)
                assert isinstance(result[0], asyncio.CancelledError)
                assert hits == [1] and client.primary.session.closed
                assert client.secondary.session is None and not plugin.inflight_requests
            finally:
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
                await plugin.terminate()

    asyncio.run(run())


def test_image_send_timeout_is_logged_and_retried(monkeypatch, caplog):
    async def run():
        plugin = make_plugin()
        group = GroupSubscription("bot:GroupMessage:123", ("s",), notification_style="image")
        plugin.subscriptions.put(group)
        sends = []
        cancelled = asyncio.Event()
        real_wait_for = asyncio.wait_for

        async def scaled_wait_for(awaitable, timeout):
            return await real_wait_for(awaitable, timeout=timeout / 100)

        monkeypatch.setattr(asyncio, "wait_for", scaled_wait_for)

        async def render(*args, **kwargs):
            return "https://renderer/card.png"

        async def send(session, chain):
            sends.append([kind for kind, _ in chain.chain])
            if len(sends) == 1:
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return True

        plugin.html_render = render
        plugin.context.send_message = send
        queue = plugin.notifications
        queue.enqueue(group.session, "s", "changed", unavailable_snapshot(plugin.servers[0]), "status", now=0)
        try:
            await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=0)
            assert queue.pending and cancelled.is_set()
            assert "stage=image_send" in caplog.text and "TimeoutError" in caplog.text
            await queue.flush(plugin._send_notification, plugin.subscriptions.get, now=5)
            assert not queue.pending and sends == [["text", "image"], ["text", "image"]]
        finally:
            await plugin.terminate()

    asyncio.run(run())


def test_monitor_counts_failed_rounds_not_http_attempts_and_recovers(monkeypatch, caplog):
    async def run():
        module = importlib.import_module(MCStatusClient.__module__)
        monkeypatch.setattr(module, "RETRY_DELAY", 0.001)
        plugin = make_plugin()
        plugin.status_client = build_status_client(True)
        group = GroupSubscription("bot:GroupMessage:123", ("s",), notification_style="image")
        plugin.subscriptions.put(group)
        healthy = False
        hits = []

        async def handler(request):
            hits.append(request.path)
            if healthy:
                return web.json_response({"status": "online", "players": {"online": 2}})
            return web.Response(status=503)

        async with upstream(handler) as url:
            plugin.status_client.primary.BASE_URL = url + "/primary"
            plugin.status_client.secondary.BASE_URL = url + "/backup"
            try:
                await plugin._check_server(plugin.servers[0])
                assert len(hits) == 4 and plugin.failures["s"] == 1
                assert not plugin.notifications.pending
                await plugin._check_server(plugin.servers[0])
                item = plugin.notifications.pending[(group.session, "s")]
                assert len(hits) == 8 and plugin.failures["s"] == 2
                assert item.snapshot.status == "unknown"
                assert "stage=complete" in caplog.text and "查询源不可用" in caplog.text
                healthy = True
                await plugin._check_server(plugin.servers[0])
                item = plugin.notifications.pending[(group.session, "s")]
                assert len(hits) == 9 and plugin.failures["s"] == 0
                assert item.snapshot.status == "online" and item.snapshot.online == 2
            finally:
                await plugin.terminate()

    asyncio.run(run())
