from .models import DisplayOptions, ServerConfig, ServerSnapshot, ServerState


def server_info_fields(snapshot: ServerSnapshot, options: DisplayOptions) -> list[tuple[str, str, str]]:
    fields = []
    if options.show_server_address:
        host = snapshot.server_ip
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        fields.append(("🌐", "地址", f"{host}:{snapshot.server_port}"))
    if options.show_server_type:
        fields.append(("🔧", "类型", "基岩版" if snapshot.server_type == "bedrock" else "Java版"))
    if snapshot.status == "online":
        if options.show_game_version:
            fields.append(("🎮", "版本", snapshot.version))
        if options.show_online_players:
            fields.append(("👥", "在线玩家", f"{snapshot.online}/{snapshot.max_players}"))
        if options.show_motd and snapshot.motd.strip():
            motd = snapshot.motd.strip()
            fields.append(("📝", "MOTD", motd[:120] + ("…" if len(motd) > 120 else "")))
        if options.show_player_list:
            if snapshot.players:
                fields.append(("📋", "玩家样本（可能不完整）", ", ".join(snapshot.players[:8])))
            elif snapshot.online == 0:
                fields.append(("📋", "玩家列表", "当前无玩家在线"))
            else:
                fields.append(("📋", "玩家列表", "服务器未提供玩家名单"))
    if options.show_update_time:
        fields.append(("🕒", "数据时间", snapshot.update_time))
    if snapshot.error and snapshot.status != "online":
        fields.append(("⚠️", "说明", snapshot.error[:200]))
    return fields


def format_server_info(snapshot: ServerSnapshot | None, display_options: DisplayOptions) -> str:
    if snapshot is None:
        return "⚠️ 查询异常：未获取到服务器数据"
    emoji, label = {
        "online": ("🟢", "在线"),
        "offline": ("🔴", "离线"),
        "unknown": ("🟡", "查询异常"),
    }.get(snapshot.status, ("🟡", "查询异常"))
    header = (
        f"{emoji} {snapshot.name}: {label}" if display_options.show_server_name else f"{emoji} 状态: {label}"
    )
    return "\n".join(
        [
            header,
            *(
                f"{icon} {name}: {value}"
                for icon, name, value in server_info_fields(snapshot, display_options)
            ),
        ]
    )


def detect_changes(
    server: ServerConfig, state: ServerState, snapshot: ServerSnapshot
) -> tuple[bool, str | None]:
    first = state.last_status is None
    old_status, old_count = state.last_status, state.last_player_count
    old_names = set(state.last_player_list)
    state.last_status = snapshot.status
    state.last_player_count = snapshot.online
    state.last_player_list = snapshot.players[:]
    labels = {"online": "已上线", "offline": "已离线", "unknown": "查询异常，暂无法判断服务器是否在线"}
    if first:
        return True, f"{server.server_name} 监控已启动：{labels[snapshot.status]}"
    if old_status != snapshot.status:
        return True, f"{server.server_name} {labels[snapshot.status]}"
    if snapshot.status != "online":
        return False, None
    changes = []
    if old_count != snapshot.online:
        changes.append(f"{server.server_name} 在线人数: {old_count} → {snapshot.online}")
    if old_names != set(snapshot.players):
        changes.append(f"{server.server_name} 玩家样本发生变化（不代表完整进出记录）")
    return bool(changes), "\n".join(changes) or None
