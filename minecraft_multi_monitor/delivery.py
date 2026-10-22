import asyncio
import time
from dataclasses import dataclass

from astrbot.api import logger

from .models import ServerSnapshot

MAX_ATTEMPTS = 3


@dataclass(slots=True)
class PendingNotification:
    session: str
    server_key: str
    message: str
    snapshot: ServerSnapshot | None
    kind: str
    expires_at: float
    next_attempt_at: float
    attempts: int = 0


def accepts_notification(group, item):
    return (
        group is not None
        and item.server_key in group.server_keys
        and group.notify_mode != "off"
        and (group.notify_mode == "all" or item.kind == "status")
    )


class NotificationQueue:
    """Per-destination latest-event queue. Failures never roll back observed state."""

    def __init__(self):
        self.pending: dict[tuple[str, str], PendingNotification] = {}
        self._semaphore = asyncio.Semaphore(3)

    def enqueue(self, session, server_key, message, snapshot, kind, *, now=None):
        now = time.monotonic() if now is None else now
        key = (session, server_key)
        if key not in self.pending and len(self.pending) >= 200:
            logger.error("通知队列达到 200 项上限，丢弃最旧通知")
            self.pending.pop(next(iter(self.pending)))
        self.pending[key] = PendingNotification(session, server_key, message, snapshot, kind, now + 300, now)

    async def flush(self, send, resolve, *, now=None):
        def current_time():
            return time.monotonic() if now is None else now

        async def deliver(key, item):
            if current_time() >= item.expires_at or not accepts_notification(resolve(item.session), item):
                if self.pending.get(key) is item:
                    self.pending.pop(key)
                return
            if current_time() < item.next_attempt_at:
                return
            async with self._semaphore:
                if self.pending.get(key) is not item:
                    return
                if current_time() >= item.expires_at or not accepts_notification(resolve(item.session), item):
                    self.pending.pop(key)
                    return
                started = time.monotonic()
                try:
                    # Quote <=5s, render <=35s, image send <=15s, final text <=15s.
                    sent = await asyncio.wait_for(send(item), timeout=75)
                except Exception as exc:
                    logger.warning(
                        f"通知异常 [{item.session}] [{item.server_key}] stage=delivery "
                        f"attempt={item.attempts + 1}/{MAX_ATTEMPTS} "
                        f"elapsed={time.monotonic() - started:.2f}s {type(exc).__name__}: {exc}"
                    )
                    sent = False
            if self.pending.get(key) is not item:
                return
            item.attempts += 1
            if sent or item.attempts >= MAX_ATTEMPTS:
                self.pending.pop(key)
                if not sent:
                    logger.error(f"通知发送失败，已尝试三次 [{item.session}] [{item.server_key}]")
            else:
                delay = 5 if item.attempts == 1 else 15
                item.next_attempt_at = current_time() + delay
                logger.warning(
                    f"通知等待重试 [{item.session}] [{item.server_key}] "
                    f"attempt={item.attempts}/{MAX_ATTEMPTS} wait={delay}s"
                )

        await asyncio.gather(*(deliver(key, item) for key, item in list(self.pending.items())))
