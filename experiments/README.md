# 保留的独立研究项目

本目录只存放不参与工作区主流程、且尚未验证的研究代码。

| 下级 | 类型 | 作用 |
|---|---|---|
| `autoCFD/` | 目录 | 保留的研究代码，独立 Git 仓库，含 `src/`、`Benchmark/`、`assets/`、`.vscode/`、`run.sh`、`LICENSE` 和自身 `README.md` |

`experiments/autoCFD` 本轮不调试，且**未验证为本工作区的求解器**。该项目面向 OpenFOAM 与"自然语言到算例"的转换，与本工作区的 ANSYS 流程不是同一套求解器；工作区的求解入口保留在 `fluent_solution`。它的研究结论不能作为本工作区任何阶段的验收依据。

子项目按自身的 README 说明使用；工作区的统一入口不调用本目录。
