import asyncio

import pytest
from tests_support import Event, make_plugin


@pytest.mark.parametrize("action", ["启动", "停止", "重置"])
def test_mc_global_control_requires_astrbot_admin(action):
    async def run():
        plugin = make_plugin()
        plugin.cache["sentinel"] = (0, None)
        reply = await anext(plugin.mc_command(Event(), action))
        assert "管理员" in reply
        assert plugin.task is None and plugin.delivery_task is None
        assert "sentinel" in plugin.cache
        await plugin.terminate()

    asyncio.run(run())


def test_admin_controls_full_lifecycle_through_mc_in_private_session():
    async def run():
        plugin = make_plugin()
        event = Event(admin=True, umo="bot:FriendMessage:admin", group="")
        reply = await anext(plugin.mc_command(event, "启动"))
        assert "已启动" in reply
        assert plugin.task and plugin.delivery_task
        first = plugin.task
        plugin.cache["sentinel"] = (0, None)
        reply = await anext(plugin.mc_command(event, "重置"))
        assert "已重置" in reply
        assert first.done() and plugin.task is not first
        assert "sentinel" not in plugin.cache
        reply = await anext(plugin.mc_command(event, "停止"))
        assert "已停止" in reply
        assert plugin.task is None and plugin.delivery_task is None and plugin.startup_task is None
        await anext(plugin.mc_command(event, "重置"))
        assert plugin.task is None and plugin.delivery_task is None
        await plugin.terminate()

    asyncio.run(run())


def test_mc_start_without_servers_does_not_create_tasks():
    async def run():
        plugin = make_plugin()
        plugin.servers = []
        reply = await anext(plugin.mc_command(Event(admin=True), "启动"))
        assert "server_entries" in reply
        assert plugin.task is None and plugin.delivery_task is None
        await plugin.terminate()

    asyncio.run(run())


def test_framework_only_registers_mc_command_entry():
    async def run():
        plugin = make_plugin()
        entries = {
            function.registered_command
            for function in vars(type(plugin)).values()
            if hasattr(function, "registered_command")
        }
        assert entries == {"mc"}
        await plugin.terminate()

    asyncio.run(run())
