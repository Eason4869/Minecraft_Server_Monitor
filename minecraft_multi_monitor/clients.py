import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp
from astrbot.api import logger

from .config import safe_int
from .models import ServerConfig, ServerSnapshot


def _now_text(timestamp=None) -> str:
    try:
        moment = (
            datetime.fromtimestamp(float(timestamp) / 1000, timezone.utc).astimezone()
            if timestamp is not None
            else datetime.now().astimezone()
        )
    except (TypeError, ValueError, OverflowError, OSError):
        moment = datetime.now().astimezone()
    return moment.strftime("%Y-%m-%d %H:%M:%S %z")


def _normalize_player_names(value) -> list[str]:
    if isinstance(value, str):
        value = [] if value.strip() in ("", "无") else value.split(",")
    if not isinstance(value, list):
        return []
    names = []
    for player in value[:100]:
        if isinstance(player, dict):
            player = player.get("name_clean") or player.get("name") or player.get("username") or ""
        name = str(player or "").strip()[:64]
        if name and name not in names:
            names.append(name)
    return names


def unavailable_snapshot(server: ServerConfig, error="查询源不可用，请稍后重试") -> ServerSnapshot:
    return ServerSnapshot(
        server.key,
        server.server_name,
        server.server_ip,
        server.server_port,
        server.server_type,
        "unknown",
        "",
        "未知版本",
        "",
        0,
        0,
        [],
        "",
        "",
        _now_text(),
        False,
        error=error,
    )


class JsonClient:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def _get_json(self, url, server, source, *, params=None):
        started = asyncio.get_running_loop().time()
        try:
            if self.session is None or self.session.closed:
                self.session = aiohttp.ClientSession(headers={"User-Agent": "EasonClawMC/0.3.0"})
            async with self.session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status != 200:
                    logger.warning(f"[{source}] [{server.key}] 查询 HTTP {response.status}")
                    return None
                # Limit upstream payloads, including a server-provided icon/MOTD.
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > 512 * 1024:
                        raise ValueError("响应超过 512 KiB")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("API 响应不是对象")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            elapsed = asyncio.get_running_loop().time() - started
            logger.warning(
                f"[{source}] [{server.key}] 查询失败 {type(exc).__name__}: {exc}，耗时 {elapsed:.1f}s"
            )
            return None


class MCStatusClient(JsonClient):
    BASE_URL = "https://api.mcstatus.io/v2/status"

    async def fetch_status(self, server: ServerConfig, source: str) -> ServerSnapshot | None:
        host = server.server_ip
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        address = quote(f"{host}:{server.server_port}", safe="[]:")
        data = await self._get_json(
            f"{self.BASE_URL}/{server.server_type}/{address}", server, source, params={"timeout": "5"}
        )
        if data is None:
            return None
        try:
            return self._parse_response(server, data)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning(f"[{source}] [{server.key}] mcstatus 解析失败 {type(exc).__name__}: {exc}")
            return None

    def _parse_response(self, server: ServerConfig, data: dict) -> ServerSnapshot:
        if not isinstance(data.get("online"), bool):
            raise ValueError("缺少布尔 online 字段")
        online = data["online"]
        motd_info = data.get("motd") or {}
        motd = (
            motd_info.get("clean") or motd_info.get("raw") or ""
            if isinstance(motd_info, dict)
            else str(motd_info)
        )
        version_info = data.get("version") or {}
        if isinstance(version_info, dict):
            version = str(
                version_info.get("name_clean")
                or version_info.get("name")
                or version_info.get("name_raw")
                or "未知版本"
            )
            protocol = str(version_info.get("protocol") or "未知")
        else:
            version, protocol = str(version_info), "未知"
        players = data.get("players") or {}
        if not isinstance(players, dict):
            players = {}
        return ServerSnapshot(
            key=server.key,
            name=server.server_name,
            server_ip=server.server_ip,
            server_port=server.server_port,
            server_type=server.server_type,
            status="online" if online else "offline",
            motd=str(motd)[:1000],
            version=version[:200],
            protocol=protocol,
            online=max(0, safe_int(players.get("online"), 0)) if online else 0,
            max_players=max(0, safe_int(players.get("max"), 0)) if online else 0,
            players=_normalize_player_names(players.get("list")) if online else [],
            software=str(data.get("software") or "未知"),
            map_name="未知",
            update_time=_now_text(data.get("retrieved_at")),
            raw_online=online,
            source="mcstatus.io",
            icon=str(data.get("icon") or ""),
        )


class MineBBSClient(JsonClient):
    BASE_URL = "https://motd.minebbs.com/api/status"

    async def fetch_status(self, server: ServerConfig, source: str) -> ServerSnapshot | None:
        return await self._fetch_with_stype(server, source, "je" if server.server_type == "java" else "be")

    async def _fetch_with_stype(self, server, source, stype):
        data = await self._get_json(
            self.BASE_URL,
            server,
            source,
            params={
                "ip": server.server_ip,
                "port": str(server.server_port),
                "stype": stype,
            },
        )
        if data is None:
            return None
        try:
            return self._parse_response(server, data)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning(f"[{source}] [{server.key}] MineBBS 解析失败 {type(exc).__name__}: {exc}")
            return None

    def _parse_response(self, server: ServerConfig, data: dict) -> ServerSnapshot:
        status = str(data.get("status") or "").strip().lower()
        if status not in {"online", "offline"}:
            raise ValueError("缺少有效 status 字段")
        online = status == "online"
        players = data.get("players") or {}
        if not isinstance(players, dict):
            players = {}
        motd_info = data.get("motd") or {}
        motd = (
            data.get("pureMotd")
            or (motd_info.get("text") if isinstance(motd_info, dict) else str(motd_info))
            or ""
        )
        return ServerSnapshot(
            key=server.key,
            name=server.server_name,
            server_ip=server.server_ip,
            server_port=server.server_port,
            server_type=server.server_type,
            status=status,
            motd=str(motd)[:1000],
            version=str(data.get("version") or "未知版本")[:200],
            protocol=str(data.get("protocol") or "未知"),
            online=max(0, safe_int(players.get("online"), 0)) if online else 0,
            max_players=max(0, safe_int(players.get("max"), 0)) if online else 0,
            players=_normalize_player_names(players.get("sample")) if online else [],
            software=str(data.get("type") or "未知"),
            map_name="未知",
            update_time=_now_text(),
            raw_online=online,
            delay=safe_int(data.get("delay"), 0) if data.get("delay") is not None else None,
            error=str(data.get("error") or "")[:200] or None,
            source="MineBBS",
        )


class StatusClient:
    def __init__(self, use_mcbbs_api: bool, enable_fallback: bool = True):
        self.primary = MineBBSClient() if use_mcbbs_api else MCStatusClient()
        self.secondary = MCStatusClient() if use_mcbbs_api else MineBBSClient()
        self.enable_fallback = enable_fallback

    async def fetch_status(self, server, source):
        snapshot = await self.primary.fetch_status(server, source)
        if snapshot is None and self.enable_fallback:
            snapshot = await self.secondary.fetch_status(server, source)
        return snapshot

    async def close(self):
        await asyncio.gather(self.primary.close(), self.secondary.close())


def build_status_client(use_mcbbs_api: bool, enable_fallback: bool = True):
    return StatusClient(use_mcbbs_api, enable_fallback)
