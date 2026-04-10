import os
import sys


PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


from minecraft_multi_monitor import MyPlugin

__all__ = ["MyPlugin"]
