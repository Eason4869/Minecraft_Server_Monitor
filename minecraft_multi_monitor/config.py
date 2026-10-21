import hashlib

from astrbot.api import AstrBotConfig, logger

from .models import DisplayOptions, PluginSettings, ServerConfig, ServerState

DISPLAY_DEFAULTS = DisplayOptions()


def safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def safe_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default

    value_str = str(value).strip().lower()
    if value_str in {"true", "1", "yes", "on"}:
        return True
    if value_str in {"false", "0", "no", "off"}:
        return False
    return default


def parse_target_group(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value if value.isdigit() else None


def normalize_server_type(server_type: str) -> str:
    normalized = str(server_type or "java").strip().lower()
    return normalized if normalized in {"bedrock", "java"} else "java"


def load_display_options(config: AstrBotConfig) -> DisplayOptions:
    raw_options = config.get("display_options", {})
    if not isinstance(raw_options, dict):
        logger.warning("配置项 display_options 不是对象，已使用默认展示设置。")
        raw_options = {}

    return DisplayOptions(
        show_server_name=safe_bool(raw_options.get("show_server_name"), DISPLAY_DEFAULTS.show_server_name),
        show_server_address=safe_bool(
            raw_options.get("show_server_address"), DISPLAY_DEFAULTS.show_server_address
        ),
        show_server_type=safe_bool(raw_options.get("show_server_type"), DISPLAY_DEFAULTS.show_server_type),
        show_game_version=safe_bool(raw_options.get("show_game_version"), DISPLAY_DEFAULTS.show_game_version),
        show_online_players=safe_bool(
            raw_options.get("show_online_players"), DISPLAY_DEFAULTS.show_online_players
        ),
        show_motd=safe_bool(raw_options.get("show_motd"), DISPLAY_DEFAULTS.show_motd),
        show_player_list=safe_bool(raw_options.get("show_player_list"), DISPLAY_DEFAULTS.show_player_list),
        show_update_time=safe_bool(raw_options.get("show_update_time"), DISPLAY_DEFAULTS.show_update_time),
        show_hitokoto=safe_bool(raw_options.get("show_hitokoto"), DISPLAY_DEFAULTS.show_hitokoto),
    )


def load_settings(config: AstrBotConfig) -> PluginSettings:
    global_check_interval = safe_int(config.get("check_interval", 45), 45)
    if global_check_interval < 5:
        global_check_interval = 5

    return PluginSettings(
        target_group=parse_target_group(config.get("target_group")),
        enable_auto_monitor=safe_bool(config.get("enable_auto_monitor", False), False),
        global_check_interval=global_check_interval,
        use_mcbbs_api=safe_bool(config.get("mcbbs_api", True), True),
        display_options=load_display_options(config),
    )


def resolve_check_interval(value, global_check_interval: int) -> int:
    interval = safe_int(value, global_check_interval)
    if interval <= 0:
        return global_check_interval
    return max(5, interval)


def load_servers_from_config(
    config: AstrBotConfig, global_check_interval: int, *, include_disabled=False
) -> list[ServerConfig]:
    raw_servers = config.get("server_entries")
    if raw_servers is None:
        raw_servers = config.get("servers", [])

    if not isinstance(raw_servers, list):
        logger.warning("配置项 server_entries 不是列表，已忽略。")
        raw_servers = []

    servers: list[ServerConfig] = []
    for index, item in enumerate(raw_servers, start=1):
        if not isinstance(item, dict):
            logger.warning(f"server_entries 第 {index} 项不是对象，已跳过。")
            continue

        enabled = safe_bool(item.get("enabled", True), True)
        if not enabled and not include_disabled:
            continue

        raw_key = str(item.get("key", "")).strip()
        raw_name = str(item.get("server_name", "")).strip()
        default_name = raw_key or f"服务器{index}"
        server_name = raw_name or default_name
        server_ip = str(item.get("server_ip", "")).strip()
        server_type = str(item.get("server_type") or "java").strip().lower()
        if server_type not in {"java", "bedrock"}:
            raise ValueError(f"{server_name} 服务器类型必须是 java 或 bedrock")
        default_port = 19132 if server_type == "bedrock" else 25565
        raw_port = item.get("server_port")
        server_port = default_port if raw_port in (None, "") else safe_int(raw_port, -1)
        if not 1 <= server_port <= 65535:
            raise ValueError(f"{server_name} 端口必须在 1 到 65535 之间")
        check_interval = resolve_check_interval(
            item.get("check_interval", global_check_interval), global_check_interval
        )
        identity = f"{server_type}|{server_ip.lower()}|{server_port}"
        key = raw_key or "server_" + hashlib.sha256(identity.encode()).hexdigest()[:12]

        if not server_ip:
            logger.warning(f"{server_name} 缺少 server_ip，已跳过。")
            continue

        if any(server.key == key for server in servers):
            raise ValueError(f"服务器唯一标识重复: {key}，请为每台服务器设置不同的 key")
        if any(char.isspace() for char in server_ip) or any(char in server_ip for char in "/?#@"):
            raise ValueError(f"{server_name} 地址只能填写域名或 IP，不要包含协议或路径")

        servers.append(
            ServerConfig(
                key=key,
                enabled=enabled,
                server_name=server_name,
                server_ip=server_ip,
                server_port=server_port,
                server_type=server_type,
                check_interval=check_interval,
            )
        )

    return servers


def build_initial_states(servers: list[ServerConfig]) -> dict[str, ServerState]:
    return {server.key: ServerState() for server in servers}
