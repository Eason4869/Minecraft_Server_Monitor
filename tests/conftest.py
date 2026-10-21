"""Small AstrBot boundary double; business logic and aiohttp run unmodified."""

import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def module(name, **attrs):
    obj = types.ModuleType(name)
    obj.__dict__.update(attrs)
    sys.modules[name] = obj
    return obj


class Filters:
    def command(self, *args, **kwargs):
        return lambda function: function


class MessageChain:
    def __init__(self):
        self.chain = []

    def message(self, text):
        self.chain.append(("text", text))
        return self

    def url_image(self, url):
        self.chain.append(("image", url))
        return self

    def file_image(self, path):
        self.chain.append(("image", path))
        return self


class Star:
    def __init__(self, context):
        self.context = context


module("astrbot")
module("astrbot.api", AstrBotConfig=dict, logger=logging.getLogger("tests"))
module("astrbot.api.event", AstrMessageEvent=object, filter=Filters(), MessageChain=MessageChain)
module("astrbot.api.event.filter", PlatformAdapterType=types.SimpleNamespace(AIOCQHTTP=1))
module("astrbot.api.star", Context=object, Star=Star, register=lambda *a: lambda cls: cls)
