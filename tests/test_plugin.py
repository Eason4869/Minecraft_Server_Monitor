import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

from tests_support import Event, make_plugin


def test_non_admin_cannot_stop_or_mutate():
    async def run():
        plugin = make_plugin()
        event = Event()
        assert "管理员" in (await anext(plugin.mc_command(event, "停止")))
        assert "管理员" in (await anext(plugin.mc_command(event, "订阅", "s")))
        assert plugin.subscriptions.get(event.unified_msg_origin) is None
        await plugin.terminate()

    asyncio.run(run())


def test_queries_are_scoped_and_admin_can_persist_subscription():
    async def run():
        plugin = make_plugin()
        event = Event()
        assert "未绑定" in (await anext(plugin.mc_command(event, "查询")))
        admin = Event(admin=True)
        assert "已订阅" in (await anext(plugin.mc_command(admin, "订阅", "s")))
        assert plugin.config.saved
        result = await anext(plugin.mc_command(event, "列表"))
        assert "生存服" in result and "隐藏服" not in result
        other = Event(umo="other:GroupMessage:123")
        assert "未绑定" in (await anext(plugin.mc_command(other, "查询")))
        private = Event(umo="bot:FriendMessage:user", group="")
        assert "私聊" in (await anext(plugin.mc_command(private, "查询")))
        await plugin.terminate()

    asyncio.run(run())


def test_admin_query_in_unbound_group_does_not_publish_global_catalog():
    async def run():
        plugin = make_plugin()
        admin = Event(admin=True)
        assert "未绑定" in (await anext(plugin.mc_command(admin, "查询")))
        await plugin.terminate()

    asyncio.run(run())


def test_disabling_subscribed_server_preserves_other_servers_and_subscription():
    async def run():
        plugin = make_plugin()
        from minecraft_multi_monitor.subscriptions import GroupSubscription

        plugin.subscriptions.put(
            GroupSubscription("bot:GroupMessage:123", ("s", "hidden"), default_server="s")
        )
        plugin.config["server_entries"][0]["enabled"] = False
        restarted = type(plugin)(plugin.context, plugin.config)
        assert [server.key for server in restarted.servers] == ["hidden"]
        assert restarted.subscriptions.get("bot:GroupMessage:123").server_keys == ("s", "hidden")
        restarted.status_client.fetch_status = AsyncMock(return_value=None)
        result = await anext(restarted._query(Event(), style="text"))
        assert "隐藏服" in result and "生存服" not in result
        await plugin.terminate()
        await restarted.terminate()

    asyncio.run(run())


def test_unload_cancels_delayed_start_and_closes_client():
    async def run():
        plugin = make_plugin(auto=True)
        plugin.status_client.close = AsyncMock()
        await plugin.initialize()
        delayed = plugin.startup_task
        await plugin.terminate()
        await asyncio.sleep(0)
        assert delayed.done()
        assert plugin.task is None or plugin.task.done()
        plugin.status_client.close.assert_awaited_once()

    asyncio.run(run())


def test_request_coalescing_and_independent_server_concurrency():
    async def run():
        plugin = make_plugin()
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def fetch(server, source):
            calls.append(server.key)
            entered.set()
            await release.wait()
            from minecraft_multi_monitor.clients import unavailable_snapshot

            return replace(unavailable_snapshot(server), status="online")

        plugin.status_client.fetch_status = fetch
        first = asyncio.create_task(plugin._fetch_server_data(plugin.servers[0], "test"))
        await entered.wait()
        second = asyncio.create_task(plugin._fetch_server_data(plugin.servers[0], "test"))
        third = asyncio.create_task(plugin._fetch_server_data(plugin.servers[1], "test"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second, third)
        assert sorted(calls) == ["hidden", "s"]
        await plugin.terminate()

    asyncio.run(run())


def test_render_failure_returns_text_and_never_leaks_unbound_server():
    async def run():
        plugin = make_plugin()
        plugin.config["display_options"] = {}
        from minecraft_multi_monitor.subscriptions import GroupSubscription

        plugin.subscriptions.put(GroupSubscription("bot:GroupMessage:123", ("s",)))
        from minecraft_multi_monitor.clients import unavailable_snapshot

        plugin.status_client.fetch_status = AsyncMock(
            side_effect=lambda server, source: unavailable_snapshot(server)
        )
        plugin.html_render = AsyncMock(side_effect=RuntimeError("renderer offline"))
        results = [result async for result in plugin.mc_command(Event(), "查询")]
        assert results and all("隐藏服" not in result for result in results)
        assert "查询异常" in results[0]
        await plugin.terminate()

    asyncio.run(run())


def test_failed_server_does_not_kill_monitor_and_offline_is_confirmed():
    async def run():
        plugin = make_plugin()
        from minecraft_multi_monitor.clients import unavailable_snapshot

        s = plugin.servers[0]
        online = replace(unavailable_snapshot(s), status="online", online=2)
        offline = replace(online, status="offline", online=0)
        plugin.status_client.fetch_status = AsyncMock(return_value=online)
        await plugin._check_server(s)
        plugin.cache.clear()
        plugin.status_client.fetch_status = AsyncMock(return_value=offline)
        await plugin._check_server(s)
        assert plugin.server_states[s.key].last_status == "online"
        plugin.cache.clear()
        await plugin._check_server(s)
        assert plugin.server_states[s.key].last_status == "offline"
        plugin.cache.clear()
        plugin.status_client.fetch_status = AsyncMock(side_effect=RuntimeError("bad provider"))
        await plugin._check_server(s)
        await plugin._check_server(s)
        assert plugin.server_states[s.key].last_status == "unknown"
        await plugin.terminate()

    asyncio.run(run())
