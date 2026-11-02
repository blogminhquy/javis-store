"""Paths for the code-pack tests of javis.gate, and server/ of Javis OS on sys.path.

The Gate plugin runs INSIDE a Javis OS server and calls its hub (`mcp_hub.tra_loi_jsonrpc(gate=...)`, Javis
0.440.0), so its tests need a Javis OS checkout next to this repo. CI clones it; locally point JAVIS_OS_DIR at
yours (default: ../javis-os next to this repo).

    JAVIS_OS_DIR=/path/to/javis-os python tests/javis.gate/test_gate.py
"""
import os
import sys
from pathlib import Path

STORE = Path(__file__).resolve().parents[2]
PACK = STORE / "packs" / "javis.gate"
ROOT = Path(os.environ.get("JAVIS_OS_DIR") or (STORE.parent / "javis-os")).resolve()
SERVER = ROOT / "server"
if not (SERVER / "chat_bridge.py").is_file():
    raise SystemExit(f"Javis OS 0.440.0+ checkout not found at {ROOT} (set JAVIS_OS_DIR)")
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
