# Workspace rules

## 文件前部的路径与参数配置

- 所有自有代码涉及可修改的输入文件、输入文件夹、输出文件、输出文件夹或运行参数时，必须在文件前部设置清晰的“输入 / 输出 / 参数配置”区，集中定义实际使用的默认值、文件名或路径模板，方便像修改参数一样修改路径。不要只写说明而把可修改的默认值继续散落在函数或执行逻辑中。
- 配置区放在必要的 shebang、编码声明、模块说明、`from __future__` 和 import 之后、主要函数 / 类及执行逻辑之前；配置所依赖的脚本根路径须先定义。已有位于文件前部的参数表或配置类可以继续作为唯一来源，不重复维护第二份默认值。
- 文件前部说明输入输出的用途、相对路径基准、动态命名规则，以及命令行、环境变量、`workspace.json` 和本地配置等覆盖入口与优先级。必填输入不得为了集中配置而虚构默认文件；纯转发入口或无磁盘 I/O 的模块应明确说明实际配置来源或“无文件输入输出”。
- 按运行 ID、调用参数或用户选择产生的路径仍在运行时解析；不要因前置配置而固定运行目录、提前读写文件或改变原有覆盖优先级。输出路径、日志 / 报告文件名和可调参数的使用处应引用前部定义，避免多处修改。
- 保持脚本相对路径和 `workspace.json` 约定，不将本机安装路径、凭据、依赖 / 生成 / 缓存目录内容写入配置区。协议固定名称和内部常量应标明用途，不能误标为可任意修改的用户参数。
- 后续新增或修改代码时同时遵守此布局，并同步适用的 README；仅整理配置位置时保留原有数值、参数含义、算法和运行行为。

## 工作区边界

- Preserve `DATCOM`: do not read, enumerate, edit, move or delete its contents.
- `spaceclaim` and `fluent_meshing` are independent repositories. Read each repository's own instructions before editing it.
- Keep folder structure and `README.md` files in sync. Any change that adds, removes, renames or moves a directory must update the corresponding `README.md` in the same change, so that it lists that directory's direct children and their purpose. The workspace root covers the top level and one level below; every other directory documents its own direct children.
- A structure change without its matching README update is incomplete work. A README entry that no longer matches the tree is a defect, not documentation debt. Dependency, generated and cache directories are documented too, marked as not version-controlled and not to be edited by hand.
- Use `workspace.json` and script-relative paths. Discover ANSYS through AWP variables or its supported local setup configuration; keep installation paths and credentials out of source and reports.
- Keep runtime models, meshes and logs outside code repositories. Write high-fidelity records under `data_lib/runs/high/<run-id>/<stage>/` and low-fidelity DATCOM archives under `data_lib/runs/low/<geometry_id>/`; retain failed artifacts under `feedback`. Geometry is 14 parameters: 11 length dimensions in `data_lib/inputs/cases.csv`, plus three shared fin-section parameters. The section type is switchable: the default hexagon uses `thickness_percent`, `flat_percent`, and `le_radius` (DATCOM `SECTYP = HEX`); the NACA option uses `thickness_percent`, `le_radius_index`, and `max_thickness_tenths` (DATCOM `NACA-1-4-...`). Every shape parameter that is deliberately NOT in the input table lives in ONE module, `DATCOM/scripts/missile_spec.py`, whose front section is the single configuration area: the three fin-section parameters, the nose shape (`NOSE_SHAPE = "ogive"` writes `TNOSE = OGIVE`, `"cone"` writes `TNOSE = CONE`; the default when no `TNOSE` card is written is the tangent ogive, not a cone), the point-cloud-only tip fillet, the fin layout (`LE_MODE`, `FIN_PHASE`, `CRUCIFORM_COUNT`), and the baseline geometry; the rest of that module holds the nose profile and fin-section equations. `DATCOM/scripts/01_batch_datcom.py` reads the table and calls once per `geometry_id` plus `altitude_m`. `DATCOM/scripts/04_pointnet_demo.py` writes point clouds to `data_lib/datasets/point_clouds/<geometry_id>/` (one folder per geometry, the folder name is the identifier; a run without an identifier is a demo and is written flat into the root directory) from `--geometry-id` or `--params` for the 11 lengths, and takes the same airfoil section and the same nose shape from that module. SpaceClaim uses the same three fin-section parameters, plus `k_nose` and `k_tip`. Identifier encoding, confidence, added-point range, final point-cloud format, the solver, and post-report adjustment authority stay undecided.
- Existing historical records are evidence snapshots, not fresh acceptance results.
- Preserve dependency versions and Git/untracked content during migration. `_deps` and `.mesh_inspect_deps` use different Python/NumPy environments and must not be combined.
- Validate resolved absolute paths before filesystem moves or removals. Use a complete verified snapshot for rollback, preserving newer files rather than overwriting them.
- `fluent_solution` and `ML_training` are reserved modules. `experiments/autoCFD` is retained research code and has not been validated as this workspace's solver.
- Keep SpaceClaim MCP stdout exclusively JSON-RPC. External MCP registration is outside this migration.
