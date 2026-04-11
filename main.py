import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from minecraft_multi_monitor.clients import build_status_client
from minecraft_multi_monitor.config import (
    build_initial_states,
    load_servers_from_config,
    load_settings,
)
from minecraft_multi_monitor.models import ServerConfig
from minecraft_multi_monitor.monitoring import detect_changes, format_server_info
from minecraft_multi_monitor.services import get_hitokoto, notify_group


@register("minecraft_multi_monitor", "xlyang", "Minecraft 多服务器监控插件", "0.1.0")
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}

        settings = load_settings(self.config)
        self.task: asyncio.Task | None = None
        self.target_group = settings.target_group
        self.enable_auto_monitor = settings.enable_auto_monitor
        self.global_check_interval = settings.global_check_interval
        self.use_mcbbs_api = settings.use_mcbbs_api
        self.display_options = settings.display_options

        self.servers = load_servers_from_config(self.config, self.global_check_interval)
        self.server_states = build_initial_states(self.servers)
        self.server_locks = {server.key: asyncio.Lock() for server in self.servers}
        self.status_client = build_status_client(self.use_mcbbs_api)

        logger.info(
            "Minecraft 多服务器监控插件已加载，"
            f"目标群: {self.target_group}, 自动启动: {self.enable_auto_monitor}, "
            f"服务器数: {len(self.servers)}, 默认API: {'MineBBS' if self.use_mcbbs_api else 'mcstatus.io'}"
        )

        if self.enable_auto_monitor and self.servers:
            asyncio.create_task(self._delayed_auto_start())

    async def initialize(self):
        logger.info("Minecraft 多服务器监控插件初始化完成")

    async def terminate(self):
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("Minecraft 多服务器监控任务已停止")

    async def _delayed_auto_start(self):
        await asyncio.sleep(5)
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.monitor_loop())
            logger.info("Minecraft 多服务器监控已自动启动")

    async def _fetch_server_data(self, server: ServerConfig, source: str):
        lock = self.server_locks.setdefault(server.key, asyncio.Lock())
        async with lock:
            return await self.status_client.fetch_status(server, source)

    async def get_all_server_status_text(self) -> str:
        if not self.servers:
            return "❌ 当前没有启用的服务器配置"

        blocks: list[str] = []
        for index, server in enumerate(self.servers, start=1):
            snapshot = await self._fetch_server_data(server, source="manual")
            blocks.append(
                f"===== {index}. {server.server_name} =====\n"
                f"{format_server_info(snapshot, self.display_options)}"
            )
        return "\n\n".join(blocks)

    async def monitor_loop(self):
        try:
            while True:
                await asyncio.sleep(1)
                if not self.servers:
                    continue

                now = asyncio.get_running_loop().time()
                for server in self.servers:
                    state = self.server_states[server.key]
                    if now - state.last_check_ts < server.check_interval:
                        continue

                    state.last_check_ts = now
                    snapshot = await self._fetch_server_data(server, source="monitor")
                    if snapshot is None:
                        logger.warning(f"[monitor] [{server.server_name}] 本次未获取到服务器状态")
                        continue

                    logger.info(
                        f"[monitor] [{server.server_name}] 当前状态: "
                        f"status={snapshot.status}, players={snapshot.online}/{snapshot.max_players}, "
                        f"names={snapshot.players}"
                    )

                    changed, message = detect_changes(server, state, snapshot)
                    if not changed or not message:
                        logger.info(f"[monitor] [{server.server_name}] 本次无状态变化")
                        continue

                    logger.info(f"[monitor] [{server.server_name}] 检测到状态变化: {message}")
                    detail = format_server_info(snapshot, self.display_options)
                    final_message = f"🔔 服务器状态变化：\n{message}\n\n📊 当前状态：\n{detail}"
                    if self.display_options.show_hitokoto:
                        hitokoto = await get_hitokoto()
                        if hitokoto:
                            final_message += f"\n\n💬 {hitokoto}"

                    sent = await notify_group(self.context, self.target_group, final_message)
                    if not sent:
                        logger.error(f"[monitor] [{server.server_name}] 状态变化已识别，但群消息发送失败")
        except asyncio.CancelledError:
            logger.info("监控循环已取消")
            raise
        except Exception as exc:
            logger.error(f"监控循环异常: {exc}")

    @filter.command("start_server_monitor")
    async def start_server_monitor_task(self, event: AstrMessageEvent):
        if not self.servers:
            yield event.plain_result("❌ 当前没有启用的服务器配置，请先在 WebUI 中配置 server_entries")
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
        if self.display_options.show_hitokoto:
            hitokoto = await get_hitokoto()
            if hitokoto:
                text += f"\n\n💬 {hitokoto}"
        yield event.plain_result(text)

    @filter.command("重置监控")
    async def reset_monitor(self, event: AstrMessageEvent):
        self.server_states = build_initial_states(self.servers)
        self.server_locks = {server.key: asyncio.Lock() for server in self.servers}
        yield event.plain_result("✅ 监控状态缓存已重置")


__all__ = ["MyPlugin"]
