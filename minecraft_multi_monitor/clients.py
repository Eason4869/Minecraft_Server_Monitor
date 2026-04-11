import asyncio
from datetime import datetime

import aiohttp
from astrbot.api import logger

from .models import ServerConfig, ServerSnapshot


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_player_names(player_list) -> list[str]:
    if isinstance(player_list, str):
        if not player_list.strip() or player_list.strip() == "无":
            return []
        return [name.strip() for name in player_list.split(",") if name.strip()]

    if not isinstance(player_list, list):
        return []

    names: list[str] = []
    for player in player_list:
        if isinstance(player, dict):
            names.append(
                str(
                    player.get("name_clean")
                    or player.get("name")
                    or player.get("username")
                    or "未知玩家"
                )
            )
        else:
            names.append(str(player))
    return names


class MCStatusClient:
    async def fetch_status(self, server: ServerConfig, source: str) -> ServerSnapshot | None:
        api_url = (
            f"https://api.mcstatus.io/v2/status/"
            f"{server.server_type}/{server.server_ip}:{server.server_port}?timeout=20"
        )
        try:
            logger.info(f"[{source}] [{server.server_name}] 使用 mcstatus 查询: {api_url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    api_url,
                    headers={"User-Agent": "MinecraftMultiMonitor/0.1.0"},
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as response:
                    raw_text = await response.text()
                    if response.status != 200:
                        logger.warning(
                            f"[{source}] [{server.server_name}] mcstatus HTTP错误: "
                            f"{response.status}, body={raw_text[:300]}"
                        )
                        return None

                    try:
                        data = await response.json(content_type=None)
                    except Exception as exc:
                        logger.warning(
                            f"[{source}] [{server.server_name}] mcstatus JSON解析失败: "
                            f"{exc}, body={raw_text[:300]}"
                        )
                        return None

                    return self._parse_response(server, data)
        except asyncio.TimeoutError:
            logger.warning(f"[{source}] [{server.server_name}] mcstatus 查询超时")
            return None
        except aiohttp.ClientError as exc:
            logger.warning(f"[{source}] [{server.server_name}] mcstatus 网络错误: {exc}")
            return None
        except Exception as exc:
            logger.error(f"[{source}] [{server.server_name}] mcstatus 查询异常: {exc}")
            return None

    def _parse_response(self, server: ServerConfig, data: dict) -> ServerSnapshot:
        online = bool(data.get("online", False))
        motd_info = data.get("motd", {})
        if isinstance(motd_info, dict):
            motd = motd_info.get("clean") or motd_info.get("raw") or ""
        else:
            motd = str(motd_info or "")

        version_info = data.get("version", {})
        if isinstance(version_info, dict):
            version = str(version_info.get("name", "未知版本"))
            protocol = str(version_info.get("protocol", "未知"))
        else:
            version = str(version_info or "未知版本")
            protocol = "未知"

        players_info = data.get("players", {})
        if isinstance(players_info, dict):
            online_players = int(players_info.get("online", 0) or 0)
            max_players = int(players_info.get("max", 0) or 0)
            player_names = _normalize_player_names(players_info.get("list", []) or [])
        else:
            online_players = 0
            max_players = 0
            player_names = []

        map_info = data.get("map", {})
        map_name = map_info.get("name", "未知") if isinstance(map_info, dict) else "未知"

        return ServerSnapshot(
            key=server.key,
            name=server.server_name,
            server_ip=server.server_ip,
            server_port=server.server_port,
            server_type=server.server_type,
            status="online" if online else "offline",
            motd=motd,
            version=version,
            protocol=protocol,
            online=online_players,
            max_players=max_players,
            players=player_names,
            software=str(data.get("software", "未知")),
            map_name=str(map_name),
            update_time=_now_text(),
            raw_online=online,
            host=f"{server.server_ip}:{server.server_port}",
            delay=None,
            error=None,
        )


class MineBBSClient:
    BASE_URL = "https://motd.minebbs.com/api/status"

    async def fetch_status(self, server: ServerConfig, source: str) -> ServerSnapshot | None:
        query_modes = self._build_query_modes(server)
        snapshots: dict[str, ServerSnapshot] = {}

        for stype in query_modes:
            snapshot = await self._fetch_with_stype(server, source, stype)
            if snapshot is None:
                continue
            snapshots[stype] = snapshot

        preferred = "je" if server.server_type == "java" else "be"
        preferred_snapshot = snapshots.get(preferred)
        auto_snapshot = snapshots.get("auto")

        if preferred_snapshot and preferred_snapshot.status == "online":
            if auto_snapshot and auto_snapshot.players != preferred_snapshot.players:
                logger.info(
                    f"[{source}] [{server.server_name}] MineBBS auto 与 {preferred} 返回的玩家列表不一致，"
                    f"优先采用 {preferred} 结果: auto={auto_snapshot.players}, {preferred}={preferred_snapshot.players}"
                )
            return preferred_snapshot

        if auto_snapshot:
            return auto_snapshot

        if preferred_snapshot:
            return preferred_snapshot

        return None

    def _build_query_modes(self, server: ServerConfig) -> list[str]:
        preferred = "je" if server.server_type == "java" else "be"
        modes = ["auto", preferred]
        unique_modes: list[str] = []
        for mode in modes:
            if mode not in unique_modes:
                unique_modes.append(mode)
        return unique_modes

    async def _fetch_with_stype(self, server: ServerConfig, source: str, stype: str) -> ServerSnapshot | None:
        params = {
            "ip": server.server_ip,
            "port": str(server.server_port),
            "stype": stype,
        }
        try:
            logger.info(
                f"[{source}] [{server.server_name}] 使用 MineBBS 查询: "
                f"{self.BASE_URL} ip={server.server_ip} port={server.server_port} stype={stype}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.BASE_URL,
                    params=params,
                    headers={"User-Agent": "MinecraftMultiMonitor/0.1.0"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    raw_text = await response.text()
                    if response.status != 200:
                        logger.warning(
                            f"[{source}] [{server.server_name}] MineBBS HTTP错误: "
                            f"{response.status}, body={raw_text[:300]}"
                        )
                        return None

                    try:
                        data = await response.json(content_type=None)
                    except Exception as exc:
                        logger.warning(
                            f"[{source}] [{server.server_name}] MineBBS JSON解析失败: "
                            f"{exc}, body={raw_text[:300]}"
                        )
                        return None

                    return self._parse_response(server, data)
        except asyncio.TimeoutError:
            logger.warning(f"[{source}] [{server.server_name}] MineBBS 查询超时, stype={stype}")
            return None
        except aiohttp.ClientError as exc:
            logger.warning(f"[{source}] [{server.server_name}] MineBBS 网络错误: {exc}")
            return None
        except Exception as exc:
            logger.error(f"[{source}] [{server.server_name}] MineBBS 查询异常: {exc}")
            return None

    def _parse_response(self, server: ServerConfig, data: dict) -> ServerSnapshot:
        raw_status = str(data.get("status", "offline")).strip().lower()
        raw_type = str(data.get("type", "")).strip().lower()
        online = raw_status == "online"

        if raw_type == "java":
            normalized_type = "java"
        elif raw_type == "bedrock":
            normalized_type = "bedrock"
        else:
            normalized_type = server.server_type

        players_info = data.get("players", {})
        if isinstance(players_info, dict):
            online_players = int(players_info.get("online", 0) or 0)
            max_players = int(players_info.get("max", 0) or 0)
            player_names = _normalize_player_names(players_info.get("sample", []))
        else:
            online_players = 0
            max_players = 0
            player_names = []

        error = str(data.get("error") or "").strip() or None

        motd_info = data.get("motd", {})
        if isinstance(motd_info, dict):
            motd = str(data.get("pureMotd") or motd_info.get("text") or "")
        else:
            motd = str(data.get("pureMotd") or motd_info or "")

        return ServerSnapshot(
            key=server.key,
            name=server.server_name,
            server_ip=server.server_ip,
            server_port=server.server_port,
            server_type=normalized_type,
            status="online" if online else "offline",
            motd=motd,
            version=str(data.get("version", "未知版本")),
            protocol=str(data.get("protocol", "未知")),
            online=online_players,
            max_players=max_players,
            players=player_names,
            software=str(data.get("type", "未知")),
            map_name="未知",
            update_time=_now_text(),
            raw_online=online,
            host=str(data.get("host") or f"{server.server_ip}:{server.server_port}"),
            delay=int(data.get("delay", 0) or 0) if data.get("delay") is not None else None,
            error=error,
        )


def build_status_client(use_mcbbs_api: bool):
    return MineBBSClient() if use_mcbbs_api else MCStatusClient()
