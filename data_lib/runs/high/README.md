# 高保真运行

SpaceClaim、Fluent Meshing 和以后的求解都写在这里。一次执行的目录是 **`<stage>/<run_id>/`**：

```text
data_lib/runs/high/spaceclaim/g692bcb5d5e50__shared__20261101T101500Z/
data_lib/runs/high/fluent_meshing/g692bcb5d5e50__shared__20261101T102000Z/
data_lib/runs/high/fluent_solution/g692bcb5d5e50__cc9633c86a360__20261101T110500Z/
```

`run_id` 的形状、三段怎么拆、为什么 `stage` 不进去，见 `../README.md`。

| 下级 | 类型 | 作用 |
|---|---|---|
| `spaceclaim/` | 生成 | 建模运行，一次一个 `<run_id>/` |
| `fluent_meshing/` | 生成 | 网格运行，一次一个 `<run_id>/` |
| `fluent_solution/` | 生成 | 求解运行（脚本未实现） |
| `others/` | 生成 | 早前手工探测留下的目录，非新布局产物 |
| `run-20261028…/` 等 | 生成 | **旧布局**（`<run-id>/<阶段>/`）的历史运行，保留作证据，不迁入数据库 |
| `README.md` | 文件 | 本文件 |

## 一次成功运行留下的文件

| 文件 | 说明 |
|---|---|
| `<geometry_id>_spaceclaim.scdoc` | 模型本体 |
| `params.json` | 完整参数快照，含派生值与来源（`sources`） |
| `model.py` | 本次实际执行的空间建模脚本 |
| `result.json` | SpaceClaim 回写的实测数据（外形尺寸、面数、体积…） |
| `run.json` | 运行清单：状态、产物 sha256、逐条验收检查 |
| `<geometry_id>_spaceclaim.log` | 派发器抓取的命令行输出（stdout + stderr） |
| `diagnostic.txt` | 只在脚本内部抛异常时出现 |

失败时整个运行目录会被移到 `feedback/<stage>/<run_id>/`（`run.json` 里会记录 `pathMapping`），
本目录下就只剩成功的运行。是否入库不看这些文件，只看数据库里的 `review` 记录。
