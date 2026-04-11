import aiohttp
from astrbot.api import logger
from astrbot.api.event.filter import PlatformAdapterType


async def get_hitokoto() -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://v1.hitokoto.cn/?encode=text",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                if response.status == 200:
                    return (await response.text()).strip()
    except Exception as exc:
        logger.warning(f"获取一言失败: {exc}")
    return None


async def notify_group(context, target_group: str | None, message: str) -> bool:
    if not target_group:
        logger.error("target_group 未配置，无法发送通知")
        return False

    try:
        platform = None
        tried_platforms: list[str] = []

        for adapter_name in ("AIOCQHTTP", "QQ_OFFICIAL", "SATORI"):
            adapter_type = getattr(PlatformAdapterType, adapter_name, None)
            if adapter_type is None:
                continue

            tried_platforms.append(adapter_name)
            candidate = context.get_platform(adapter_type)
            if candidate and hasattr(candidate, "get_client"):
                platform = candidate
                logger.info(f"通知发送使用平台适配器: {adapter_name}")
                break

        if not platform:
            logger.error(f"无法获取可用的平台客户端，已尝试: {', '.join(tried_platforms) or '无'}")
            return False

        client = platform.get_client()
        logger.info(f"开始发送群消息到 {target_group}，消息长度: {len(message)}")
        result = await client.api.call_action(
            "send_group_msg",
            group_id=int(target_group),
            message=message,
        )

        if result and result.get("message_id"):
            logger.info(f"群消息发送成功，message_id={result.get('message_id')}")
            return True

        logger.warning(f"发送群消息失败: {result}")
        return False
    except Exception as exc:
        logger.error(f"发送群消息异常: {exc}")
        return False
