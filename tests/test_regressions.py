from dataclasses import replace

import pytest

from minecraft_multi_monitor.clients import MCStatusClient
from minecraft_multi_monitor.config import build_initial_states, load_servers_from_config
from minecraft_multi_monitor.models import DisplayOptions, ServerConfig, ServerSnapshot, ServerState
from minecraft_multi_monitor.monitoring import detect_changes, format_server_info


@pytest.fixture
def server():
    return ServerConfig("survival", True, "生存服", "example.com", 25565, "java", 45)


@pytest.fixture
def snapshot():
    return ServerSnapshot(
        "survival",
        "生存服",
        "example.com",
        25565,
        "java",
        "online",
        "Hello",
        "1.21",
        "1",
        2,
        20,
        ["Alice", "Bob"],
        "",
        "",
        "now",
        True,
    )


def test_duplicate_keys_are_rejected():
    with pytest.raises(ValueError, match="重复"):
        load_servers_from_config(
            {
                "server_entries": [
                    {"key": "same", "server_ip": "a.example"},
                    {"key": "same", "server_ip": "b.example"},
                ]
            },
            45,
        )


@pytest.mark.parametrize("port", [-1, 0, 65536, "bad"])
def test_invalid_ports_are_rejected(port):
    with pytest.raises(ValueError, match="端口"):
        load_servers_from_config({"server_entries": [{"server_ip": "a.example", "server_port": port}]}, 45)


def test_missing_bedrock_port_uses_19132():
    servers = load_servers_from_config(
        {"server_entries": [{"server_ip": "a.example", "server_type": "bedrock"}]}, 45
    )
    assert servers[0].server_port == 19132
    assert len(build_initial_states(servers)) == 1


def test_same_count_different_names_is_reported_as_sample_change(server, snapshot):
    state = ServerState()
    detect_changes(server, state, snapshot)
    changed, text = detect_changes(server, state, replace(snapshot, players=["Alice", "Carol"]))
    assert changed
    assert "样本" in text
    assert "加入" not in text


def test_offline_does_not_claim_players_left(server, snapshot):
    state = ServerState()
    detect_changes(server, state, snapshot)
    changed, text = detect_changes(server, state, replace(snapshot, status="offline", online=0, players=[]))
    assert changed and "离线" in text
    assert "离开" not in text


def test_unavailable_names_do_not_mean_empty(snapshot):
    text = format_server_info(replace(snapshot, players=[]), DisplayOptions(show_player_list=True))
    assert "未提供" in text
    assert "当前无玩家在线" not in text


def test_java_version_uses_documented_field(server):
    result = MCStatusClient()._parse_response(server, {"online": True, "version": {"name_clean": "1.21.4"}})
    assert result.version == "1.21.4"


def test_unknown_is_not_offline(snapshot):
    text = format_server_info(replace(snapshot, status="unknown", error="查询源不可用"), DisplayOptions())
    assert "查询异常" in text


def test_implicit_keys_do_not_transfer_authorization_when_reordered():
    entries = [
        {"server_name": "生存服", "server_ip": "public.example"},
        {"server_name": "隐藏服", "server_ip": "private.example"},
    ]
    before = load_servers_from_config({"server_entries": entries}, 45)
    after = load_servers_from_config({"server_entries": list(reversed(entries))}, 45)
    authorized = before[0].key
    assert next(server for server in after if server.key == authorized).server_ip == "public.example"
