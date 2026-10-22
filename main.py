import asyncio
from dataclasses import replace

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .minecraft_multi_monitor.clients import build_status_client, unavailable_snapshot
from .minecraft_multi_monitor.config import (
    build_initial_states,
    load_servers_from_config,
    load_settings,
    safe_bool,
)
from .minecraft_multi_monitor.delivery import MAX_ATTEMPTS, NotificationQueue, accepts_notification
from .minecraft_multi_monitor.models import ServerConfig
from .minecraft_multi_monitor.monitoring import detect_changes, format_server_info
from .minecraft_multi_monitor.rendering import CardRenderer
from .minecraft_multi_monitor.services import get_hitokoto, notify_session
from .minecraft_multi_monitor.subscriptions import GroupSubscription, SubscriptionStore


@register("astrbot_plugin_minecraft_monitor", "Eason4869", "Minecraft 服务器监控", "0.3.2")
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config if config is not None else {}
        settings = load_settings(self.config)
        self.enable_auto_monitor = settings.enable_auto_monitor
        self.target_group = settings.target_group
        self.display_options = settings.display_options
        catalog = load_servers_from_config(self.config, settings.global_check_interval, include_disabled=True)
        self.server_catalog = catalog
        self.servers = [server for server in catalog if server.enabled]
        self.server_states = build_initial_states(self.servers)
        self.server_locks = {server.key: asyncio.Lock() for server in self.servers}
        self.subscriptions = SubscriptionStore(self.config, {server.key for server in catalog})
        self.status_client = build_status_client(
            settings.use_mcbbs_api, safe_bool(self.config.get("enable_api_fallback"), True)
        )
        self.renderer = CardRenderer(lambda *args, **kwargs: self.html_render(*args, **kwargs))
        self.notifications = NotificationQueue()
        self.cache = {}
        self.failures = {}
        self.offline_counts = {}
        self.fetch_semaphore = asyncio.Semaphore(3)
        self.inflight_requests = set()
        self.control_lock = asyncio.Lock()
        self.task = None
        self.delivery_task = None
        self.startup_task = None
        self.closed = False

    async def initialize(self):
        self._migrate_legacy_group()
        if self.enable_auto_monitor and self.servers and not self.closed:
            if self.startup_task is None:
                self.startup_task = asyncio.create_task(self._delayed_auto_start())
        logger.info(
            f"Minecraft 监控已加载：{len(self.servers)} 台服务器，{len(self.subscriptions.groups)} 个群订阅"
        )

    def _migrate_legacy_group(self):
        if not self.target_group or self.subscriptions.groups:
            return
        manager = getattr(self.context, "platform_manager", None)
        platforms = [
            platform
            for platform in getattr(manager, "platform_insts", [])
            if platform.meta().name == "aiocqhttp"
        ]
        if len(platforms) != 1:
            logger.warning(
                "旧 target_group 无法唯一确定 OneBot 平台，请由管理员在目标群 /mc 订阅 key，或填写 group_entries"
            )
            return
        session = f"{platforms[0].meta().id}:GroupMessage:{self.target_group}"
        try:
            self.subscriptions.put(GroupSubscription(session, tuple(server.key for server in self.servers)))
            logger.info(f"已迁移旧 target_group 为分群订阅: {session}")
        except Exception as exc:
            logger.error(f"旧配置迁移失败 {type(exc).__name__}: {exc}")

    async def _delayed_auto_start(self):
        await asyncio.sleep(5)
        await self._start_monitor()

    async def _start_monitor(self):
        async with self.control_lock:
            await self._start_monitor_unlocked()

    async def _start_monitor_unlocked(self):
        if self.closed:
            return
        if (
            self.startup_task
            and self.startup_task is not asyncio.current_task()
            and not self.startup_task.done()
        ):
            self.startup_task.cancel()
            await asyncio.gather(self.startup_task, return_exceptions=True)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.monitor_loop())
        if self.delivery_task is None or self.delivery_task.done():
            self.delivery_task = asyncio.create_task(self._delivery_loop())

    async def _stop_monitor(self):
        async with self.control_lock:
            await self._stop_monitor_unlocked()

    async def _stop_monitor_unlocked(self):
        tasks = [
            task
            for task in (self.startup_task, self.task, self.delivery_task)
            if task and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.startup_task = self.task = self.delivery_task = None

    async def terminate(self):
        self.closed = True
        await self._stop_monitor()
        await self.renderer.close()
        requests = tuple(self.inflight_requests)
        for request in requests:
            request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        await self.status_client.close()
        self.notifications.pending.clear()

    async def _fetch_server_data(self, server: ServerConfig, source: str):
        async with self.server_locks[server.key]:
            if self.closed:
                return None
            now = asyncio.get_running_loop().time()
            cached = self.cache.get(server.key)
            if cached and now - cached[0] < 3:
                return cached[1]
            async with self.fetch_semaphore:
                if self.closed:
                    return None
                started = asyncio.get_running_loop().time()
                request = asyncio.create_task(self.status_client.fetch_status(server, source))
                self.inflight_requests.add(request)
                try:
                    snapshot = await request
                except Exception as exc:
                    logger.warning(
                        f"[{source}] [{server.key}] 查询异常 stage=fetch "
                        f"elapsed={asyncio.get_running_loop().time() - started:.2f}s "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return None
                finally:
                    self.inflight_requests.discard(request)
            if snapshot is not None:
                self.cache[server.key] = (asyncio.get_running_loop().time(), snapshot)
            return snapshot

    async def _check_server(self, server):
        state = self.server_states[server.key]
        state.last_check_ts = asyncio.get_running_loop().time()
        snapshot = await self._fetch_server_data(server, "monitor")
        if snapshot is None:
            self.failures[server.key] = self.failures.get(server.key, 0) + 1
            self.offline_counts[server.key] = 0
            if self.failures[server.key] < 2:
                return
            snapshot = unavailable_snapshot(server)
        else:
            self.failures[server.key] = 0
        if snapshot.status == "offline":
            self.offline_counts[server.key] = self.offline_counts.get(server.key, 0) + 1
            if self.offline_counts[server.key] < 2:
                return
        else:
            self.offline_counts[server.key] = 0
        kind = "status" if state.last_status != snapshot.status else "players"
        changed, message = detect_changes(server, state, snapshot)
        if not changed:
            return
        for group in tuple(self.subscriptions.groups.values()):
            if (
                server.key in group.server_keys
                and group.notify_mode != "off"
                and (group.notify_mode == "all" or kind == "status")
            ):
                self.notifications.enqueue(group.session, server.key, message, snapshot, kind)

    async def monitor_loop(self):
        while True:
            now = asyncio.get_running_loop().time()
            due = [
                server
                for server in self.servers
                if now - self.server_states[server.key].last_check_ts >= server.check_interval
            ]
            results = await asyncio.gather(
                *(self._check_server(server) for server in due), return_exceptions=True
            )
            for server, result in zip(due, results, strict=True):
                if isinstance(result, Exception):
                    logger.error(f"[{server.key}] 检测失败 {type(result).__name__}: {result}")
            await asyncio.sleep(1)

    async def _send_notification(self, item):
        group = self.subscriptions.get(item.session)
        if self.closed or not accepts_notification(group, item):
            return True
        detail = format_server_info(item.snapshot, self.display_options)
        text = f"🔔 {item.message}\n\n{detail}"
        hitokoto = ""
        if self.display_options.show_hitokoto:
            hitokoto = await get_hitokoto() or ""
            if hitokoto:
                text += f"\n\n💬 {hitokoto}"
        # Quote/render waits can outlive a subscription or style change.
        group = self.subscriptions.get(item.session)
        if self.closed or not accepts_notification(group, item):
            return True
        if group.notification_style == "image" and item.snapshot is not None:
            if await self._send_image_notification(item, hitokoto):
                return True
            group = self.subscriptions.get(item.session)
            if self.closed or not accepts_notification(group, item):
                return True
            if group.notification_style == "image" and item.attempts + 1 < MAX_ATTEMPTS:
                return False
            logger.warning(
                f"通知图片降级 [{item.session}] [{item.server_key}] "
                f"attempt={item.attempts + 1}/{MAX_ATTEMPTS} action=text"
            )
        if self.closed or not accepts_notification(self.subscriptions.get(item.session), item):
            return True
        return await notify_session(self.context, item.session, text)

    async def _send_image_notification(self, item, quote):
        started = asyncio.get_running_loop().time()
        try:
            images = await asyncio.wait_for(
                self.renderer.render_pages([item.snapshot], self.display_options, quote), timeout=35
            )
            if not images:
                raise ValueError("渲染服务未返回图片")
        except Exception as exc:
            logger.warning(
                f"通知图片失败 [{item.session}] [{item.server_key}] stage=render "
                f"attempt={item.attempts + 1}/{MAX_ATTEMPTS} "
                f"elapsed={asyncio.get_running_loop().time() - started:.2f}s {type(exc).__name__}: {exc}"
            )
            return False
        if self.closed or not accepts_notification(self.subscriptions.get(item.session), item):
            return True
        if self.subscriptions.get(item.session).notification_style != "image":
            return False
        sent = await notify_session(self.context, item.session, item.message, images[0])
        if not sent:
            self.renderer.invalidate(images[0])
            logger.warning(
                f"通知图片失败 [{item.session}] [{item.server_key}] stage=image_send "
                f"attempt={item.attempts + 1}/{MAX_ATTEMPTS} "
                f"elapsed={asyncio.get_running_loop().time() - started:.2f}s"
            )
        return sent

    async def _delivery_loop(self):
        while True:
            try:
                await self.notifications.flush(self._send_notification, self.subscriptions.get)
            except Exception as exc:
                logger.error(f"投递循环异常 {type(exc).__name__}: {exc}")
            await asyncio.sleep(1)

    def _visible_servers(self, event):
        if not event.get_group_id():
            if not event.is_admin():
                raise ValueError("私聊查服仅限 AstrBot 管理员")
            return self.servers[:]
        group = self.subscriptions.get(event.unified_msg_origin)
        if group is None or not group.server_keys:
            raise ValueError("本群未绑定服务器，请联系 AstrBot 管理员 /mc 订阅 key")
        keys = group.server_keys
        return [server for server in self.servers if server.key in keys]

    def _select_server(self, value, servers):
        matches = [server for server in servers if server.key == value]
        if not matches:
            matches = [server for server in servers if server.server_name == value]
        if len(matches) != 1:
            raise ValueError("未找到唯一的可访问服务器，请用 /mc 列表中的 key")
        return matches[0]

    def _allowed_query_snapshots(self, event, snapshots):
        if self.closed:
            return []
        try:
            allowed_keys = {server.key for server in self._visible_servers(event)}
        except ValueError:
            return []
        return [snapshot for snapshot in snapshots if snapshot.key in allowed_keys]

    async def _query(self, event, key="", style=""):
        if self.closed:
            return
        try:
            servers = self._visible_servers(event)
            group = self.subscriptions.get(event.unified_msg_origin)
            if not key and group and any(server.key == group.default_server for server in servers):
                key = group.default_server
            if key:
                servers = [self._select_server(key, servers)]
            if not servers:
                raise ValueError("当前没有可查询的服务器")
            snapshots = await asyncio.gather(
                *(self._fetch_server_data(server, "manual") for server in servers)
            )
            snapshots = [
                snapshot or unavailable_snapshot(server)
                for server, snapshot in zip(servers, snapshots, strict=True)
            ]
            snapshots = self._allowed_query_snapshots(event, snapshots)
            if not snapshots:
                return
            style = {"图片": "image", "文字": "text"}.get(style, style)
            style = style or (group.query_style if group else "image")
            if style not in {"image", "text"}:
                raise ValueError("样式只能使用 image/图片 或 text/文字")
            quote = await get_hitokoto() if self.display_options.show_hitokoto else ""
            snapshots = self._allowed_query_snapshots(event, snapshots)
            if not snapshots:
                return
            if style == "image":
                started = asyncio.get_running_loop().time()
                try:
                    images = await self.renderer.render_pages(snapshots, self.display_options, quote or "")
                    for index, image in enumerate(images):
                        page = snapshots[index * 4 : index * 4 + 4]
                        if len(self._allowed_query_snapshots(event, page)) != len(page):
                            continue
                        yield event.image_result(image)
                    return
                except Exception as exc:
                    logger.warning(
                        f"查询图片降级 [{event.unified_msg_origin}] "
                        f"servers={','.join(snapshot.key for snapshot in snapshots)} "
                        f"elapsed={asyncio.get_running_loop().time() - started:.2f}s "
                        f"{type(exc).__name__}: {exc}"
                    )
            for index in range(0, len(snapshots), 4):
                page = self._allowed_query_snapshots(event, snapshots[index : index + 4])
                if not page:
                    continue
                text = "\n\n".join(format_server_info(snapshot, self.display_options) for snapshot in page)
                if quote:
                    text += f"\n\n💬 {quote}"
                yield event.plain_result(text)
        except ValueError as exc:
            yield event.plain_result(f"❌ {exc}")

    @filter.command("mc")
    async def mc_command(self, event: AstrMessageEvent, action: str = "帮助", key: str = "", value: str = ""):
        if action == "查询":
            async for result in self._query(event, key, value):
                yield result
            return
        if action == "列表":
            try:
                servers = self._visible_servers(event)
                yield event.plain_result(
                    "本会话可用服务器：\n"
                    + "\n".join(f"{server.key} · {server.server_name}" for server in servers)
                )
            except ValueError as exc:
                yield event.plain_result(f"❌ {exc}")
            return
        if action in {"帮助", "help"}:
            yield event.plain_result(
                "/mc 列表\n/mc 查询 [key] [image/text]\n管理员：/mc 订阅 key、退订 key、默认 key、样式 image/text、通知样式 image/text、通知 all/status/off、本群\n全局：/mc 启动、/mc 停止、/mc 重置"
            )
            return
        if not event.is_admin():
            yield event.plain_result("❌ 此操作仅限 AstrBot 管理员")
            return
        controls = {"启动": self._start_command, "停止": self._stop_command, "重置": self._reset_command}
        if action in controls:
            async for result in controls[action](event):
                yield result
            return
        if not event.get_group_id():
            yield event.plain_result("❌ 请在目标群执行分群设置")
            return
        session = event.unified_msg_origin
        if action == "本群":
            group = self.subscriptions.get(session)
            yield event.plain_result(
                f"UMO: {session}\n订阅: {', '.join(group.server_keys) if group else '无'}"
            )
            return
        try:
            group = self.subscriptions.get(session) or GroupSubscription(session)
            if action == "订阅":
                server = self._select_server(key, self.servers)
                group = replace(group, server_keys=tuple(dict.fromkeys((*group.server_keys, server.key))))
                reply = f"✅ 已订阅 {server.server_name} ({server.key})"
            elif action == "退订":
                server = self._select_server(
                    key, [s for s in self.server_catalog if s.key in group.server_keys]
                )
                group = replace(
                    group,
                    server_keys=tuple(k for k in group.server_keys if k != server.key),
                    default_server="" if group.default_server == server.key else group.default_server,
                )
                reply = f"✅ 已退订 {server.server_name}"
            elif action == "默认":
                selected = (
                    ""
                    if key in {"全部", "all"}
                    else self._select_server(key, [s for s in self.servers if s.key in group.server_keys]).key
                )
                group = replace(group, default_server=selected)
                reply = "✅ 本群默认查询已更新"
            elif action in {"样式", "通知样式"}:
                style = {"图片": "image", "文字": "text"}.get(key, key)
                group = replace(group, **{"query_style" if action == "样式" else "notification_style": style})
                reply = "✅ 本群展示样式已更新"
            elif action == "通知":
                mode = {"全部": "all", "上下线": "status", "关闭": "off"}.get(key, key)
                group = replace(group, notify_mode=mode)
                reply = "✅ 本群通知设置已更新"
            else:
                raise ValueError("未知操作，请输入 /mc 帮助")
            self.subscriptions.put(group)
            yield event.plain_result(reply)
        except ValueError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception as exc:
            logger.error(f"分群设置保存失败 {type(exc).__name__}: {exc}")
            yield event.plain_result("❌ 配置保存失败，已保留原设置，请检查日志")

    async def _start_command(self, event):
        if not self.servers:
            yield event.plain_result("❌ 请先在 WebUI 配置 server_entries")
        else:
            await self._start_monitor()
            yield event.plain_result(f"✅ 监控已启动，共 {len(self.servers)} 台服务器")

    async def _stop_command(self, event):
        await self._stop_monitor()
        yield event.plain_result("✅ 全局监控和待启动任务已停止")

    async def _reset_command(self, event):
        async with self.control_lock:
            running = any(task and not task.done() for task in (self.task, self.startup_task))
            await self._stop_monitor_unlocked()
            self.server_states = build_initial_states(self.servers)
            self.cache.clear()
            self.failures.clear()
            self.offline_counts.clear()
            self.notifications.pending.clear()
            if running:
                await self._start_monitor_unlocked()
        yield event.plain_result("✅ 监控缓存已重置")


__all__ = ["MyPlugin"]
