"""Paths for the code-pack tests of javis.facebook-monitor, and server/ of Javis OS on sys.path.

The plugin in this pack runs INSIDE a Javis OS server and imports its modules (`mcp_store`), so its tests need a
Javis OS checkout next to this repo. CI clones it; locally point JAVIS_OS_DIR at yours (default: ../javis-os next
to this repo).

    JAVIS_OS_DIR=/path/to/javis-os python tests/javis.facebook-monitor/test_fb_monitor.py
"""
import os
import sys
from pathlib import Path

STORE = Path(__file__).resolve().parents[2]
PACK = STORE / "packs" / "javis.facebook-monitor"
ROOT = Path(os.environ.get("JAVIS_OS_DIR") or (STORE.parent / "javis-os")).resolve()
SERVER = ROOT / "server"
if not (SERVER / "mcp_store.py").is_file():
    raise SystemExit(f"Javis OS checkout not found at {ROOT} (set JAVIS_OS_DIR)")
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
