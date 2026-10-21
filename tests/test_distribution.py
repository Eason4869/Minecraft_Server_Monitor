import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from minecraft_multi_monitor.config import load_display_options, load_servers_from_config
from minecraft_multi_monitor.services import notify_session
from minecraft_multi_monitor.subscriptions import SubscriptionStore

ROOT = Path(__file__).resolve().parents[1]


def test_complete_example_config_matches_schema_and_parsers():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert set(schema) == set(config)
    servers = load_servers_from_config(config, 45)
    assert [server.server_port for server in servers] == [25565, 19132]
    groups = SubscriptionStore(config, {server.key for server in servers})
    assert len(groups.groups) == 2
    assert load_display_options(config).show_server_address is False
    assert schema["group_entries"]["templates"].keys() == {"group_subscription"}


def test_notification_uses_typed_text_and_exact_session():
    async def run():
        context = SimpleNamespace(send_message=AsyncMock(return_value=True))
        session = "specific-bot:GroupMessage:123"
        assert await notify_session(context, session, "[CQ:at,qq=all]")
        assert context.send_message.call_args.args[0] == session
        assert context.send_message.call_args.args[1].chain == [("text", "[CQ:at,qq=all]")]
        context.send_message = AsyncMock(return_value=False)
        assert not await notify_session(context, session, "test")

    asyncio.run(run())
