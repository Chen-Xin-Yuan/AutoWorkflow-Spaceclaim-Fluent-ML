# -*- coding: utf-8 -*-
"""Inspect an exported mesh file with the isolated .mesh_inspect_deps environment."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys


# ---------------------------------------------------------------------------
# 输入 / 输出 / 参数配置
# ---------------------------------------------------------------------------
# 输入：第 1 个位置参数为必填网格文件，按调用时工作目录解析；无默认文件。
# 输出：仅向标准输出打印 JSON，不写输出文件；实际文件读取和内存结果由
# mesh_validation.inspect_mesh() 完成，其验收配置在该模块前部。
# 解释器与依赖路径由 mesh_check.cmd 或 workspace_runtime.py 的配置提供，
# 本脚本只在 main() 中按 __file__ 定位仓库，不固定每次运行的输入路径。


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
