import asyncio
import aiohttp
from datetime import datetime

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event.filter import PlatformAdapterType


@register("minecraft_monitor", "xlyang", "Minecraft 多服务器监控插件", "0.1")
class MyPlugin(Star):
    DISPLAY_DEFAULTS = {
        "show_server_name": True,
        "show_server_address": False,
        "show_server_type": True,
        "show_game_version": False,
        "show_online_players": True,
        "show_motd": True,
        "show_player_list": False,
        "show_update_time": True,
        "show_hitokoto": False,
    }

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}

        self.task = None
        self.last_update_time = None

        self.target_group = self._parse_target_group(self.config.get("target_group"))
        self.enable_auto_monitor = self._safe_bool(self.config.get("enable_auto_monitor", False), False)
        self.global_check_interval = self._safe_int(self.config.get("check_interval", 45), 45)
        self.display_options = self._load_display_options()

        self.servers = self._load_servers_from_config()
        self.server_states = self._build_initial_states(self.servers)
        self.server_locks = {server["key"]: asyncio.Lock() for server in self.servers}

        logger.info(
            f"Minecraft多服务器监控插件已加载，目标群: {self.target_group}, "
            f"自动启动: {self.enable_auto_monitor}, 服务器数: {len(self.servers)}"
        )

        if self.enable_auto_monitor and self.servers:
            asyncio.create_task(self._delayed_auto_start())

    async def initialize(self):
        logger.info("Minecraft多服务器监控插件初始化完成")

    async def terminate(self):
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("Minecraft多服务器监控任务已停止")

    def _safe_int(self, value, default):
        try:
            return int(value)
        except Exception:
            return default

    def _parse_target_group(self, value):
        if value is None:
            return None
        value = str(value).strip()
        return value if value.isdigit() else None

    def _safe_bool(self, value, default=False):
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

    def _load_display_options(self):
        raw_options = self.config.get("display_options", {})
        options = self.DISPLAY_DEFAULTS.copy()

        if not isinstance(raw_options, dict):
            logger.warning("配置项 display_options 不是对象，已使用默认展示设置。")
            return options

        for key, default in self.DISPLAY_DEFAULTS.items():
            options[key] = self._safe_bool(raw_options.get(key, default), default)
        return options

    def _normalize_server_type(self, server_type: str) -> str:
        server_type = str(server_type or "java").strip().lower()
        return server_type if server_type in ("bedrock", "java") else "java"

    def _load_servers_from_config(self):
        raw_servers = self.config.get("servers", [])
        servers = []

        if not isinstance(raw_servers, list):
            logger.warning("配置项 servers 不是列表，已忽略。")
            raw_servers = []

        for index, item in enumerate(raw_servers, start=1):
            if not isinstance(item, dict):
                logger.warning(f"servers 第 {index} 项不是对象，已跳过。")
                continue

            enabled = self._safe_bool(item.get("enabled", True), True)
            if not enabled:
                continue

            server_name = str(item.get("server_name", f"服务器{index}")).strip() or f"服务器{index}"
            server_ip = str(item.get("server_ip", "")).strip()
            server_port = self._safe_int(item.get("server_port", 25565), 25565)
            server_type = self._normalize_server_type(item.get("server_type", "java"))
            check_interval = self._safe_int(
                item.get("check_interval", self.global_check_interval),
                self.global_check_interval
            )
            key = str(item.get("key", f"server_{index}")).strip() or f"server_{index}"

            if not server_ip:
                logger.warning(f"{server_name} 缺少 server_ip，已跳过。")
                continue

            if check_interval < 5:
                check_interval = 5

            servers.append({
                "key": key,
                "enabled": enabled,
                "server_name": server_name,
                "server_ip": server_ip,
                "server_port": server_port,
                "server_type": server_type,
                "check_interval": check_interval,
            })

        return servers

    def _build_initial_states(self, servers):
        states = {}
        for server in servers:
            states[server["key"]] = {
                "last_player_count": None,
                "last_player_list": [],
                "last_status": None,
                "last_check_ts": 0.0,
            }
        return states

    async def _delayed_auto_start(self):
        await asyncio.sleep(5)
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.monitor_loop())
            logger.info("Minecraft多服务器监控已自动启动")

    async def get_hitokoto(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://v1.hitokoto.cn/?encode=text",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status == 200:
                        return (await response.text()).strip()
        except Exception as e:
            logger.warning(f"获取一言失败: {e}")
        return None

    async def notify_group(self, message: str):
        if not self.target_group:
            logger.error("target_group 未配置，无法发送通知")
            return False

        try:
            platform = self.context.get_platform(PlatformAdapterType.AIOCQHTTP)
            if not platform or not hasattr(platform, "get_client"):
                logger.error("无法获取 AIOCQHTTP 客户端")
                return False

            client = platform.get_client()
            result = await client.api.call_action(
                "send_group_msg",
                group_id=int(self.target_group),
                message=message
            )

            if result and result.get("message_id"):
                return True

            logger.warning(f"发送群消息失败: {result}")
            return False
        except Exception as e:
            logger.error(f"发送群消息异常: {e}")
            return False

    async def _fetch_server_data(self, server: dict, source: str = "monitor"):
        server_name = server["server_name"]
        server_ip = server["server_ip"]
        server_port = server["server_port"]
        server_type = server["server_type"]

        api_url = f"https://api.mcstatus.io/v2/status/{server_type}/{server_ip}:{server_port}?timeout=20"
        lock = self.server_locks.setdefault(server["key"], asyncio.Lock())

        async with lock:
            try:
                logger.info(f"[{source}] [{server_name}] 开始查询: {api_url}")

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        api_url,
                        headers={"User-Agent": "MinecraftServerMonitor/2.2"},
                        timeout=aiohttp.ClientTimeout(total=25)
                    ) as response:
                        raw_text = await response.text()

                        if response.status != 200:
                            logger.warning(
                                f"[{source}] [{server_name}] HTTP错误: {response.status}, "
                                f"body={raw_text[:300]}"
                            )
                            return None

                        try:
                            data = await response.json(content_type=None)
                        except Exception as e:
                            logger.warning(
                                f"[{source}] [{server_name}] JSON解析失败: {e}, body={raw_text[:300]}"
                            )
                            return None

                        parsed = self._parse_server_data(server, data)
                        logger.info(
                            f"[{source}] [{server_name}] 查询完成: "
                            f"online={parsed['status']}, players={parsed['online']}/{parsed['max']}"
                        )
                        return parsed

            except asyncio.TimeoutError:
                logger.warning(f"[{source}] [{server_name}] 查询超时: {api_url}")
                return None
            except aiohttp.ClientError as e:
                logger.warning(f"[{source}] [{server_name}] 网络错误: {e}")
                return None
            except Exception as e:
                logger.error(f"[{source}] [{server_name}] 查询异常: {e}")
                return None

    def _parse_server_data(self, server: dict, data: dict):
        online = bool(data.get("online", False))
        status = "online" if online else "offline"

        motd_info = data.get("motd", {})
        if isinstance(motd_info, dict):
            motd = motd_info.get("clean") or motd_info.get("raw") or ""
        else:
            motd = str(motd_info or "")

        version_info = data.get("version", {})
        if isinstance(version_info, dict):
            version = version_info.get("name", "未知版本")
            protocol = version_info.get("protocol", "未知")
        else:
            version = str(version_info or "未知版本")
            protocol = "未知"

        players_info = data.get("players", {})
        if isinstance(players_info, dict):
            online_players = int(players_info.get("online", 0) or 0)
            max_players = int(players_info.get("max", 0) or 0)
            player_list = players_info.get("list", []) or []
        else:
            online_players = 0
            max_players = 0
            player_list = []

        return {
            "key": server["key"],
            "name": server["server_name"],
            "server_ip": server["server_ip"],
            "server_port": server["server_port"],
            "server_type": server["server_type"],
            "status": status,
            "motd": motd,
            "version": version,
            "protocol": protocol,
            "online": online_players,
            "max": max_players,
            "players": player_list,
            "software": data.get("software", "未知"),
            "map": (data.get("map", {}) or {}).get("name", "未知") if isinstance(data.get("map"), dict) else "未知",
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw_online": data.get("online", False),
        }

    def _extract_player_names(self, player_list):
        names = []
        if not isinstance(player_list, list):
            return names

        for p in player_list:
            if isinstance(p, dict):
                names.append(str(p.get("name_clean") or p.get("name") or p.get("username") or "未知玩家"))
            else:
                names.append(str(p))
        return names

    def _format_server_info(self, server_data: dict):
        if not server_data:
            return "❌ 获取服务器数据失败"

        status_emoji = "🟢" if server_data["status"] == "online" else "🔴"
        type_label = "基岩版" if server_data["server_type"] == "bedrock" else "Java版"

        lines = []

        if self.display_options["show_server_name"]:
            lines.append(f"{status_emoji} 服务器: {server_data['name']}")
        else:
            lines.append(f"{status_emoji} 状态: {'在线' if server_data['status'] == 'online' else '离线'}")

        if self.display_options["show_server_address"]:
            lines.append(f"🌐 地址: {server_data['server_ip']}:{server_data['server_port']}")

        if self.display_options["show_server_type"]:
            lines.append(f"🔧 类型: {type_label}")

        if self.display_options["show_game_version"]:
            lines.append(f"🎮 版本: {server_data['version']}")

        if self.display_options["show_online_players"]:
            lines.append(f"👥 在线玩家: {server_data['online']}/{server_data['max']}")

        motd = server_data.get("motd", "").strip()
        if self.display_options["show_motd"] and motd:
            if len(motd) > 120:
                motd = motd[:120] + "..."
            lines.append(f"📝 MOTD: {motd}")

        player_names = self._extract_player_names(server_data.get("players", []))
        if self.display_options["show_player_list"]:
            if player_names:
                show_names = player_names[:8]
                line = f"📋 玩家列表: {', '.join(show_names)}"
                if len(player_names) > 8:
                    line += f" (+{len(player_names) - 8}人)"
                lines.append(line)
            else:
                lines.append("📋 当前无玩家在线")

        if self.display_options["show_update_time"]:
            lines.append(f"🕒 更新时间: {server_data['update_time']}")

        return "\n".join(lines)

    def _check_changes(self, server: dict, server_data: dict):
        state = self.server_states[server["key"]]

        current_status = server_data["status"]
        current_count = server_data["online"]
        current_names = self._extract_player_names(server_data["players"])

        if state["last_player_count"] is None:
            state["last_player_count"] = current_count
            state["last_player_list"] = current_names[:]
            state["last_status"] = current_status

            if current_count > 0:
                return True, f"🟢 {server['server_name']} 监控已启动，当前有玩家在线"
            return True, f"🟢 {server['server_name']} 监控已启动"

        changes = []

        if state["last_status"] != current_status:
            if current_status == "online":
                changes.append(f"🟢 {server['server_name']} 已上线")
            else:
                changes.append(f"🔴 {server['server_name']} 已离线")

        diff = current_count - state["last_player_count"]
        if diff > 0:
            new_players = sorted(set(current_names) - set(state["last_player_list"]))
            if new_players:
                changes.append(f"📈 {server['server_name']}: {', '.join(new_players)} 加入了服务器 (+{diff})")
            else:
                changes.append(f"📈 {server['server_name']}: 有 {diff} 名玩家加入了服务器")
        elif diff < 0:
            left_players = sorted(set(state["last_player_list"]) - set(current_names))
            if left_players:
                changes.append(f"📉 {server['server_name']}: {', '.join(left_players)} 离开了服务器 ({diff})")
            else:
                changes.append(f"📉 {server['server_name']}: 有 {abs(diff)} 名玩家离开了服务器")

        state["last_player_count"] = current_count
        state["last_player_list"] = current_names[:]
        state["last_status"] = current_status

        if changes:
            return True, "\n".join(changes)
        return False, None

    async def get_all_server_status_text(self):
        if not self.servers:
            return "❌ 当前没有启用的服务器配置"

        blocks = []
        for idx, server in enumerate(self.servers, start=1):
            server_data = await self._fetch_server_data(server, source="manual")
            blocks.append(f"===== {idx}. {server['server_name']} =====\n{self._format_server_info(server_data)}")
        return "\n\n".join(blocks)

    async def monitor_loop(self):
        try:
            while True:
                await asyncio.sleep(1)

                if not self.servers:
                    continue

                now = asyncio.get_running_loop().time()

                for server in self.servers:
                    state = self.server_states[server["key"]]
                    interval = max(5, int(server["check_interval"]))

                    if now - state["last_check_ts"] < interval:
                        continue

                    state["last_check_ts"] = now

                    server_data = await self._fetch_server_data(server, source="monitor")
                    if server_data is None:
                        continue

                    changed, message = self._check_changes(server, server_data)
                    if changed:
                        detail = self._format_server_info(server_data)
                        final_message = f"🔔 服务器状态变化：\n{message}\n\n📊 当前状态：\n{detail}"
                        hitokoto = None
                        if self.display_options["show_hitokoto"]:
                            hitokoto = await self.get_hitokoto()
                        if hitokoto:
                            final_message += f"\n\n💬 {hitokoto}"
                        await self.notify_group(final_message)
        except asyncio.CancelledError:
            logger.info("监控循环已取消")
            raise
        except Exception as e:
            logger.error(f"监控循环异常: {e}")

    @filter.command("start_server_monitor")
    async def start_server_monitor_task(self, event: AstrMessageEvent):
        if not self.servers:
            yield event.plain_result("❌ 当前没有启用的服务器配置，请先在 WebUI 中配置 servers")
            return

        if self.task and not self.task.done():
            yield event.plain_result("✅ 监控任务已经在运行中")
            return

        self.task = asyncio.create_task(self.monitor_loop())
        yield event.plain_result(f"✅ 多服务器监控已启动，当前监控 {len(self.servers)} 台服务器")

    @filter.command("stop_server_monitor")
    async def stop_server_monitor_task(self, event: AstrMessageEvent):
        if self.task and not self.task.done():
            self.task.cancel()
            yield event.plain_result("✅ 监控任务已停止")
        else:
            yield event.plain_result("❌ 当前没有正在运行的监控任务")

    @filter.command("查询")
    async def query_server_status(self, event: AstrMessageEvent):
        text = await self.get_all_server_status_text()
        hitokoto = None
        if self.display_options["show_hitokoto"]:
            hitokoto = await self.get_hitokoto()
        if hitokoto:
            text += f"\n\n💬 {hitokoto}"
        yield event.plain_result(text)

    @filter.command("重置监控")
    async def reset_monitor(self, event: AstrMessageEvent):
        self.server_states = self._build_initial_states(self.servers)
        self.server_locks = {server["key"]: asyncio.Lock() for server in self.servers}
        yield event.plain_result("✅ 监控状态缓存已重置")
