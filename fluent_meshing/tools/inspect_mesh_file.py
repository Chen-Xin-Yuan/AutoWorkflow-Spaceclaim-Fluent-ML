# -*- coding: utf-8 -*-
"""Inspect an exported mesh file with the isolated .mesh_inspect_deps environment."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys


def main() -> int:
    file = Path(sys.argv[1]).resolve()
    project = Path(__file__).resolve().parents[1]
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    from mesh_validation import inspect_mesh
    result = inspect_mesh(file)
    print(json.dumps({'file': file.name, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
