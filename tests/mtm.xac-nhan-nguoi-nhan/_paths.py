"""Paths for the code-pack tests of mtm.xac-nhan-nguoi-nhan, and server/ of Javis OS on sys.path.

The plugin runs INSIDE a Javis OS server through the `chatbot_turn_context` hook, so the end-to-end part of
the test needs a Javis OS checkout next to this repo. CI clones it; locally point JAVIS_OS_DIR at yours
(default: ../javis-os next to this repo).

    JAVIS_OS_DIR=/path/to/javis-os python tests/mtm.xac-nhan-nguoi-nhan/test_xac_nhan_nguoi_nhan.py
"""
import os
import sys
from pathlib import Path

STORE = Path(__file__).resolve().parents[2]
PACK = STORE / "packs" / "mtm.xac-nhan-nguoi-nhan"
PLUGIN = PACK / "plugins" / "xac-nhan-nguoi-nhan"
ROOT = Path(os.environ.get("JAVIS_OS_DIR") or (STORE.parent / "javis-os")).resolve()
SERVER = ROOT / "server"
if not (SERVER / "plugins_host.py").is_file():
    raise SystemExit(f"Javis OS checkout not found at {ROOT} (set JAVIS_OS_DIR)")
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
