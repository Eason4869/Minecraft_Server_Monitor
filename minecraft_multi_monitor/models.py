from dataclasses import dataclass, field


@dataclass(slots=True)
class DisplayOptions:
    show_server_name: bool = True
    show_server_address: bool = False
    show_server_type: bool = True
    show_game_version: bool = False
    show_online_players: bool = True
    show_motd: bool = True
    show_player_list: bool = False
    show_update_time: bool = True
    show_hitokoto: bool = False


@dataclass(slots=True)
class PluginSettings:
    target_group: str | None
    enable_auto_monitor: bool
    global_check_interval: int
    use_mcbbs_api: bool
    display_options: DisplayOptions


@dataclass(slots=True)
class ServerConfig:
    key: str
    enabled: bool
    server_name: str
    server_ip: str
    server_port: int
    server_type: str
    check_interval: int


@dataclass(slots=True)
class ServerState:
    last_player_count: int | None = None
    last_player_list: list[str] = field(default_factory=list)
    last_status: str | None = None
    last_check_ts: float = 0.0


@dataclass(slots=True)
class ServerSnapshot:
    key: str
    name: str
    server_ip: str
    server_port: int
    server_type: str
    status: str
    motd: str
    version: str
    protocol: str
    online: int
    max_players: int
    players: list[str]
    software: str
    map_name: str
    update_time: str
    raw_online: bool
    host: str = ""
    delay: int | None = None
    error: str | None = None
