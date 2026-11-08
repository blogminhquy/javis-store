"""The single plugin of the javis.zalo store pack: every Zalo tool the standard MCP lacks.

Why one plugin. Until pack 1.5.1 each new Zalo feature came as a plugin of its own (send images and files, group tools,
read group images, friends), so the Plugins page showed four Zalo entries for what the owner sees as one thing. The
owner asked on 2026-11-08 to stop the fragmentation: one plugin, and new Zalo tools go into it.

How. The code of each former plugin stays as it was, one module per area next to this file:

    send_media.py    zalo_send_image (images and files)
    group.py         members, mentions, group notes, reminders, polls
    read_images.py   zalo_read_images (fetch the photos posted in a chat into the brain)
    friend.py        find people, friend requests, nicknames

Each module keeps its own `register(ctx)` and declares the permission level of every tool it registers
(`min_mode`), so merging changes no tool's level: reading stays readonly, saving photos into the brain stays safe,
anything sent to Zalo stays full. A NEW Zalo feature is a new module here (or a function in the matching one), added
to `_PARTS` and to `tools` in plugin.yaml, never a new plugin folder.

The modules are loaded by path rather than with relative imports, so this works with every Javis loader since 0.73.0
(older ones did not treat a plugin folder as a package).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PARTS = ("send_media", "group", "read_images", "friend")


def _load(part: str):
    name = f"{__name__}__{part}"
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{part}.py")
    if not spec or not spec.loader:
        raise ImportError(f"zalo-extras: cannot load {part}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def register(ctx):
    for part in _PARTS:
        _load(part).register(ctx)
