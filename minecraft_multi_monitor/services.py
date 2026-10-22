import asyncio

import aiohttp
from astrbot.api import logger
from astrbot.api.event import MessageChain


async def get_hitokoto() -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://v1.hitokoto.cn/?encode=text", timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    return (await response.text())[:200].strip()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning(f"获取一言失败 {type(exc).__name__}: {exc}")
    return None


async def notify_session(context, session: str, message: str, image: str | None = None) -> bool:
    started = asyncio.get_running_loop().time()
    stage = "image_send" if image else "text_send"
    try:
        chain = MessageChain().message(message)
        if image:
            if image.startswith(("http://", "https://")):
                chain.url_image(image)
            else:
                chain.file_image(image)
        # Framework success means the matching adapter accepted the send call.
        # It cannot prove delivery to a remote user's client.
        sent = bool(await asyncio.wait_for(context.send_message(session, chain), timeout=15))
        if sent:
            return True
        reason = "适配器返回 False，未确认接收消息"
    except Exception as exc:
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"
    logger.warning(
        f"通知发送失败 [{session}] stage={stage} "
        f"elapsed={asyncio.get_running_loop().time() - started:.2f}s reason={reason}"
    )
    return False
