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
        platform = context.get_platform(PlatformAdapterType.AIOCQHTTP)
        if not platform or not hasattr(platform, "get_client"):
            logger.error("无法获取 AIOCQHTTP 客户端")
            return False

        client = platform.get_client()
        result = await client.api.call_action(
            "send_group_msg",
            group_id=int(target_group),
            message=message,
        )

        if result and result.get("message_id"):
            return True

        logger.warning(f"发送群消息失败: {result}")
        return False
    except Exception as exc:
        logger.error(f"发送群消息异常: {exc}")
        return False
