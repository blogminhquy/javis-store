"""Paths for the code-pack tests of javis.zalo, and server/ of Javis OS on sys.path.

The plugins in this pack run INSIDE a Javis OS server and import its modules (`zalo_cli`, `image_gen`,
`image_vision`, `conversations`), so their tests need a Javis OS checkout next to this repo. CI clones it;
locally point JAVIS_OS_DIR at yours (default: ../javis-os next to this repo).

    JAVIS_OS_DIR=/path/to/javis-os python tests/javis.zalo/test_zalo_read_images.py
"""
import os
import sys
from pathlib import Path

STORE = Path(__file__).resolve().parents[2]
PACK = STORE / "packs" / "javis.zalo"
ROOT = Path(os.environ.get("JAVIS_OS_DIR") or (STORE.parent / "javis-os")).resolve()
SERVER = ROOT / "server"
if not (SERVER / "zalo_cli.py").is_file():
    raise SystemExit(f"Javis OS checkout not found at {ROOT} (set JAVIS_OS_DIR)")
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
