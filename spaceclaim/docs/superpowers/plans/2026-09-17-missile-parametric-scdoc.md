# 导弹参数化建模 → SCDoc 生成方案

> **状态：Task 0–3 已实现并在本机 SCDM 2024 R1 上真机验收通过（见第十一节）。** Task 4（批量扫参）尚未实现。
> **For agentic workers:** 按任务清单逐项实现，每项完成后回填 `- [x]`，并保持证据标签一致。

**Goal:** 让使用者输入一组几何参数（13 个，**每一个都可改**），即可自动生成一个导弹几何的 `.scdoc` 文件，并给出可核查的验收证据。

**Architecture:** 四段式。①一份**参数 schema 作为唯一真源**，同时驱动 MCP 工具入参、命令行开关、`params.json` 模板与优化区间文件；②Node 侧做参数合并、校验与文件生成；③仓库内维护**一份固定的参数化模板脚本** `model.py`；④Node 启动本机 SpaceClaim 以 headless 方式执行模板脚本，脚本读 JSON、建模、`DocumentSave.Execute` 落盘，并写回 `result.json`。`.scdoc` 由 SpaceClaim 生成，Node 侧不直接写二进制。

**Tech Stack:** Node.js 18+（ESM，`node:child_process`）、MCP stdio server、ANSYS SpaceClaim/SCDM 脚本 API（IronPython，API Version 与所选 `AWP_ROOT<number>` 版本一致）。

## Global Constraints

- 不修改 `python-map.mjs` 中既有的意图模板表；新能力落在独立模块中。
- **所有几何数值都必须来自参数集**：模板脚本与生成器内不得出现裸数值常量（含 `t_fin`、`root_overlap`、`fin_phase` 等约定量）。参数改动的唯一入口是参数集，不是改脚本。
- **参数字段只定义一次**：字段名 / 单位 / 默认值 / 区间只在 `lib/missile-schema.mjs` 中声明，MCP 入参、CLI、JSON 模板、校验器均由它派生。
- 不把机器专属 ANSYS 安装路径、CHM 内容、模型文件、运行日志写入源码、测试、README 或 fixtures。
- SpaceClaim 可执行文件一律通过 `AWP_ROOT<number>` / 通用 `AWP_ROOT` / 首次配置解析，禁止硬编码。
- 生成产物（`params.json`、`model.py` 副本、`.scdoc`、`result.json`）写入仓库**之外**的工作目录。
- 证据标签必须严格区分：`official-doc` / `inferred` / `runtime-verified`。
- **进程返回码 0、HTTP 成功、缓存命中都不能作为几何正确的证据。**

## 一、参数与接口

### 1.1 几何参数：13 个，全部可覆盖

**"优化区间"只决定该参数是否进入优化池，与"能否修改"无关——每一个参数都可以被调用方任意指定。**

| 字段 | 含义 | 单位 | 默认值 | 优化区间 | 可覆盖 |
| --- | --- | --- | --- | --- | --- |
| `L_N` | 鼻锥长度 | m | 0.49 | —（不进优化池） | 是 |
| `L` | 弹身总长度 | m | 3.69 | —（不进优化池） | 是 |
| `D` | 机身最大直径 | m | 0.18 | —（不进优化池） | 是 |
| `L_W` | 前十字舵轴向位置 | m | 1.72 | 1.2 – 1.8 | 是 |
| `C_RW` | 前舵根弦长 | m | 0.23 | 0.1 – 0.4 | 是 |
| `C_TW` | 前舵梢弦长 | m | 0.06 | 0 – 0.09 | 是 |
| `B_W` | 前舵展长 | m | 0.23 | 0.1 – 0.3 | 是 |
| `L_F` | 后十字舵轴向位置 | m | 3.2 | 3.0 – 3.2 | 是 |
| `C_RF` | 后舵根弦长 | m | 0.38 | 0.1 – 0.4 | 是 |
| `C_TF` | 后舵梢弦长 | m | 0.19 | 0 – 0.25 | 是 |
| `B_F` | 后舵展长 | m | 0.22 | 0.1 – 0.3 | 是 |
| `t_fin` | 舵面厚度（舵宽） | m | 0.012（12 mm） | —（不进优化池） | **是** |
| `k_nose` | 鼻锥与柱段交接处倒圆半径系数，`r_f = L · k_nose` | — | 0.005 | 0 – 0.01 | 是 |
| `k_tip` | 鼻锥尖端倒圆半径系数，`r_t = L · k_tip`（与锥面相切的球面钝化） | — | 0.002 | 0 – 0.01 | 是 |

共 14 个几何输入 + 运行级选项。

`t_fin` 默认 12 mm，是**可覆盖的普通入参**，不是硬编码常量。它不做优化只是因为它不在优化池里；把它加进区间文件即可自动升格为优化变量，无需改代码。

`k_nose` 是柱面与锥面**交接处**的相切过渡圆角系数。倒圆位置由 `fillet_location` 决定：`cone_cylinder`（仅交接处）、`nose_tip`（仅弹尖）、`both`（默认，两处都有），三个取值都已实现并真机验收。

`k_tip` 是**弹尖钝化**系数：以半径 `r_t` 的球面与锥面相切，截掉尖锐顶点。约束是切点必须留在锥面内，即 `r_t < L_N·sinα / cos²α`（基准尺寸下 `k_tip < 0.0248`）。钝化后剖面整体朝弹尖方向平移 `offset = r_t(1/sinα − 1)`，让钝化后的端面落在 `x = 0`，**总长仍严格等于 `L`**（多出来的长度补在柱段上）。代价是锥柱交接处随之前移（基准值 `offset = 33.5 mm`，交接处 0.490 → 0.4582）。

**站位的量取基准**：模型坐标原点就是**弹尖**——钝化后球面盖的最前点落在 `x = 0`，弹尾落在 `x = L`，所以 `L_W` / `L_F` 就是字面意义上"舵面前缘到弹尖的距离"，钝化与否都一样（真机实测站位 1.7200 / 3.2000）。钝化把鼻锥缩短 `offset` 并把锥柱交接处朝弹尖拉近（基准值 `offset = 33.472 mm`，交接处 0.490 → 0.4582），这是"总长必须等于 `L`"且"弹尖在原点"两条同时成立时的必然结果，**不需要、也不应该去"补偿"舵面**：把舵面平移反而会破坏 `L_W` / `L_F` 的字面含义。舵面到交接处的距离因此比未钝化的设计值小 `offset`，这是已知量，`params.json` 里直接给出 `tip_offset` 与 `nose_junction_x`。

**舵面剖面怎么建**：设计梯形定义在**弹体表面**上——`C_RW` / `C_RF` 是那里量到的根弦，`L_W` / `L_F` 是那里量到的前缘站位。`root_overlap`（默认 5 mm）纯粹是建模手段：舵根要沉进弹体里，布尔并才干净。所以沉进去的那一段必须把设计梯形的边**沿各自的走向**延伸下去（后缘径向、前缘顺着自身后掠），于是：

- 可见前缘与弹体表面的交线正好在 `L_W` / `L_F`；
- 可见根弦正好是 `C_RW` / `C_RF`；
- 埋在里面的根弦更长（基准值 233.7 / 384.3 mm），且不与柱面共面。

两个坑都踩过：①把前缘从**埋入的**根部往外延伸，可见前缘就会随埋入深度整体后移（基准值 +3.6 / +4.2 mm，真机量出来就是 1723 / 3204 mm）；②反过来把埋入段做成"矩形 + 台阶"，会让舵面有一个面**正好贴在柱面上**，`Combine.Merge` 直接并不上（实测得到 3 个实体而不是 1 个）——所以剖面必须保持 4 点、且没有任何顶点的半径等于弹体半径。

### 1.1.1 仿真区域参数（外壳 / 加密区 BOI）

这两个是**仿真用的区域**，不改变导弹外形，与导弹放在**同一个 `.scdoc`** 里。外壳由官方"准备 > 外壳"命令生成（落在独立组件 `Enclosure` 中），加密区 BOI 是根部件下的独立实体 `BOI`，导弹本体是合并后的单一实体 `Missile`。

| 字段 | 含义 | 单位 | 默认值 |
| --- | --- | --- | --- |
| `enclosure_shape` | 外壳形状 | — | `"cylinder"`（当前仅支持圆柱） |
| `enclosure_radius` | 外壳半径 | m | 2.7（= 15·D） |
| `enclosure_front` | 外壳在弹尖之前的前伸 | m | 3.69（= 1·L） |
| `enclosure_back` | 外壳在弹尾之后的后伸 | m | 16.45 |
| `boi_radius` | 加密区半径 | m | 0.45（= 5·R） |
| `boi_front` | 加密区在弹尖之前的前伸 | m | 0.5 |
| `boi_back` | 加密区在弹尾之后的后伸 | m | 2.5 |
| `model_placement` | 导弹与 BOI 的轴向定位方式 | — | `"ensemble"`（导弹 + BOI **整体**居中；另有 `"missile"` 只让导弹居中、`"offset"` 不用居中规则） |
| `model_offset` | 在居中站位之上再加的轴向偏移（三种模式都生效，沿 +X：正 = 向弹尾，负 = 向弹尖即靠前） | m | **-4.0**（= 整体居中后往前 4000 mm） |
| `enclosure_name` | 外壳在结构树里的名字（落在命令新建的**组件**上） | — | `"Enclosure"` |
| `boi_name` | 加密区实体的名字 | — | `"BOI"` |

**外壳（组件 `Enclosure`）**是外流场的远场边界，尺寸按"边界不能影响解"来定：径向 2.7 m 是舵尖半径（0.32 m）的 8.4 倍，激波反射不会打到边界；后伸 16.45 m（总长 23.83 m），比原先的 18.45 m 少了 2 m，**只从尾端削**，前端 `x = -3.69` 不动。轴向中位因此是 `(-3.69 + 20.14)/2 = 8.225 m`——验收专门量这一项，因为只缩尾端（8.225）和两端各缩 1 m（9.225）的**包围盒完全相同**。

**定位 = 先居中，再按 `model_offset` 微调。** 外壳是基准、不动；`model_placement` 决定"中心"怎么算，默认的 `"ensemble"` 把**导弹与 BOI 作为一个整体**居中——BOI 总是把导弹整个包在里面（前后余量都非负），所以这个整体的外轮廓就是 BOI，其轴向中位比导弹本体的中位**靠后** `(boi_back − boi_front)/2 = 1 m`，居中的站要正好减去这 1 m：

```text
站位 = 居中站位 + model_offset
居中站位(ensemble) = (enclosure_back − enclosure_front)/2 − (boi_back − boi_front)/2 = 6.38 − 1.00 = 5.38 m
居中站位(missile)  = (enclosure_back − enclosure_front)/2                              = 6.38 m
居中站位(offset)   = 0（不用居中规则，站位就是 model_offset 本身）
站位(默认)         = 5.38 + (−4.0) = 1.38 m        ← 居中后整体往前 4000 mm
```

基准参数下：外壳 `[-3.69, 20.14]`（中位 8.225，不动）、BOI `[0.88, 7.57]`（中位 4.225）、导弹 `[1.38, 5.07]`（中位 3.225）。即 BOI 前端面到外壳前端面 4.57 m、后端面到后端面 12.57 m——**整体比正中靠前 4.0 m**，正是 `model_offset`；**导弹本身**又比外壳中位靠前 5.0 m，其中 1.0 m 是 BOI 自己的不对称（前 0.5 m、后 2.5 m）。想要正好居中填 `model_offset = 0`；想让导弹本体居中（而不是整体）用 `model_placement = "missile"`；不想用居中规则、直接给绝对站位用 `"offset"`。模式与偏移都是**换一个数**，不用改代码。

实现上**不是建完再平移**，而是在 `resolveModelPlacement()` 里把 `居中站位 + model_offset` 解析成一个轴向站位，再进剖面（`bodyProfile` 的 `shift` 含该站位）与舵面站位（`le_x = L_W + 站位`），于是解析式与实测始终在同一个绝对坐标系里，`bodyCenterOfMass` / `fin.centroid` 天然含位置；`leFromNoseTip = leStation − nose_tip_x` 仍等于 `L_W`。外壳的轴向缓冲随之补偿为 `(enclosure_front + 站位, enclosure_back − 站位)`，所以边界绝对位置不变。验收的**旋转体轴向质心**一项因此同时钉住了"位置确实生效"——默认不加这 1.38 m，实测会比解析值小整整 1.38 m。

**加密区 BOI**是网格细化区域，比外壳紧得多：半径 0.45 m 比舵尖 0.32 m 留 0.13 m 余量，前伸 0.5 m ≈ 2.8D 容纳钝头前的弓形激波，后伸 2.5 m ≈ 13.9D 覆盖底部回流与近尾流。

**外壳走官方命令，不自绘。** 外壳调用 `Enclosure.Create(ISelection, EnclosureOptions)`，和菜单"准备 > 外壳"点出来的是同一个特性（`enclosure_shape` 目前只开放 `cylinder`）。三个必须显式给的东西：

1. `EnclosureOptions.Frame = Frame.Create(Point.Create(0, 0, 0), Direction.DirX)`。默认圆柱轴是**全局 Z**，不设 frame 外壳就横躺在弹体旁边（实测：默认方向下外壳轴向落在 Z 上）。
2. `Subtract = False`。置真会把导弹从外壳里布尔减掉，远场边界就成了空壳。
3. `EnclosureCushion = CylinderEnclosureCushion(axisLow, axisHigh, radius)`。命令按"选中体的包围盒 + 缓冲"定尺寸：轴向缓冲是包围盒两端各自的外伸量（实测 `(3.69, 14.76, …)` 给出轴向长度 `0.64 + 3.69 + 14.76 = 19.09`），径向缓冲则是加在**包围盒中心到角点的距离**上（实测默认 frame 下 `2.7 + hypot(1.845, 0.32) = 4.5725`）。所以参数表里保持绝对尺寸不变，换算成缓冲在 Node 侧完成：`cushion.radius = enclosure_radius − hypot(lateral/2, vertical/2)`，基准值 `2.7 − 0.4525483 = 2.2474517`。

命令会把外壳放进一个**新建的组件**里，而生成出来的 body 的 `Name` 属性是**只读**的（`DesignBodyGeneral`），所以外壳的标识落在**组件名**上：脚本把该组件命名为 `enclosure_name` 的值（默认 `Enclosure`，想叫 `外壳` 就自己改这个参数）。这一点也解释了为什么它在结构树里看着不像一个普通实体——`GetRootPart().Bodies` 看不到它，必须用 `GetRootPart().GetAllBodies()` 才能数全（实测 `bodyCount = 3`）。

**名字的处理**：名字不写进脚本，而是通过 `params.json`（UTF-8）传入，脚本源文件保持**纯 ASCII**，宿主不必猜脚本编码（单测锁死这一点）。因为 `enclosure_name` 允许任意字符串，两条读回路径都要走通：

1. **ASCII 名字（默认）**：`componentNames` 能正常回读，验收就**同时**要求 `String.Equals` 比对通过**且**读回的组件名列表里确实有这个名字——名字设到了别的对象上会被抓住。
2. **非 ASCII 名字**（例如把 `enclosure_name` 设成 `外壳`）：宿主把 .NET 字符串转成 Python `str` 时遇到非 ASCII 首个字节就抛 `UnicodeDecodeError`（早先直接把建模打断），所以所有回读都包在 try 里、读不出来记 `<unreadable>` 而不是抛错；此时**验证改用 `String.Equals`**：把期望值（来自 JSON 的 Python unicode）作为参数传给 .NET `String.Equals`，方向是 unicode → .NET string，不需要解码宿主返回的字节串。实测这种配置下 `namedObjects = {Missile: true, BOI: true, 外壳: true}`，而 `componentNames` 回读为 `<unreadable>`，验收只按比对判定、不把"读不回"当失败（有单测分别锁住两条路径）。

**加密区 BOI** 没有对应的官方命令，仍按**绕 X 轴的旋转实体**（矩形剖面旋转 360°）生成，沿用已验证的旋转流程，是根部件下的独立实体。

### 1.1.2 CFD 命名选择（Named Selection，`official-doc` + `runtime-verified`）

同一次运行在同一个文档里建 7 组命名选择：

| 组名 | 内容 | 面来源 |
| --- | --- | --- |
| `inlet` | 外壳前端盖 | 外壳面中质心 x 最小者 |
| `wall` | 外壳圆柱侧面 | 外壳面中两端盖之外的那些 |
| `outlet` | 外壳后端盖 | 外壳面中质心 x 最大者 |
| `missile_nose_tip` | 弹尖球面倒圆 | 剖面段 `tip` |
| `missile_nose` | 圆锥面 | 剖面段 `cone` |
| `missile_body` | 柱面 + 锥柱交接圆角 + 弹底端面 | 剖面段 `cylinder` / `shoulder` / `base` |
| `missile_fin` | 前后舵面全部实例 | 质心不在轴线上的面 |

**导弹面必须一个不漏**，所以分类不靠猜：

1. **先分两族**：回转体每个面的质心都落在 X 轴上（舵面孔洞按 4 次对称分布，不会把任何面的质心推离轴线），舵面则全部离轴——实测两族刚好是 5 / 40。
2. **弹体族按解析面积配对**：`tip` / `cone` / `shoulder` / `base` 的面积由 Pappus 精确给出，每个面取相对误差最小的那一段（阈值 1e-3）。**柱面被舵面裁过，没有可用的解析面积，用排除法**取剩下的那一个面；剩余面数不等于 1 就报错。
3. **建完读回**：`GetRootPart().Groups` → `Group.Name` / `Group.Members`，重新量每组的面数与合面积。

**验收**：4 组导弹选择的面数之和 = 实体总面数；**面积之和 = 实体自身表面积**（`MeasureHelper.MeasureArea(body)`）。后者是"没有遗漏、也没有分错组"的真凭据——面数与面积任一不匹配都会失败。基准值 2.7955190015 m²，实测相对误差 1e-15。

命名（`NamedSelection.Create(primary, secondary, name, PartLocation.Root)`，`secondary` 先试空选择再退化为 primary）与组名放在 `lib/spaceclaim-runner.mjs`，脚本里不出现名称常量。`missile_nose_tip` 在尖头配置（`k_tip = 0`）下没有面，此时不建该组，验收也相应不要求。

### 1.1.3 结构清理与物理抑制（`official-doc` + `runtime-verified`）

**先删空组件。** `Pattern.CreateCircular` 会给每组阵列实例各建一个组件，`Combine.Merge` 把 body 并进导弹后组件就空着留下（实测 4 个）。建模完成后逐个删除，但：

- **删前必须确认是空的**：`Component` 没有 body 集合属性，用扩展方法 `component.GetAllBodies()`（`ComponentExtensions`）读，非空就跳过；读不出来也跳过并记为 `bodiesError`。
- **删后复核**：`GetAllBodies()` 的总数必须与删除前一致，不一致直接抛异常中断——删错东西不会静默通过。
- `Component.Delete()` 继承自 `DocObject`。

**再抑制其余。** 参与物理的只有 Missile / BOI / Enclosure，剩下的用 `ViewHelper.SetSuppressForPhysics(ISelection, bool)` 标记抑制：

- 遍历根部件下的 `Bodies` / `Components` / `Curves` / `DatumPlanes` / `DatumLines` / `DatumPoints` / `CoordinateSystems`；`Component` **没有** body 集合属性，所以必须再补一遍 `GetAllBodies()`，否则组件里的外壳 body 根本走不到。
- 保留集按**对象身份**判定（`is`，脚本里已在用）：导弹 body、BOI body、外壳 body、外壳组件；不按名字，因为外壳 body 的名字读不回。
- 命名选择是 Group，不在遍历集合里，不会被误抑制。
- 清理后的实测结构：2 个根 body + 1 个组件（`Enclosure`）+ 1 个默认坐标系 → 删掉 4 个空组件，抑制 1 个坐标系，保留 4 个对象。

> **证据边界**：API 只有 setter，**没有**读取"是否已物理抑制"的接口，`IsSuppressed` 是另一个（普通抑制）标志，抑制前后都是 `False`。所以可证明的是：遍历清单完整回报、保留集恰好是那 4 个、其余每个对象的调用都成功、4 个被删组件删前 body 数都是 0、删后 body 总数不变。抑制状态本身**无法回读**，不冒充 `runtime-verified`。

### 1.2 约定参数：同样留出接口

这些量此前是我为把几何"补全"而引入的内部约定。它们**也必须可覆盖**，否则使用者改了主参数却改不动这些隐含约定，模型就会和预期不符。

| 字段 | 含义 | 默认值 | 可选值 |
| --- | --- | --- | --- |
| `fin_phase` | 十字舵相位 | `"+"`（0/90/180/270） | `"+"` / `"x"`（45° 起） |
| `root_overlap` | 舵根沿展向嵌入弹体的重叠量 δ | 0.005 m | 任意 `> 0`（必须小于 `B_*`） |
| `le_mode` | 梯形舵第四约束 | `"trailing_aligned"`（后缘齐平） | 当前仅支持此值，见下 |
| `fillet_location` | 倒圆位置 | `"both"`（交接处 + 弹尖） | `cone_cylinder` / `nose_tip` / `both` |
| `cruciform_count` | 每级舵面片数 | 4 | 正偶数 |
| `scdm_api_version` | 目标脚本 API 版本 | 由所选 `AWP_ROOT<number>` 解析 | 版本字符串 |
| `output_dir` / `file_name` | 产物目录与文件名 | 由调用方给 | 路径 / 文件名 |

**未实现的枚举值必须显式报错，不得静默降级。** 例如 `le_mode` 传入 `"leading_aligned"` 时返回明确的"暂不支持"错误，而不是悄悄按后缘齐平建模——静默降级会产出看起来正常但几何错误的模型。

### 1.3 接口设计：唯一真源 + 四条访问路径

```text
lib/missile-schema.mjs        ← 唯一真源：字段名 / 单位 / 默认值 / 区间 / 校验规则
   ├─→ MCP 工具 inputSchema（由 schema 生成，全部字段可选 + 取默认值）
   ├─→ 命令行开关（scripts/missile.mjs --L 3.7 --D 0.19 --t_fin 0.012 …）
   ├─→ params.json 模板（含全部字段、单位与默认值，可直接手改）
   └─→ ranges.json（优化区间 → 决定哪些参数进入优化池）
```

- **单一真源**：字段名、默认值、区间只在 schema 里定义一次。MCP 入参、CLI、JSON 模板、校验器全部由它派生，避免四套定义漂移。
- **优先级**：调用方显式值 > 预设文件 > schema 内置默认值。
- **完整生效值必须落盘**：写入 run 目录的 `params.json` 是**合并后的完整参数集**，不是"只记录改动过的字段"，并给每个字段标注来源：

  ```json
  { "L":  { "value": 3.69, "unit": "m", "source": "default" },
    "t_fin": { "value": 0.012, "unit": "m", "source": "explicit" } }
  ```

  这样任何一次生成都能回答"这个 12 mm 是哪来的"，也是复现与排错的唯一依据。
- **优化池由数据决定**：`ranges.json` 的键集合就是优化变量集合；增删键即可调整优化池，不需要改代码。
- 使用者自己的预设文件放在仓库**之外**的用户目录，不入库。

## 二、几何定义（已确认的口径）

坐标系：X 沿弹轴，**原点在弹尖**，X 正方向指向弹尾；Y/Z 横向。参数以 m 输入，模板内一律换算为 mm 建模。

### 2.1 主体：单次旋转体

在含轴平面绘制闭合剖面并绕 X 轴旋转 360°，一次得到单个实体（避免"圆锥 + 圆柱 + 布尔"的额外风险）。剖面的段数由 `fillet_location` 与两个系数是否为零决定：

| 配置 | 剖面段 | 旋转体面数 |
| --- | --- | --- |
| 默认（交接处 + 弹尖） | 弹尖弧 → 锥面 → 交接弧 → 柱面 → 底面 → 轴 | 5 |
| 仅交接处 | 锥面 → 交接弧 → 柱面 → 底面 → 轴 | 4 |
| 仅弹尖 | 弹尖弧 → 锥面 → 柱面 → 底面 → 轴 | 4 |
| 两者皆无（尖头尖肩） | 锥面 → 柱面 → 底面 → 轴 | 3 |

沿轴的收口段（`r = 0`）不产生面，因此不参与面积核对。共面的相邻段（例如柱面被肩部站位分成的两段）会先合并，避免把同一条柱面算成两个面。

### 2.1.1 鼻锥倒圆（`r_f = L · k_nose`）

圆角与锥面、柱面**同时相切**，几何量全部解析求出，不使用近似倒角命令，保证可复现：

| 量 | 表达式 | 基准值 `k_nose=0.005` | 区间上限 `k_nose=0.01` |
| --- | --- | --- | --- |
| 倒圆半径 | `r_f = L · k_nose` | 0.01845 m | 0.03690 m |
| 锥半角 | `α = atan(R / L_N)`（与 `k_nose` 无关） | 10.41° | 10.41° |
| 圆心 | `(c_x, R - r_f)`，其中 `c_x = [ r_f·S + L_N·(R - r_f) ] / R`，`S = √(L_N² + R²)` | (0.49168, 0.07155) m | (0.49336, 0.05310) m |
| 柱面切点 | `T2 = (c_x, R)` | (0.49168, 0.09) m | (0.49336, 0.09) m |
| 锥面切点 | `T1` = 由交接角沿锥面回退 `t` 得到，`t = r_f · tan(α/2)` | 回退 0.00168 m | 回退 0.00336 m |
| 圆角占位 | 沿锥面与柱面各占 `t` | 各约 1.7 mm | 各约 3.4 mm |

整个优化区间内圆角只吃掉锥柱交接处 1.7 – 3.4 mm，始终是局部过渡，对总体积与外形影响很小。区间上限处的 `r_f = 36.9 mm` 仍低于告警阈值 `0.5·R = 45 mm`，因此**在已批准的区间内不会触发头部外形告警**。

模板实现要点：**必须使用 `SketchArc.CreateSweepArc(center, start, end, sense)` 并显式给出 `sense`**。

> **实测坑（已踩）**：`SketchArc.Create(center, start, end)` 与 `SketchArc.Create3PointArc(from, mid, to)` 都会生成**补弧（major arc）**——即无论第三点在哪一侧，SpaceClaim 都按草图坐标系自带的绕向建弧。补弧会把剖面变成"尖角区域减去以倒圆圆心为心、`r_f` 为半径的圆盘"，于是模型上出现一个环形凹槽而不是圆角。判别证据：剖面面积会从正确的 `0.3100499` 变成 `0.3089805`（相差正好 `π·r_f²`），旋转体体积比解析值少 `2π·c_y·π·r_f²`。

**可行性硬约束（不是经验取值）：**

- 圆心纵坐标必须为正：`R - r_f > 0` → `r_f < R` → **`k_nose < R / L`**（基准尺寸下 ≈ 0.0244）。
- 柱面切点必须落在柱段内：`c_x ≤ L`。
- 锥面切点必须落在锥面内：`t ≤ S`。

### 2.1.2 弹尖倒圆 / 钝化（`r_t = L · k_tip`）

以半径 `r_t` 的球面与锥面相切，截掉尖锐顶点：

| 量 | 表达式 | 基准值 |
| --- | --- | --- |
| 弹尖半径 | `r_t = L · k_tip` | 0.00738 m |
| 球心（在轴线上） | `d = r_t / sin α` | 0.040852 m |
| 球面切点 | `T_t = (d·cos²α, d·cosα·sinα)` | (0.039519, 0.007259) m |
| 截面高度 | `h = r_t` 沿轴量到 `T_t` | 0.006047 m |
| 钝化截断量 | `offset = d - r_t` | 0.033472 m |

**总长口径**：钝化后剖面整体后移 `offset`，使钝头顶点落在 `x = 0`，而底面仍在 `x = L`。因此**总长严格等于 `L`**，代价是锥面与柱面的交接站位从 `L_N` 前移到 `L_N - offset`（基准值 0.4565 m）。这样"弹身总长度"始终等于模型的真实长度，包围盒验收才有意义。

**硬约束**：切点必须留在锥面内 → `r_t < L_N·sinα / cos²α`（基准尺寸下 `k_tip < 0.0248`），否则钝化会吃掉整个鼻锥。


### 2.2 舵面：后缘齐平、前缘后掠

**轴向基准：`L_W` / `L_F` 等于根弦前缘到弹尖的距离。**

展向从弹体内部 `R - root_overlap` 起（默认重叠 5 mm，有意嵌入弹体以保证并集干净，可由 `root_overlap` 覆盖），到 `R + B` 止：

| 点 | x | r |
| --- | --- | --- |
| P1 根前缘 | `L_*` | `R - root_overlap` |
| P2 根后缘 | `L_* + C_R` | `R - root_overlap` |
| P3 梢后缘 | `L_* + C_R` | `R + B` |
| P4 梢前缘 | `L_* + C_R - C_T` | `R + B` |

P1→P2→P3→P4 闭合后沿切向对称拉伸 `t_fin`，得一片舵实体。前后舵各造一片。

基准值下的前缘后掠角：前舵 `atan((C_RW - C_TW)/B_W) ≈ 36.5°`，后舵 `atan((C_RF - C_TF)/B_F) ≈ 40.8°`。

### 2.3 阵列与合并

前后两片舵一起作为种子 → `Pattern.CreateCircular`，`CircularCount = cruciform_count`（默认 4）、`CircularAngle = DEG(360)`、轴取弹轴 → 得 8 片 → `Combine.Merge` 主体与全部舵面 → **1 个实体**。

该"阵列 + 合并 + 验收"流程在本仓库已有一份真实验收脚本，可作为模板基础：`scripts/spaceclaim-circular-repeat-smoke.py`。

### 2.4 十字舵相位

由 `fin_phase` 控制（接口参数，默认 `"+"`）：`"+"` 时 4 片位于 0° / 90° / 180° / 270°，`"x"` 时整体旋转 45°。种子舵面的初始相位由该参数决定，改相位不需要改模板结构。

## 三、数据流与调用

```text
13 个几何参数 + 约定参数（调用方任意覆盖，缺省取 schema 默认值）
   → 合并（显式值 > 预设 > 默认）+ 校验（纯 Node）
   → params.json（完整生效值 + 每字段 source 标注）
   → SpaceClaim.exe /RunScript="model.py" /ScriptArgs="params.json" /Headless=True /ExitAfterScript=True
   → 建模 → DocumentSave.Execute("missile.scdoc")
   → result.json（几何回读 + status）
```

要点：

- `.py` 为文本脚本，由本仓库生成并版本管理；**不生成二进制 `.scscript`**。
- 模板内用 `args[0]` 取得 `params.json` 路径，全部参数从 JSON 读取，命令行不再长期堆参数——**参数改动的唯一入口是参数集，而不是改脚本**。
- 模板脚本内不含任何几何数值常量；所有数值（含 `t_fin`、`root_overlap`、`fin_phase`）都从 JSON 读取。若模板里出现裸数值，即为接口设计失败。
- 几何元素选择一律采用**索引方式**，不使用录制器的"智能变量"名字（`Face2`、`Info3` 之类在无人值守时不可靠）。
- 模板头部声明与目标版本一致的 `# Python Script, API Version = <version>`。
- SpaceClaim 的 stdout 不得进入 MCP 的 stdout（stdio 传输下 stdout 保留给 JSON-RPC）。

## 四、参数校验规则

**硬错误（阻止建模）：**

- `L_N > 0`、`L_N < L`
- `D > 0`、`D < L`
- `L_W ≥ L_N`、`L_W + C_RW ≤ L`
- `L_F ≥ L_N`、`L_F + C_RF ≤ L`
- 前后舵不重叠：`L_W + C_RW ≤ L_F`
- `C_RW > 0`、`C_RF > 0`、`B_W > 0`、`B_F > 0`、`t_fin > 0`
- 倒圆：`k_nose ≥ 0`（`0` 合法，见下方分支）；`r_f < R`（等价 `k_nose < R / L`）；`c_x ≤ L`；`t ≤ S`

其中倒圆的三个几何约束由 2.1.1 的解析式直接判定，不做近似：它们分别保证圆心不越过弹轴、柱面切点落在柱段内、锥面切点落在锥面内。基准尺寸下 `k_nose` 的硬上界约为 `0.0244`，而批准的优化区间上限 0.01 留有一倍以上余量。

**`k_nose = 0` 的分支：** 此时圆角半径为零，剖面退化为尖角。这是合法几何，但**不能**送去建零半径圆弧（会产生退化面），模板必须走"尖角剖面"代码路径（锥面直线直接接到柱面直线），并在报告中显式记录 `fillet=sharp`。

**约定参数校验（1.2 节的参数同样进校验，不允许"能传但不检查"）：**

- `root_overlap > 0` 且 `root_overlap < min(B_W, B_F)`：否则舵根要么完全脱离弹体、要么整片没入弹体。
- `fin_phase ∈ { "+", "x" }`。
- `cruciform_count` 为正偶数且 ≥ 2。
- `le_mode`、`fillet_location` 只接受已实现的取值；传入其他值 → 硬错误（明确报"暂不支持"），**禁止静默回退到默认值**。
- `scdm_api_version` 必须与解析到的 SpaceClaim/CHM 版本一致，避免把不匹配的 API 版本写进模板头部。

**自动修正（记录到报告）：**

- `C_T` 下限 clamp 到 `1e-3 m`，避免零长度梢边形成的退化面。

**警告（不阻断）：**

- 取值超出上表优化区间（区间是优化范围，不是几何硬约束）。
- `C_T > C_R`：后缘齐平口径下退化为前掠前缘，几何仍合法但需提示。
- `0 < k_nose < 0.001`：`r_f < 3.7 mm`，圆角过小，可能被建模内核并入尖角而不可区分；仍按圆弧路径生成并提示。
- `r_f > 0.5 · R`（即 `k_nose > 0.0122`）：倒圆已明显改变头部外形（不再是小过渡），需提示。该阈值在批准的区间之外，只对区间外的取值生效。

**已核对：** 全部输入现已确定（含 `k_nose` 的 0 – 0.01）。基准值与优化区间全域均满足硬错误条件：`L_F` 最大 3.2 + `C_RF` 最大 0.4 = 3.6 ≤ `L` = 3.69；`L_W` 最大 1.8 + `C_RW` 最大 0.4 = 2.2 ≤ `L_F` 最小 3.0；`k_nose` 最大 0.01 时 `r_f = 36.9 mm < R = 90 mm`，且 `c_x = 0.49336 ≤ L`、`t = 3.36 mm ≤ S = 498 mm`。

## 五、产物布局（全部在仓库之外）

```text
<workdir>/<run-id>/params.json     # 完整生效值（含默认值）+ 每字段 source + 校验报告
<workdir>/<run-id>/model.py        # 本次实际执行的模板副本（可追溯）
<workdir>/<run-id>/missile.scdoc   # 一个文件：导弹本体 + 外壳 + 加密区 BOI
<workdir>/<run-id>/preview.png     # 等轴视图快照，供人工目视检查（导出失败不影响建模）
<workdir>/<run-id>/result.json     # status / 实体数 / 包围盒 / 关键尺寸回读 / 各区域测量
<workdir>/<run-id>/diagnostic.txt  # 失败时的异常文本
```

导弹、外壳、加密区都在**同一次 SpaceClaim 运行、同一个文档**里生成：导弹合并为单一实体后，先调用 `Enclosure.Create` 生成外壳，再用 `ExtrudeType.ForceIndependent` 旋转出 BOI。BOI 必须独立（若用 `Add`，BOI 会把导弹布尔吸收掉，因为导弹位于 BOI 内部）；外壳由命令自己放进组件，天然不会与导弹合并。

名称：导弹 body 是 `Missile`，BOI body 是 `boi_name`（默认 `BOI`，ASCII、可回读），外壳的名字落在命令新建的组件上、取 `enclosure_name`（默认 `Enclosure`）。命令生成的外壳 body 名字是只读的，回读时记为 `<unreadable>` 而不是抛错。名字都从 `params.json` 进来，脚本里不出现名称常量；`enclosure_name` 允许非 ASCII（例如 `外壳`），那时回读会抛 `UnicodeDecodeError`，所以按上文改用 `String.Equals` 比对。

使用者自备的预设文件与区间文件（`presets/*.json`、`ranges.json`）放在仓库之外的用户目录，不入库；它们只是输入，产物一律进 run 目录。

## 六、MCP 工具设计

| 工具 | 用途 | 是否依赖 ANSYS |
| --- | --- | --- |
| `validate_missile_params` | 纯校验（含 1.2 节约定参数），返回错误/警告/修正清单 | 否 |
| `generate_model_script` | 生成完整生效的 `params.json` 与 `model.py`，可在 GUI 里手动试跑 | 否 |
| `build_missile_model` | headless 执行并验收，返回 `.scdoc` 路径与证据 | 是 |

三个工具的入参**全部由 schema 生成**：13 个几何参数与 1.2 节的全部约定参数都是可选字段，缺省取默认值；另提供 `outputDir` / `fileName` / `timeoutMs` / `execute` 等运行级选项。新增一个参数时只改 schema，三个工具与 CLI 同时获得该字段。

现有 4 个工具保持不变。

## 七、证据边界与验收门槛

| 事实 | 当前等级 |
| --- | --- |
| `Application.RunScript` 存在于官方 API | `official-doc`（V241 CHM 的 Release Notes 有官方链接） |
| `/RunScript= /ScriptArgs= /Headless=True /ExitAfterScript=True` 的实际用法 | **`runtime-verified`**（本机 SCDM 2024 R1 已执行） |
| 安装目录内可执行文件中含 `RunScript` / `ScriptArgs` / `ExitAfterScript` / `Headless` 标识符 | 本机二进制证据 |
| `.py` 文本脚本 + `/ScriptArgs` 传参可用于无人值守调用 | **`runtime-verified`** |
| 生成的 `.scdoc` 几何正确 | **`runtime-verified`**（见第十一节验收记录） |
| 圆角半径直接回读 | **不可用**：`MeasureHelper.FilletRadius` 对旋转弧生成的圆环面抛异常，改用解析面积核算（见下） |

**`build_missile_model` 的通过条件（全部满足才算 `runtime-verified`）：**

1. `result.json` 是本次运行**新生成**的（按 mtime 判定），且 `status=PASS`；
2. 合并后实体数量为 1；
3. 包围盒与输入吻合：轴向长度 ≈ `L`，横向与竖向 ≈ `D + 2·max(B_W, B_F)`；
4. 旋转体面数与剖面段数一致（5 / 4 / 3，取决于 `fillet_location` 与两个系数）；
5. 旋转体体积 ≈ 剖面 Pappus 解析值；
6. **每个剖面段对应的面面积都 ≈ 解析值**（逐段 Pappus：`面积 = 2π · 段形心半径 · 段长`）。因为段长与形心都由倒圆半径解析决定，这一条同时锁定了交接处圆角 `r_f`、弹尖圆角 `r_t` 以及锥面/柱面的切点位置；启用时额外单独报告弹尖面与交接面的比对；
7. `.scdoc` 是本次运行新生成的；
8. **单一 `.scdoc` 内实体数正确**（1 个导弹 + 每个区域各 1 个，用 `GetRootPart().GetAllBodies()` 数，因为外壳的命令把它放进了组件），且每个区域的尺寸 ≈ 解析值、体积 ≈ `πR²·长度`、**轴向中位 ≈ `(xFront + xBack)/2`**（长度相同但缩短放在不同端时包围盒完全一样，只有中位能分辨）；
9. **轴向量测得到证明**：包围盒、面积、体积都对轴向平移不敏感，所以额外回读两个量——①旋转体自身的**轴向质心** ≈ 解析值（`∫∫ x·y dA / ∫∫ y dA`，Pappus），用来钉住弹尖基准与整体轴向站位的和；②每个舵面毛坯的**质心** ≈ 剖面形心解析值，再由解析形心偏移反推出**可见前缘站位**，用来钉住"舵面前缘确实交在 `L_W` / `L_F`"。这一条用**绝对**容差 0.1 mm：把埋入段按"前缘外推"建错时，形心只挪 1.2 mm，相对容差会睡过去；
10. **定位规则被独立复算，且真的落到实体上**：①验收在自己这一侧按参数重算一遍规则（`ensemble` = `mirror − boiSkew`、`missile` = `mirror`、`offset` = 0）再加上 `model_offset`，与请求里的站位比对到 **1e-12**——换错模式、漏掉偏移、正负号反了都过不去（不是读请求里的数再和自己比）；②**实测**：BOI 与外壳的轴向中位（`centerOfMassM.x`）之差 ≈ 解析值 `站位 + boiSkew − mirror`，容差 **1 mm**（外壳中位本身带命令的 4e-5 m 数值精度；2e-3 的相对容差看不见 1 mm 的漂移）。默认 `ensemble` + `model_offset = -4.0` 时该差值就是 −4.0 m，即"整体居中后往前 4000 mm"；
11. **外壳确实来自官方"准备 > 外壳"命令**：请求里 `mode = "enclosure-command"`、回读 `componentCount ≥ 1`、`String.Equals` 确认有一个对象叫 `enclosure_name`（ASCII 名字还要在读回的组件名列表里出现）、且换算出来的径向缓冲为正（负缓冲会把边界缩到模型里面去）；
12. **命名选择完整**：7 组（尖头配置下 6 组）都存在且非空；4 组导弹选择的面数之和 = 实体总面数；**面积之和 = 实体自身表面积**（证明没有遗漏面、也没有把面分错组）；`inlet` / `outlet` 各 1 个面且面积 = `π·enclosure_radius²`，`wall` 1 个面且面积 = `2π·enclosure_radius·enclosure_length`；
13. **结构清理与物理抑制**：阵列残留的空组件已删除（删前每个都确认过 `bodies == 0`、无读取/删除错误），删后结构里只剩外壳那一个组件且 body 总数不变；物理抑制的保留集恰好是导弹 body / BOI body / 外壳 body / 外壳组件这 4 个对象，其余对象全部抑制成功（0 失败）。抑制状态本身无接口可回读，见 1.1.3 的证据边界；
14. 失败时把异常写入 `diagnostic.txt` 并原样上报，不吞异常。

> 第 6 条是替代 `FilletRadius` 的证明方式（该 API 对旋转弧生成的圆环面会抛异常）：环面面积是半径的强函数，锥面段长与切点位置又同时被锁定，三者一致才通过。基准尺寸下实测值与解析值在 12 位有效数字内一致。

## 八、任务清单

### Task 0: 前置修复（与几何无关，但当前会挡住端到端）

**Files:** `lib/chm.mjs`、`lib/index.mjs`、`lib/paths.mjs`、`server.mjs`、`tests/*`、`scripts/smoke.mjs`

- [x] 安装依赖：`npm ci`（当前工作区没有 `node_modules`）。
- [x] 修复 CHM 解包：根因是 `hh.exe -decompile` 收到**带引号**的 CHM 路径（`ANSYS Inc` 这类含空格路径）时静默退出且零产出；改为经 `cmd.exe` 透传原始命令行，并去掉 PowerShell 桥接（同时解除执行策略依赖）。
- [x] 去掉硬编码的 `V261`：改为按已解析的 CHM 路径推导 API 版本，`server.mjs` 描述与索引元数据同步。
- [x] 验证：`npm test` 与 `node scripts/smoke.mjs` 通过（并修掉 smoke 里"读不到文档也能通过"的假断言）。

### Task 1: 几何 API 取证（面向目标版本 CHM）

**Files:** 无（结论见第十一节取证表）

- [x] 用 `search_api` 确认几何命令签名（结果见第十一节）。
- [x] 确认 `.py` 脚本在 headless 执行时可通过 `/ScriptArgs` 拿到参数文件（`args[0]` 可用），并以环境变量作为兜底。
- [x] 确认 `DocumentSave.Execute(string)` 可直接保存 `.scdoc`（无需 FileSettings）。
- [x] 每条结论标注证据等级；`FilletRadius` 不可用这一条也在真机上确认并改用替代证明。

### Task 2: 参数接口、模板与生成（纯 Node，不依赖 ANSYS）

**Files:** 新增 `lib/missile-schema.mjs`（唯一真源）、`lib/missile-params.mjs`（合并 + 校验）、`lib/missile-geometry.mjs`（解析几何）、`lib/missile-template.mjs`（脚本生成）、`scripts/missile.mjs`（命令行入口）、`tests/missile-{schema,params,geometry,template}.test.mjs`；`server.mjs` 注册 `validate_missile_params`、`generate_model_script`

- [x] 建 schema 唯一真源：13 个几何参数 + 1.2 节全部约定参数的字段名 / 单位 / 默认值 / 区间 / 说明。
- [x] 由 schema 派生 MCP 工具入参、CLI 开关、`params.json` 模板与优化池；字段没有第二处定义。
- [x] 实现合并优先级（显式值 > 预设文件 > 默认值）与完整生效值落盘（含每字段 `source` 标注）。
- [x] 实现第四节全部校验规则与报告结构。
- [x] 生成 `params.json`、`model.py`、`result.json`；单测断言 `model.py` 内没有任何几何数值常量。
- [x] 单测覆盖接口本身：schema 字段与文档表格一致（防定义漂移）；**逐个参数各做一次 override 断言**。
- [x] 单测覆盖合并：显式值 > 预设 > 默认；预设缺字段时逐字段回落默认值。
- [x] 单测覆盖：基准值、区间边界、`C_T = 0` clamp、`C_T > C_R` 告警、每条硬错误的拒绝、超区间告警。
- [x] 单测覆盖倒圆：基准值与区间上限的解析值（`r_f / c_x / T1 / T2 / t`）一致；`k_nose = 0` 走尖角分支；过小倒圆告警；`k_nose ≥ R/L` 被拒。
- [x] 单测覆盖 `t_fin`：默认 12 mm，且可被覆盖。
- [x] 单测覆盖约定参数：`root_overlap` 越界被拒；枚举非法值明确报错且不静默回退。
- [x] 单测用真机实测值做回归锚点：剖面面积、环面/锥面/柱面面积、旋转体体积。
- [x] 验证：`npm test` 通过（67 项，0 失败）。

### Task 3: headless 执行器与验收

**Files:** 新增 `lib/spaceclaim-runner.mjs`；`server.mjs` 注册 `build_missile_model`

- [x] 解析 `SpaceClaim.exe`：优先 `SCDM_EXECUTABLE`、首次配置，其后按 `AWP_ROOT<number>` 展开，禁止硬编码路径。
- [x] `build_missile_model` 与 Task 2 共用同一套参数合并与校验实现。
- [x] spawn 时 `windowsHide`，带超时，stdout/stderr 不回灌 MCP 的 JSON-RPC 通道。
- [x] 实现第七节的 9 条通过条件，写回结构化证据（含每项检查的实测值与期望值）。
- [x] 在目标安装上真实验收并回填 `runtime-verified` 证据；输出目录在仓库外（系统临时目录）。
- [x] `execute: false` 时只生成脚本，且在未安装 ANSYS 的机器上也可用（供单测与手工试跑）。

### Task 4（可选）: 批量扫参

- [ ] 单实例内循环多组参数、存多个 `.scdoc`，避免重复启动进程与重复占用许可。
- [ ] 每组参数独立写 `result.json`，失败不影响其余组。

## 九、风险与待决项

1. **默认值已全部确定，但每个字段都可被覆盖**：覆盖后的参数仍必须通过第四节全部校验，**不提供"跳过校验"的开关**——接口自由不能以产出错误几何为代价。
2. **`t_fin` 的物理口径**：使用者口述为"舵宽 12 mm"，本实现按**舵面厚度**（沿切向对称拉伸 12 mm）落地，并已由真机实测的外形尺寸间接印证。若口径有变，改的只是默认值。
2.1. **弹尖倒圆的形式**：当前实现为**与锥面相切的球面钝化**（`r_t = L·k_tip`，默认 7.38 mm）。若想要的是平面切平或锥面倒棱，属于另一种剖面构造，需要新增一个 `tip_mode` 分支。
2.2. **弹尖钝化会前移肩部**：为保证总长仍为 `L`，剖面整体后移 `r_t(1/sinα − 1)`，基准值下肩部从 0.49 m 前移到 0.4565 m。若要求"肩部到钝头仍为 `L_N`"，则锥角会随之变化，是另一种口径。
3. **倒圆无法直接回读半径**：`MeasureHelper.FilletRadius` 对旋转弧生成的圆环面抛异常，已改用"环面面积 + 两个切点面面积"三项解析核算（第七节第 6 条）。若将来改用真正的圆角特征，可换回直接回读。
3. **包围盒期望必须按相位与片数计算**：`D + 2·max(B)` 只适用于 4 片"+"相位；已改为按实际外形角点计算（11.4 节）。
4. **十字舵相位**：默认 `"+"`，可用 `fin_phase = "x"` 覆盖，该分支已真机验收（包围盒 0.46103 与解析值一致）。
5. **接口漂移**：已用 schema 一致性单测 + "模板内无裸数值"单测锁死。
6. **`.scdoc` 保存**：已确认 `DocumentSave.Execute(path)` 可按 `.scdoc` 扩展名保存；产物 115 KB 打开正常（真机）。
7. **`args` 可用性**：已确认 `/ScriptArgs` 传入的路径可由脚本内 `args[0]` 读取；同时保留 `SCDM_MISSILE_PARAMS` 环境变量兜底。
8. **许可与耗时**：单次执行约 23 秒（含 SpaceClaim 冷启动），每次占用一个实例；批量场景必须走 Task 4。
9. **版本绑定**：模板头部 API 版本取自安装布局（`…\\v241\\scdm\\SpaceClaim.exe` → `V241`），不取"最高 API 文件夹"，避免同机存在 `SpaceClaim.Api.V242` 时误判。
10. **单元与精度**：参数为 m、建模为 mm，换算集中在模板入口一处。
11. **`k_nose = 0` 的代码分支**：尖角与倒圆是两条剖面构造逻辑，两者都已单独验收。

## 十、参考来源

- 本仓库既有真实验收脚本与流程：`scripts/spaceclaim-circular-repeat-smoke.py`、`AGENTS.md`
- 命令行调用与参数传递（第三方实践，非官方文档）：
  - <https://www.topcfd.cn/40689/>
  - <https://www.topcfd.cn/40698/>
  - <https://www.topcfd.cn/40703/>

## 十一、真机验收记录

环境：本机 ANSYS SCDM 2024 R1（`AWP_ROOT<number>` 布局下的 `scdm\\SpaceClaim.exe`），headless 执行，产物全部写在系统临时目录。

### 11.1 API 取证（V241 CHM，标 `official-doc`）

| 用途 | 签名 |
| --- | --- |
| 草图平面 | `ViewHelper.SetSketchPlane(Plane)`，`Plane.PlaneXY` |
| 草图直线 | `SketchLine.Create(Point2D, Point2D, bool isConstruction = false, bool centerline = false)` |
| 草图圆弧 | `SketchArc.CreateSweepArc(Point2D center, Point2D start, Point2D end, bool sense)` |
| 草图实体化 | `ViewHelper.SetViewMode(InteractionMode, ICommandInfo)` |
| 旋转成实体 | `RevolveFaces.Execute(ISelection, Line axis, double angle, RevolveFaceOptions, ICommandInfo = null)` |
| 拉伸成实体 | `ExtrudeFaces.Execute(ISelection, Direction, double distance, ExtrudeFaceOptions, ICommandInfo = null)` |
| 拉伸类型 | `ExtrudeType`：`None/Add/Cut/ForceAdd/ForceCut/ForceIndependent/ForceNewSurface` |
| 圆周阵列 | `Pattern.CreateCircular(ISelection, CircularPatternData, ICommandInfo = null)`；`CircularPatternData.CircularCount / CircularAngle / CircularAxis` |
| 布尔并 | `Combine.Merge(ISelection, bool createImprints, ICommandInfo = null)` 等重载 |
| 保存 | `DocumentSave.Execute(string filename)` |
| 选择 | `Selection.Create(params IDocObject[])`、`BodySelection.Create(params IDesignBody[])`、`FaceSelection.Create(...)` |
| 测量 | `MeasureHelper.BodyExtrema / MeasureArea / MeasureVolume / ConeAngle / CylinderDiameter / FilletRadius` |
| 几何构造 | `Point.Create(double, double, double)`、`Point2D.Create`、`Line.Create(Point, Direction)`、`Direction.DirX/DirZ`、`MM()`、`DEG()` |
| 外壳（"准备 > 外壳"） | `Enclosure.Create(ISelection, EnclosureOptions, ICommandInfo = null)` → `EnclosureResult.CreatedBodies`（`ICollection<IDesignBody>`） |
| 外壳选项 | `EnclosureOptions.EnclosureType / EnclosureCushion / Frame / Subtract / CreateShareTopology / CushionProportion / CustomBody / Tolerance`；`EnclosureType`：`Box=0 / Cylinder=1 / Sphere=2 / CustomShape=3` |
| 外壳缓冲 | `CylinderEnclosureCushion(double axisLow, double axisHigh, double radius)`（`Axis` 为 `AxisCushion{Low, High}`）、`BoxEnclosureCushion(double, double, double, double, double, double)`、`SphereEnclosureCushion(double radius)` |
| 外壳坐标系 | `Frame.Create(Point origin, Direction dirZ)`（`Frame` 在 `SpaceClaim.Api.V241.Geometry`），`Frame.AxisX/AxisY/AxisZ/Origin` |
| 外壳对象 | 命令把 body 放进一个**新建 Component**；`DesignBody.Enclosure` 返回 `EnclosureAspect`（含 `SourceBodies` / `Update()`），但命令回读出来的是 `DesignBodyGeneral`，其 `Name` **只读** |
| 全文档实体枚举 | `Part.GetAllBodies()`（`PartExtensions`，含子组件里的 body）；`Part.Bodies` 只看本部件 |
| 命名选择 | `NamedSelection.Create(ISelection primary, ISelection secondary, string name, PartLocation type = Root, ICommandInfo = null)` → `CreateNamedSelectionGroupResult.Success` / `.CreatedNamedSelection`；`PartLocation`：`Root=0` / `Active=1` |
| 命名选择回读 | `Part.Groups` → `Group.Name`（可读写）、`Group.Members`、`Group.SecondaryMembers` |
| 视图取景 | `ViewHelper.ActivateNamedView(string name, bool zoomToExtent = false, bool animate = false)` **返回 bool**；`ViewHelper.ZoomToEntity(ISelection = null)`（不给选择就取全景）。实测 `ActivateNamedView("Isometric", True, False)` 在本机**返回 False**——名字不在宿主的视图表里，相机根本没动，出图仍然"成功"但是空的（PNG 只有 7 KB）。所以出图前必须显式 `ZoomToEntity`，并把 `ActivateNamedView` 的返回值记进诊断。 |
| 面的位置 | `MeasureHelper.GetCentroid(ISelection)`（仅面选择）→ `Point`；回转体的面质心落在轴上，据此把弹体面与舵面分开 |
| 物理抑制 | `ViewHelper.SetSuppressForPhysics(ISelection, bool)`；**无 getter**，`DesignBody.IsSuppressed` 是普通抑制、与此无关 |
| 组件内容与删除 | `ComponentExtensions.GetComponent/GetAllBodies()` 等扩展方法（`Component` 本身**没有** body 属性）；`Component.Delete()`（继承自 `DocObject`） |

### 11.2 执行链路（`runtime-verified`）

`SpaceClaim.exe /RunScript=model.py /ScriptArgs=params.json /Headless=True /ExitAfterScript=True`，脚本内 `args[0]` 取参数文件；单次运行约 23 秒，退出码 0。

### 11.3 几何验收（基准参数 + 默认倒圆配置 `fillet_location = "both"`，`runtime-verified`）

| 检查 | 实测 | 解析期望 |
| --- | --- | --- |
| 合并后实体数 | 1（45 个面） | 1 |
| 轴向长度 | 3.69 m | `L = 3.69`（钝化后仍为总长） |
| 横向/竖向 | 0.64 / 0.64 m | `按相位与片数算出的包围盒` |
| 旋转体面数 | 5 | 弹尖弧 + 锥面 + 交接弧 + 柱面 + 底面 |
| 旋转体体积 | 0.0864365432 | 0.0864365583 |
| **弹尖倒圆面** | 0.0002803892 | 0.0002803892（`r_t = L·k_tip = 7.38 mm`） |
| 交接处倒圆面 | 0.0018930508 | 0.0018930508（`r_f = 18.45 mm`） |
| 锥面 / 柱面 / 底面 | 0.1389970 / 1.8275352 / 0.0254469 | 同左（逐段 Pappus） |
| `.scdoc` | 123 KB，运行内新生成 | 新生成 |
| `preview.png` | 等轴视图快照（`Window.ActiveWindow.Export(WindowExportFormat.Png, …)`） | 供人工目视 |

### 11.3.2 仿真区域（`runtime-verified`）

导弹、外壳、BOI 在**同一个文档**里生成，一次运行一个文件，全部验收通过（共 45 项检查）。外壳由官方"准备 > 外壳"命令生成，落在新建组件 `Enclosure` 中；BOI 是根部件下的旋转实体。基准参数（`model_placement = "ensemble"`，居中站位 5.38 m，`model_offset = −4.0 m`，最终站位 1.38 m）下：

| 区域 | 生成方式 | 实测尺寸 (m) | 解析尺寸 (m) | 实测体积 (m³) | 解析体积 (m³) | 轴向范围 (m) | 实测中位 (m) | 面数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 外壳（圆柱，组件名 `Enclosure`） | `Enclosure.Create` + `Frame` 转到 X 轴 | 23.82995 × 5.4 × 5.4 | 23.83 × 5.4 × 5.4 | 545.7694 | 545.7597 | `[-3.69, 20.14]` | 8.225027 | 3 |
| 加密区 BOI（圆柱，实体名 `BOI`） | 旋转矩形剖面 | 6.69 × 0.9 × 0.9 | 6.69 × 0.9 × 0.9 | 4.2560369 | 4.2559941 | `[0.88, 7.57]` | 4.224983 | 3 |
| 导弹本体（合并后单一实体，实体名 `Missile`） | 旋转 + 4 片舵阵列 + `Combine.Merge` | 3.69 × 0.64 × 0.64 | 同左 | 0.0864365（旋转体；含舵面的合并体量到 0.0910481） | 0.0864366 | `[1.38, 5.07]` | 质心 3.3696709 | 45 |

**定位**：`ensemble` 居中站位 = `(16.45 − 3.69)/2 − (2.5 − 0.5)/2 = 6.38 − 1.00 = 5.38 m`，再加 `model_offset = −4.0 m` → 最终站位 **1.38 m**。于是 BOI 前端面到外壳前端面 4.57 m、后端面到后端面 12.57 m，即整体比正中**靠前 4.0 m**（解析恒等式与实测差两项都验到：实测 `4.224983 − 8.225027 = −4.000044` m，容差 1 mm；外壳中位的 4.4e-5 m 偏差来自命令自身的数值精度）。导弹本体的中位（3.225）又比外壳中位靠前 5.0 m，其中 1.0 m 是 BOI 自己的不对称量 `(2.5 − 0.5)/2`，另外 4.0 m 是 `model_offset`；填 `model_offset = 0` 即正中，换 `model_placement = "missile"` 则改成导弹本体居中。此时外壳的轴向缓冲是 `(5.07, 15.07)`，仍为正，弹尖离外壳前端面 5.07 m。

外壳的径向缓冲换算：`2.7 − hypot(0.64/2, 0.64/2) = 2.7 − 0.4525483 = 2.2474517`，命令回读半径 2.69999999，与目标 2.7 相差 4e-9。文档内实体数为 3（`Missile / BOI / <unreadable>`，外壳 body 名字只读不可回读），组件名列表是 `["Enclosure"]`（ASCII，可回读）；导弹本体仍为合并后的单一实体（45 个面），尺寸与 11.3 节一致。体积相对偏差约 2e-5，来自草图/旋转的数值精度，远低于 2e-3 的验收容差。

> **踩坑记录 1**：第一版把实体名设成中文（"外壳"），脚本宿主在回读 `body.Name` 时抛 `UnicodeDecodeError: ('unknown', u'\xe5', …)`，整个建模中断。现在的处理是：名字默认取 ASCII 的 `Enclosure` / `BOI`（可回读，验收同时要求读回的组件名列表里确实有它），非 ASCII 名字仍然**支持**——`enclosure_name` / `boi_name` 可填任意字符串，只是回读会退化成 `<unreadable>`，验收就退回到 `String.Equals` 比对。两条路径各有单测。
>
> **踩坑记录 2**：外壳最初是自绘的旋转实体。改用官方命令后连踩三个坑：默认圆柱轴是**全局 Z**（不设 `Frame` 外壳就横躺），`Subtract` 默认真会把导弹抠掉，`DesignBodyGeneral.Name` **只读**（因此外壳的标识只能放在组件上）；另外命令把外壳放进组件后 `GetRootPart().Bodies` **看不到它**，实体计数必须改用 `GetAllBodies()`，否则会误判成"少了外壳"。

### 11.3.1 其它倒圆配置（`runtime-verified`）

| 配置 | 旋转体面数 | 实测面面积 | 解析期望 |
| --- | --- | --- | --- |
| `cone_cylinder`（仅交接处） | 4 | 0.1399132083 / 0.0018930508 / 1.8086071623 / 0.0254469 | 完全一致 |
| 两者皆无（尖头尖肩） | 3 | 0.1408618120 / 1.8095573685 / 0.0254469 | 完全一致 |

覆盖参数复测（`k_nose=0.008`、`t_fin=0.015`、`L_W=1.6`、`B_W=0.26`、`C_TW=0.05`，经 MCP `build_missile_model` 调用）：11 项检查全部通过，`r_f` 变为 29.52 mm、展向变为 0.70 m，均与解析值一致，证明参数覆盖在真实链路上生效。

### 11.4 参数覆盖扫描（`runtime-verified`）

逐项覆盖代表性参数并整轮验收，全部通过：

| 覆盖 | 实测包围盒 (m) | 解析期望 (m) | 面数 |
| --- | --- | --- | --- |
| 默认基准 | 3.69 / 0.64 / 0.64 | 3.69 / 0.64 / 0.64 | 44 |
| `fin_phase = "x"` | 3.69 / 0.4610336 / 0.4610336 | 3.69 / 0.4610336 / 0.4610336 | 44 |
| `cruciform_count = 6` | 3.69 / 0.64 / 0.5602563 | 3.69 / 0.64 / 0.5602563 | 64 |
| `L=4.0, D=0.2, L_N=0.5` | 4.0 / 0.66 / 0.66 | 4.0 / 0.66 / 0.66 | 44 |
| `root_overlap = 0.01` | 3.69 / 0.64 / 0.64 | 3.69 / 0.64 / 0.64 | 44 |
| `L_F/C_RF/C_TF/B_F` 改后舵 | 3.69 / 0.74 / 0.74 | 3.69 / 0.74 / 0.74 | 44 |
| `L_W/C_RW/C_TW/B_W` 改前舵 | 3.69 / 0.62 / 0.62 | 3.69 / 0.62 / 0.62 | 44 |
| `k_nose = 0`（尖角分支） | 3.69 / 0.64 / 0.64 | 3.69 / 0.64 / 0.64 | 44 |

> **扫描发现并修掉的缺陷**：原先的验收用 `D + 2·max(B_W, B_F)` 作为横向/竖向期望，这只对"4 片 + 十字相位"成立。`fin_phase = "x"` 时实测 0.46103（几何正确），却被判为失败。现在改为按**实际相位与片数**逐片计算外形角点得到真实包围盒——这既修掉了误判，也让该检查同时证明了相位与片数确实生效（预测值与实测值在 12 位有效数字内一致）。

## 后续变更

舵面横截面改为对称 NACA 四位数。schema 使用 `thickness_percent`、`le_radius_index`、`max_thickness_tenths`，平面形状仍由弦长与展长决定。上文里的 `t_fin` 记录保留为当时的等厚度实现。
