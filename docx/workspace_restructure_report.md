# 工作区重构验收记录

记录日期：2026-10-14，Asia/Shanghai。

迁移、工作流接口修复和两个程序的通用真实验收已完成。经用户授权关闭占用目录的 DeepSeek 本地服务后，旧 `mcp_skill` 空目录树已移除。

## 迁移与保留

- 完整备份：`../archive/migration-20261014T084018Z/`。
- 备份及迁移分别逐文件验证了 45,465 个文件；清单包含原路径、目标路径、大小和 SHA-256。
- 历史模型、网格和配套记录迁入 `../data_lib/legacy/`；交付前再次核对 36 个历史和原输入文件的校验值，全部匹配。
- 旧网格目录因被占用而保留的原副本已归档到 `../archive/retired-originals-20261014T132640Z/Fluent_meshing_mcp/`。旧位置现已没有代码、依赖或数据文件。
- SpaceClaim、Fluent Meshing、Fluent_skill 和 autoCFD 各自 Git 信息及原未跟踪内容均保留；没有建立根 Git 仓库或提交、推送代码。
- `missile_project` 内容未读取、枚举、编辑、移动或删除。外部 MCP 注册未修改。

## 真实验收

| 项目 | 验收结果 | 新运行 |
|---|---|---|
| SpaceClaim 圆周阵列 | 六个规定角度，360° 覆盖，合并后 1 个实体、20 个面，新模型和 PASS 记录 | `../data_lib/runs/run-20261014090017-60f0082d/spaceclaim/` |
| 独立长方体输入 | 1 实体、6 面，100×50×50 mm，体积 250000 mm³，inlet/outlet/wall 面数 1/1/4 | `../data_lib/runs/run-20261014125026-9ddde5e0/spaceclaim/` |
| Fluent 通用网格 | 634 单元、4072 面、3723 节点，3D/mm，1 个实际流体区，边界非空 | `../data_lib/runs/run-20261014131553-bd8c9f7c/fluent_meshing/` |

Fluent 最小单元体积为 78.4829 mm³，总体积为 250000 mm³；最小正交质量为 0.6171749502，大于 0.01 门槛。质量报告覆盖全部 634 个导出单元；HDF5 包围盒为 (0,0,0) 至 (100,50,50) mm。网格检查完成且未报告错误，本次会话已关闭。

Fluent 24.1 的 Update Regions 在没有显式更改时返回空参数状态。当前验收读取实际 cell-zone 报告，而不是将空参数当作无流体区。其质量接口返回明确的单元数、最小值、最大值和平均值；当前验证使用本次直接查询结果及本次检查日志。

## 接口与异常测试

- SpaceClaim：锁文件安装完成；105 项测试中 104 项通过、1 项跳过；MCP smoke 通过。
- 根 MCP 启动器：从项目目录之外成功完成 stdio 握手、7 个工具列表和官方 API 查询；标准输出只透传 JSON-RPC。
- Fluent：11 项测试通过，覆盖输入缺失、启动失败、检查失败、超时、零退出码但缺少证据、主动停止及会话关闭。
- 历史 workflow：比较 17 个任务，计算参数差异为 0；原参数常量与完整备份的静态比较通过。
- 两个命令行入口均从其他目录验证；支持原有选项，Fluent 新增显式输入、独立运行目录和通用测试 profile。
- 失败数据实际保存在 `../feedback/`。短暂目录占用时保留真实反馈副本并写转向记录，索引不会重复统计。
- 依赖版本清单：`dependency-inventory.json`；两个 Python 依赖树保持隔离。Python 选择保存在被 Git 忽略的本地配置中，源码不固定本机安装路径。

## 使用与边界

入口和目录约定见 [工作区说明](../README.md)，数据来源见 `../data_lib/index.json`。自动求解和机器学习本轮只建立预留模块。autoCFD 完整保留到 experiments，本轮未调试。

历史专用案例仅进行了文件、元数据和参数静态回归；本次真实运行使用通用测试件，未验证自动建模到网格的交接。

## 旧入口清理

用户已明确授权关闭占用目录的 DeepSeek dsh 本地服务。核对进程名称、服务入口和启动时间后，仅停止该进程；确认旧目录没有文件或重解析点后，移除了旧 `mcp_skill/Fluent_meshing_mcp` 和 `mcp_skill` 空目录。数据、工作流文件和完整归档备份均保留，日常入口位于新目录。

## 功能恢复（2026-10-15，Asia/Shanghai）

按归档基线（`../archive/migration-20261014T084018Z/`）逐项核对并恢复了两个功能，未整体覆盖目录。

**快照**：`../archive/snapshot-pre-recovery-20261015T084726Z/`，包含两个仓库（Git bundle、未跟踪文件、本地配置、依赖）、根入口与说明，45,355 个文件、345.1 MB，逐文件 SHA-256 复核 0 缺失、0 不一致。`missile_project`、`archive`、`data_lib`、`feedback` 未参与复制。

**精简**：删除了方盒演示生成器（`spaceclaim/scripts/spaceclaim-box-smoke.py`）、`verify-box` 分支、`box-smoke` profile、方盒评估分支、对应测试与 `fluid_box` 输入；圆周阵列验收保留。根入口去掉 `verify-box`，`verify-meshing` 改为必须显式 `-Geometry` 并走原专用 recorded 工作流。

**建模入口**：`spaceclaim/scripts/missile.mjs` 保留参数文件、命令行和 MCP 三种输入；`../data_lib/inputs/missile_params_example.json` 只填写图中 11 个主尺寸。仅生成脚本的记录为 `GENERATED` 且 `runtimeVerified=false`，MCP 的 `build_missile_model` 在 `execute:false` 时同样返回 `GENERATED` 与 generated-only 证据标签。运行记录与失败包改由工作区配置解析，不再依赖调用者的临时目录。

**网格交接**：`mesh_missile.py` 只保留原专用 7 步流程；导入后核验必需面标签，区域与边界层区域名称必须唯一匹配，缺失或多义直接报 `MappingError`，不再回退到猜测的名字。Fluent 24.1 在体网格步骤不回报区域清单，改为从实际网格读回 cell zone 名称后确定区域尺寸作用对象。导出的 `.msh.h5` 由独立环境（`.mesh_inspect_deps_313` + `.mesh-inspect-python`，CPython 3.13 的 h5py）核验非空、三维、mm、区域与边界组，并与质量报告交叉核对；两套 Python 依赖继续隔离。

**本次真实验收**（全部为新运行，历史记录未作为依据）：

| 项目 | 结果 | 新运行 |
|---|---|---|
| 建模基准参数（11 项示例） | 45/45 项通过，`runtimeVerified` | `../data_lib/runs/run-20261015084402-5a5e5cb4/spaceclaim/` |
| 建模变参（`k_nose=0.008`、`L_W=1.6`、`B_W=0.26`、`C_TW=0.05`） | 45/45 项通过；`r_f` 18.45→29.52 mm、展向 0.64→0.70 m 与解析值一致 | `../data_lib/runs/run-20261015085503-05cf96e2/spaceclaim/` |
| 圆周阵列 | 6 体、60° 间隔、360° 覆盖、合并 1 体 20 面 | `../data_lib/runs/run-20261015090223-e9abcd29/spaceclaim/` |
| 新基准模型的全流程网格 | `PASSED` + `runtimeVerified`；2,495,263 单元、3D/mm、最小正交质量 0.05711706、最小体积 0.0318 mm³ | `../data_lib/runs/run-20261015095424-25ecc5b8/fluent_meshing/` |

网格证据：导出文件 547,815,433 字节，cell zone `zone----enclosure----`（2,495,263 单元），面区域
`inlet` 346 / `outlet` 346 / `wall` 5,276 / `missile-zone----enclosure----_43` 35,204 / interior 15,418,399；
质量报告覆盖全部导出单元。`tools/compare_with_reference.py` 对比本次 workflow 与历史参考：**17 个任务全部一致，真实差异 0**。
仅生成脚本的运行记录为 `GENERATED`（`../data_lib/runs/run-20261015100425-0979edb8/spaceclaim/`），不作为验收通过。

**入口核对**：`verify-meshing` 不传 `-Geometry` 直接报错；`run_meshing.cmd` 不传 `--geometry` 退出码 2；`verify-box` 不再是合法入口；
圆周阵列验收、MCP smoke（107 通过 / 1 跳过）、SpaceClaim 108 项测试（107 通过 / 1 跳过）、Fluent 25 项测试全部通过。
