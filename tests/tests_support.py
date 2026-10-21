import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


class Config(dict):
    saved = False

    def save_config(self):
        self.saved = True


class Event:
    def __init__(self, admin=False, umo="bot:GroupMessage:123", group="123"):
        self.admin = admin
        self.unified_msg_origin = umo
        self.group = group

    def is_admin(self):
        return self.admin

    def get_group_id(self):
        return self.group

    def plain_result(self, text):
        return text

    def image_result(self, path):
        return "IMAGE:" + path


def make_plugin(auto=False):
    if "test_plugin_package" not in sys.modules:
        package = types.ModuleType("test_plugin_package")
        package.__path__ = [str(Path(__file__).resolve().parents[1])]
        sys.modules[package.__name__] = package
    MyPlugin = importlib.import_module("test_plugin_package.main").MyPlugin
    config = Config(
        enable_auto_monitor=auto,
        group_entries=[],
        server_entries=[
            {"key": "s", "server_name": "生存服", "server_ip": "a.example"},
            {"key": "hidden", "server_name": "隐藏服", "server_ip": "b.example"},
        ],
    )
    plugin = MyPlugin(SimpleNamespace(), config)
    plugin.status_client.fetch_status = AsyncMock(return_value=None)
    return plugin
