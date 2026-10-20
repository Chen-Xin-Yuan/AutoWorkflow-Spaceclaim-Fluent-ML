# 测试范围

本目录没有直接子目录。测试使用 Node.js 内置 `node:test`，从仓库根目录执行：

```powershell
npm ci
npm test
```

| 测试文件 | 主要覆盖 |
| --- | --- |
| [`paths.test.mjs`](paths.test.mjs) | CHM 来源优先级、版本化/通用 AWP、API 版本发现、首次配置 AWP 选择及失败提示。 |
| [`extract.test.mjs`](extract.test.mjs) | 合成 HTML 的元数据与签名提取、sitemap 解析、API 搜索排名。 |
| [`python-map.test.mjs`](python-map.test.mjs) | `inferred` 候选、拉伸配方、圆周阵列与合并 API 的映射；不执行这些 Python 候选。 |
| [`missile-schema.test.mjs`](missile-schema.test.mjs) | 参数声明、默认值、可覆盖项、优化区间、已实现的约定、脚本输入字段及计划中的字段记录。 |
| [`missile-params.test.mjs`](missile-params.test.mjs) | 显式值/预设/默认值优先级、类型转换、未知字段拒绝、硬约束、修正、警告和区域包容关系。 |
| [`missile-geometry.test.mjs`](missile-geometry.test.mjs) | 倒圆相切、剖面、面积/体积/质心、舵面轮廓与站位、相位/片数、模型定位、BOI 和外壳的解析预期。 |
| [`missile-template.test.mjs`](missile-template.test.mjs) | API 版本头、请求文档、参数驱动模板、区域/命名选择/物理抑制等模板内容及可选预览行为。 |
| [`missile-runner.test.mjs`](missile-runner.test.mjs) | 基于构造结果的验收门槛、缺失/过期产物和错误几何的拒绝、安装解析及无需安装的仅生成模式。 |
| [`fixture-acceptance.test.mjs`](fixture-acceptance.test.mjs) | 圆周阵列结果解析、六个角度、合并实体数、非零拓扑，以及未知 fixture 的拒绝。 |
| [`protocol.test.mjs`](protocol.test.mjs) | 无 CHM 时清晰退出；有 CHM 环境时检查 MCP 仅生成状态与证据标签。 |
| [`chm.test.mjs`](chm.test.mjs) | 显式启用后，实际提取本机官方 CHM 并检查缓存复用。 |

`npm test` 的大部分测试使用合成输入、解析几何或临时文件，不需要运行 SpaceClaim。部分用例名称提及历史 SpaceClaim 测量值，当前测试仍是在比较固定期望值，不是新的真机执行。

运行器的非法参数用例也会创建运行记录并尝试将失败产物移至工作区配置的反馈目录；该目录须可写。临时输出与反馈目录之间的移动若触发文件系统或权限错误，用例可能在错误路径的收尾阶段失败，应区别于几何断言失败。

`protocol.test.mjs` 的仅生成 MCP 用例在检测到 `SCDM_CHM` 或非空 `AWP_ROOT*` 环境变量时自动运行；此时 MCP 启动仍需可访问的真实 CHM。仅有用户首次配置不会触发该用例。该用例检查 `build_missile_model` 的 `GENERATED` 和 `generate_model_script` 的 `script-generated`，不启动 SpaceClaim。

真实 CHM 提取用例默认跳过，只有 `SCDM_RUN_REAL_CHM_TEST=1` 时才尝试执行；找不到可用 CHM 仍会跳过。该用例将临时提取缓存写到仓库外。测试结束应查看通过、失败与跳过的数量，不能把跳过视作完成了真实文档检查。

更完整的 CHM/MCP 检查用 `npm run smoke` 或 `npm run verify:real`；真正启动 SpaceClaim 的圆周阵列验收用 `npm run verify:geometry`。各入口的条件见 [`scripts/README.md`](../scripts/README.md)。CI 的 `npm test`、进程返回码或历史结果都不能单独证明当前几何 `runtime-verified`。
