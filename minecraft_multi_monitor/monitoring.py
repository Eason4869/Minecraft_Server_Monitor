from .models import DisplayOptions, ServerConfig, ServerSnapshot, ServerState


def format_server_info(snapshot: ServerSnapshot | None, display_options: DisplayOptions) -> str:
    if not snapshot:
        return "❌ 获取服务器数据失败"

    status_emoji = "🟢" if snapshot.status == "online" else "🔴"
    type_label = "基岩版" if snapshot.server_type == "bedrock" else "Java版"

    lines: list[str] = []
    if display_options.show_server_name:
        lines.append(f"{status_emoji} 服务器: {snapshot.name}")
    else:
        lines.append(f"{status_emoji} 状态: {'在线' if snapshot.status == 'online' else '离线'}")

    if display_options.show_server_address:
        lines.append(f"🌐 地址: {snapshot.server_ip}:{snapshot.server_port}")

    if display_options.show_server_type:
        lines.append(f"🔧 类型: {type_label}")

    if display_options.show_game_version:
        lines.append(f"🎮 版本: {snapshot.version}")

    if display_options.show_online_players:
        lines.append(f"👥 在线玩家: {snapshot.online}/{snapshot.max_players}")

    motd = snapshot.motd.strip()
    if display_options.show_motd and motd:
        lines.append(f"📝 MOTD: {motd[:120]}{'...' if len(motd) > 120 else ''}")

    if display_options.show_player_list:
        if snapshot.players:
            show_names = snapshot.players[:8]
            line = f"📋 玩家列表: {', '.join(show_names)}"
            if len(snapshot.players) > 8:
                line += f" (+{len(snapshot.players) - 8}人)"
            lines.append(line)
        else:
            lines.append("📋 当前无玩家在线")

    if display_options.show_update_time:
        lines.append(f"🕒 更新时间: {snapshot.update_time}")

    if snapshot.error and snapshot.status != "online":
        lines.append(f"⚠️ 说明: {snapshot.error}")

    return "\n".join(lines)


def detect_changes(
    server: ServerConfig,
    state: ServerState,
    snapshot: ServerSnapshot,
) -> tuple[bool, str | None]:
    current_status = snapshot.status
    current_count = snapshot.online
    current_names = snapshot.players[:]

    if state.last_player_count is None:
        state.last_player_count = current_count
        state.last_player_list = current_names
        state.last_status = current_status
        if current_count > 0:
            return True, f"🟢 {server.server_name} 监控已启动，当前有玩家在线"
        return True, f"🟢 {server.server_name} 监控已启动"

    changes: list[str] = []

    if state.last_status != current_status:
        if current_status == "online":
            changes.append(f"🟢 {server.server_name} 已上线")
        else:
            changes.append(f"🔴 {server.server_name} 已离线")

    diff = current_count - state.last_player_count
    if diff > 0:
        new_players = sorted(set(current_names) - set(state.last_player_list))
        if new_players:
            changes.append(f"📈 {server.server_name}: {', '.join(new_players)} 加入了服务器 (+{diff})")
        else:
            changes.append(f"📈 {server.server_name}: 有 {diff} 名玩家加入了服务器")
    elif diff < 0:
        left_players = sorted(set(state.last_player_list) - set(current_names))
        if left_players:
            changes.append(f"📉 {server.server_name}: {', '.join(left_players)} 离开了服务器 ({diff})")
        else:
            changes.append(f"📉 {server.server_name}: 有 {abs(diff)} 名玩家离开了服务器")

    state.last_player_count = current_count
    state.last_player_list = current_names
    state.last_status = current_status

    if changes:
        return True, "\n".join(changes)
    return False, None
