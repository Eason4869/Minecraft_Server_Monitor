from copy import deepcopy
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class GroupSubscription:
    session: str
    server_keys: tuple[str, ...] = ()
    default_server: str = ""
    query_style: str = "image"
    notification_style: str = "text"
    notify_mode: str = "all"


class SubscriptionStore:
    def __init__(self, config, server_keys: set[str]):
        self.config = config
        self.server_keys = server_keys
        self.groups = {}
        entries = config.get("group_entries", [])
        if not isinstance(entries, list):
            raise ValueError("group_entries 必须是列表")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("群订阅必须是对象")
            if not isinstance(entry.get("server_keys", []), list):
                raise ValueError("server_keys 必须是服务器 key 列表")
            group = GroupSubscription(
                session=str(entry.get("session") or "").strip(),
                server_keys=tuple(entry.get("server_keys") or ()),
                default_server=str(entry.get("default_server") or ""),
                query_style=entry.get("query_style", "image"),
                notification_style=entry.get("notification_style", "text"),
                notify_mode=entry.get("notify_mode", "all"),
            )
            self._validate(group)
            if group.session in self.groups:
                raise ValueError(f"群会话重复: {group.session}")
            self.groups[group.session] = group

    def _validate(self, group):
        parts = group.session.split(":", 2)
        if len(parts) != 3 or not parts[0] or parts[1] != "GroupMessage" or not parts[2]:
            raise ValueError("群会话必须是完整 UMO：平台实例ID:GroupMessage:群ID")
        if any(not isinstance(key, str) or key not in self.server_keys for key in group.server_keys):
            raise ValueError("群订阅引用了不存在的服务器 key")
        if group.default_server and group.default_server not in group.server_keys:
            raise ValueError("默认服务器必须在本群订阅中")
        if group.query_style not in {"image", "text"} or group.notification_style not in {"image", "text"}:
            raise ValueError("展示样式必须是 image 或 text")
        if group.notify_mode not in {"all", "status", "off"}:
            raise ValueError("通知类型必须是 all、status 或 off")

    def get(self, session):
        return self.groups.get(session)

    def put(self, group: GroupSubscription):
        self._validate(group)
        groups = dict(self.groups)
        groups[group.session] = group
        old_entries = deepcopy(self.config.get("group_entries", []))
        entries = []
        for item in groups.values():
            entry = asdict(item)
            entry["__template_key"] = "group_subscription"
            entry["server_keys"] = list(item.server_keys)
            entries.append(entry)
        self.config["group_entries"] = entries
        try:
            self.config.save_config()
        except Exception:
            self.config["group_entries"] = old_entries
            raise
        self.groups = groups
