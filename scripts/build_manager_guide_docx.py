"""Deprecated: builds unified guide (same as client docx)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.build_client_guide_docx import build

if __name__ == "__main__":
    path = build()
    print(path)
