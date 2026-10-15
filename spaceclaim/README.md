# ansys-scdmdoc-mcp

**中文** | [English](README.en.md)

![ANSYS SpaceClaim MCP](docs/assets/circular-pattern-hero.png)

用 MCP 更快找到 SpaceClaim 的 Python 建模命令。

## 你是否遇到过这些问题？

在 SpaceClaim 中进行几何建模时，很多操作并不是“知道功能名称”就能直接写出 Python：

- 不知道“几何体命名”“共享拓扑”“圆周阵列”等功能对应哪个命令；
- 除了打开脚本录制器，不知道还有什么方法可以获取可用脚本；
- 官方提供了 CHM 帮助文档，但 CHM 只是汇总了大量 HTML 页面；
- API 文档通常以 C#/.NET 签名为主，类型、命名空间、参数和返回值不容易理解；
- 即使搜到了一个方法，也很难判断它能否组合成真正可运行的 SCDM Python 脚本。

如果不是经验非常丰富的 SpaceClaim 二次开发用户，直接阅读 CHM 往往需要花费大量时间。

## 我们的解决方法：用 MCP 做 SpaceClaim API 助手

MCP（Model Context Protocol，模型上下文协议）是一种让 AI 应用安全调用外部工具和数据源的标准协议。它可以把本地的文档、数据库、代码工具或业务服务，以结构化工具的形式提供给 AI。

在本项目中，MCP 连接的是用户电脑上 ANSYS SpaceClaim/SCDM 安装目录中的官方 API CHM。AI 不需要凭记忆猜命令，而是可以通过 MCP：

1. 搜索官方 SpaceClaim API 文档；
2. 读取对应的类、方法或属性说明；
3. 根据你的建模意图整理相关 API；
4. 返回一段带有证据边界的 SCDM Python 候选代码；
5. 在需要时继续生成有顺序的几何建模步骤。

这样可以把“翻阅大量 CHM 页面”变成“用自然语言快速定位命令”，减少 SpaceClaim 几何建模中的试错时间。

![MCP setup and search flow](docs/assets/setup-flow.svg)

## 两种主要使用方式

### 方式一：直接向 MCP 提问功能对应的命令

当你只知道想实现什么功能，但不知道 SpaceClaim 命令时，可以直接描述目标。

例如：

```text
SpaceClaim 中如何给几何体创建命名选择？
```

```text
SpaceClaim 中如何创建共享拓扑？请给出相关 API 和 Python 候选。
```

```text
先创建一个 0° 的实体，再用圆周阵列每隔 60° 生成共 6 个实体，最后合并，应该使用哪些命令？
```

MCP 会优先检索官方 CHM 中的相关 API，例如：

```text
Pattern.CreateCircular
CircularPatternData
Combine.Merge
```

然后返回命令含义、官方文档页面、参数信息和可进一步验证的 Python 候选。

这种方式适合：

- 查询某个 SpaceClaim 功能对应的 API；
- 了解一个命令需要哪些输入；
- 快速比较多个可能的建模方法；
- 让 AI 帮你把多个 API 组织成建模步骤。

### 方式二：提供完整建模输入，让 AI 按参数复现

如果你已经有明确的建模任务，可以把输入参数、几何约束、参考图片和目标结果一起提供给 AI，让它按照这些信息生成复现脚本。

建议提供：

- 几何尺寸和单位；
- 点、线、面、轴、坐标系等基准信息；
- 阵列数量、角度、间距和旋转方向；
- 拉伸、倒角、圆角、布尔运算等操作顺序；
- 命名选择、共享拓扑或装配关系要求；
- 输入图片、草图、截图或目标几何示意图；
- 最终验收标准，例如实体数量、角度、尺寸和输出文件。

例如：

```text
请根据下面的参数和图片复现一个空天飞机外形：

- 单位：mm
- 机身长度：1200
- 翼展：900
- 左右结构对称
- 发动机舱沿机身轴线布置
- 需要创建命名选择：Fuselage、LeftWing、RightWing、Engine
- 最后检查实体数量和命名是否正确
```

这种方式适合：

- 根据图片或已有设计复现几何体；
- 生成参数化建模脚本；
- 把复杂建模任务拆分成可验证的阶段；
- 对已有 SpaceClaim 脚本进行补全、重构或排错。

## 这个 MCP 提供什么工具？

| 工具 | 用途 |
| --- | --- |
| `search_api` | 搜索官方 CHM 中的类、方法、属性、事件和几何关键词 |
| `get_doc` | 读取搜索结果对应的单个官方 API 页面 |
| `find_python_command` | 根据自然语言建模意图返回 API 关联的 Python 候选 |
| `get_geometry_recipe` | 将几何任务整理为有顺序的建模步骤 |
| `validate_missile_params` | 校验一组导弹几何参数，返回错误、警告与自动修正（不依赖 ANSYS） |
| `generate_model_script` | 生成 `params.json` 与 SCDM 脚本，可在 GUI 中手动试跑（不依赖 ANSYS） |
| `build_missile_model` | headless 调用 SpaceClaim 建出导弹模型并存成 `.scdoc`，再对产物做验收 |

示例意图：

```text
圆周阵列 6 个，每隔 60 度，生成后合并
```

该意图会关联圆周阵列和合并操作，并返回：

```python
Pattern.CreateCircular(...)
Combine.Merge(...)
```

## 参数化导弹建模

给一组几何参数，直接产出 `.scdoc` 文件。参数全部可改、可被覆盖：

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `L_N` / `L` / `D` | 鼻锥长度 / 弹身总长 / 机身直径 | 0.49 / 3.69 / 0.18 m |
| `L_W` `C_RW` `C_TW` `B_W` | 前十字舵位置 / 根弦 / 梢弦 / 展长 | 1.72 / 0.23 / 0.06 / 0.23 m |
| `L_F` `C_RF` `C_TF` `B_F` | 后十字舵位置 / 根弦 / 梢弦 / 展长 | 3.2 / 0.38 / 0.19 / 0.22 m |
| `t_fin` | 舵面厚度 | 0.012 m |
| `k_nose` | 鼻锥与柱段交接处倒圆半径系数（`r_f = L · k_nose`） | 0.005 |

还有一组可覆盖的约定参数：`fin_phase`（十字舵相位 `+` / `x`）、`root_overlap`（舵根嵌入量）、`le_mode`、`fillet_location`（倒圆位置：交接处 / 弹尖 / 两者）、`cruciform_count`、`scdm_api_version`；以及 `k_tip`（弹尖钝化半径系数）。

**坐标基准与舵面剖面**：模型自己的基准是**弹尖**——钝化后球面盖的最前点，弹尾在它的 `+L` 处。放进外壳时整体落在 `nose_tip_x`（默认 1.38 m = 整体居中站位 5.38 − 4.0，见下文 `model_placement`），所以绝对坐标下弹尖在 `x = 1.38`、弹尾在 `x = 5.07`。无论怎么放，`L_W` / `L_F` 都是字面上"舵面**可见前缘**到弹尖的距离"，钝化与否都成立（实测 1.7200 / 3.2000 m）。

设计梯形定义在**弹体表面**上：那里量到的根弦就是 `C_RW` / `C_RF`，前缘就在 `L_W` / `L_F`。`root_overlap`（默认 5 mm）只是建模手段——舵根要沉进弹体，布尔并才干净；沉进去的那段沿设计边延伸，因此**可见前缘和可见根弦都精确等于参数值**（埋在里面的根弦更长：基准值 233.7 / 384.3 mm）。

钝化会把鼻锥缩短并把锥柱交接处朝弹尖拉近（基准值 33.472 mm，0.490 → 0.4582），这是"总长 = `L`"与"弹尖固定在给定站位"同时成立的必然结果；舵面**不做平移**。该量在 `params.json` 的 `tip_offset` / `nose_junction_x` 里直接给出。

一次运行产出**一个** `missile.scdoc`，里面是三个可独立识别的对象，可直接带进 Fluent Meshing：

| 对象 | 内容 | 关键参数 |
| --- | --- | --- |
| `Missile` | 导弹本体（合并后的单一实体） | 13+1 个几何参数 |
| `Enclosure` | 外壳（外流场远场边界），名字取 `enclosure_name` | `enclosure_radius` / `enclosure_front` / `enclosure_back` |
| `BOI` | 加密区（网格细化区域），名字取 `boi_name` | `boi_radius` / `boi_front` / `boi_back` |

外壳**不是**手画的旋转实体，而是调用官方"准备 > 外壳"（`Enclosure.Create`）命令生成的，与菜单里点出来的是同一个特性：

- 圆柱轴转到全局 X（`EnclosureOptions.Frame`），默认方向是全局 Z，不设就不是沿弹轴。
- `Subtract = False`：远场边界要整块，不能在中间把导弹抠掉。
- 命令按"包围盒 + 缓冲（cushion）"算尺寸：轴向缓冲就是两端的外伸量，径向缓冲要**减去包围盒中心到角点的距离**才是绝对半径。参数表里填的仍然是绝对尺寸，换算在 Node 侧完成并单测。
- 命令会把外壳放进一个**新建的组件**里，而且生成出来的 body 的 `Name` 是只读的；所以外壳的标识落在**组件名**上，脚本把该组件命名为 `enclosure_name` 的值（默认 **`Enclosure`**）。

> **名字怎么处理的**：名字不在脚本里写死，而是通过 `params.json` 以 UTF-8 传入，脚本源文件保持**纯 ASCII**（有单测锁住）。`enclosure_name` / `boi_name` 接受任意字符串：默认的 ASCII 名字能正常回读，验收就**同时**要求 `String.Equals` 比对通过**且**读回的组件名列表里确实有它（设到别的对象上会被抓住，实测 `componentNames = ["Enclosure"]`）；若改成中文（例如 `外壳`），宿主读回时会把 .NET 字符串转 Python `str` 的步骤抛 `UnicodeDecodeError`（早先这个异常直接中断过建模），于是回读退化为 `<unreadable>`，验收改用 `String.Equals` 把期望值当 .NET 字符串传进去比，**不把"读不回"当失败**——两条路径各有单测锁住。

外壳长度由 `enclosure_front` / `enclosure_back` 决定：当前默认前伸 3.69 m（弹尖之前）、后伸 **16.45 m**（弹尾之后），总长 23.83 m。缩短只动 `enclosure_back`，前端 `x = -3.69` 不变；验收里有**轴向中位**一项专门证明长度是从哪一端缩掉的（只缩尾端时中位在 8.225 m，两端各缩 1 m 会是 9.225 m，包围盒完全看不出区别）。

**先居中，再按 `model_offset` 微调**（外壳是基准、不动，导弹和 BOI 一起走）。`model_placement` 决定"中心"怎么算：

| 模式 | 含义 | 居中站位 |
| --- | --- | --- |
| `ensemble`（默认） | **导弹 + BOI 整体**居中——BOI 总是把导弹整个包住，所以这个整体的外轮廓就是 BOI | `(enclosure_back − enclosure_front)/2 − (boi_back − boi_front)/2 = 6.38 − 1.00 = 5.38 m` |
| `missile` | 只让**导弹本体**居中 | `(enclosure_back − enclosure_front)/2 = 6.38 m` |
| `offset` | 不用居中规则，站位就是 `model_offset` 本身 | `0` |

`model_offset`（默认 **−4.0 m**）是**在居中站位之上再加的轴向偏移**，三种模式都生效：沿 +X 为正（往弹尾），**负值往弹尖、也就是靠前**。所以默认 = 整体居中后**再往前 4000 mm**，最终站位 `5.38 − 4.0 = 1.38 m`。

默认下的实测布局：

| | 位置 (m) | 中位 (m) |
| --- | --- | --- |
| 外壳 | `[-3.69, 20.14]` | **8.225**（不动） |
| BOI | `[0.88, 7.57]` | 4.224983（比外壳中位靠前 4.000044 m） |
| 导弹 | `[1.38, 5.07]` | 3.225 |

于是 BOI 前端面到外壳前端面 = **4.57 m**、后端面到后端面 = **12.57 m**，整体比正中**靠前 4.0 m**；导弹自己的中位（3.225）比外壳中位靠前 5.0 m，其中 1.0 m 是 BOI 自身的不对称（前伸 0.5 m、后伸 2.5 m），另外 4.0 m 就是你给的 `model_offset`。想正好居中就把 `model_offset` 填 `0`，想让导弹本体居中就把 `model_placement` 改成 `"missile"`（此时导弹中位 = 外壳中位 + `model_offset`）。三种模式和任意偏移都只是换个数，不用改代码。

模型不是建完之后再平移的：模式解析出的站位（居中站位 + `model_offset`）直接进剖面与舵面站位，所以解析式（质心、站位、尺寸）与实测永远在同一个绝对坐标系里，验收的**旋转体轴向质心**一项就顺带证明了模型确实落在 `nose_tip_x = 站位` 处（1.38 m 不生效的话，实测会比解析值小整整 1.38 m）。外壳的缓冲随之补偿（`axisLow = enclosure_front + 站位`、`axisHigh = enclosure_back − 站位`），所以边界本身不动。

> **出图取景踩过的坑**：`ViewHelper.ActivateNamedView("Isometric", True, False)` 在本机**返回 `False`**（名字不在宿主的视图表里），相机根本没动，而 `Window.ActiveWindow.Export` 照样"成功"——导出的是空画面（PNG 只有 7 KB）。现在出图前显式调 `ViewHelper.ZoomToEntity`（导弹图给选择、装配图给全景），并把 `ActivateNamedView` 的返回值记进诊断。修完 PNG 从 7 KB 回到 49 KB，画面里能看到导弹和外壳圆柱。

加密区 `BOI` 没有对应的官方命令，仍按旋转实体生成，是根部件下的独立实体。

### CFD 命名选择（Named Selection）

同一次运行会在**同一个 `.scdoc`** 里建好 7 组命名选择，Fluent Meshing 直接认：

| 组名 | 内容 | 默认面数 | 默认面积 (m²) |
| --- | --- | --- | --- |
| `inlet` | 外壳前端盖（弹尖上游） | 1 | 22.9022 |
| `wall` | 外壳圆柱侧面 | 1 | 404.2655 |
| `outlet` | 外壳后端盖（弹尾下游） | 1 | 22.9022 |
| `missile_nose_tip` | 弹尖球面倒圆 | 1 | 0.000280 |
| `missile_nose` | 圆锥面 | 1 | 0.138997 |
| `missile_body` | 柱面 + 锥柱交接圆角 + 弹底端面 | 3 | 1.825568 |
| `missile_fin` | 前后舵面，全部 8 片 | 40 | 0.830673 |

`inlet` / `outlet` 按导弹朝向定：导弹指向 -X（弹尖朝低 x 端），所以外壳低 x 端盖是 inlet、高 x 端盖是 outlet，中间那个圆柱面是 `wall`。

**"导弹所有面不遗漏"是被证明的，不是被声明的**：

1. 分类靠几何，不靠猜——回转体的每个面质心都落在轴上（舵面孔洞对称，不会把质心推偏），舵面全部在轴外，两族天然分开；弹体族再按解析 Pappus 面积逐段配对。柱面被舵面裁过、没有解析面积，所以用排除法取剩下的那一个面。
2. 建完把每组的面数与面积**从文档里读回来**。
3. 验收要求：4 组导弹选择的面数之和 = 实体总面数（45），并且**面积之和 = 实体自身表面积**（基准 2.7955190015 m²，实测相对误差 1e-15）。漏一个面、或把面分错组，这个和就对不上。

`missile_nose_tip` 只在 `k_tip > 0` 时有面（尖头时为 0 面，不建组）；`missile_nose` 只含锥面，**锥柱交接圆角归在 `missile_body`**——若你想让它跟锥面一组，说一声。

### 结构清理与物理抑制

**阵列残留的空组件会被删除。** `Pattern.CreateCircular` 会给每一组阵列实例各建一个组件，`Combine.Merge` 把 body 并进导弹后，组件就空着留在结构树里。脚本在建模完成后把它们删掉——但**删之前先逐个确认里面确实没有 body**（用组件自己的 `GetAllBodies()` 扩展方法读），删完再复核 body 总数没变，否则直接报错中断。

**参与物理计算的只有 Missile / BOI / 外壳。** 清掉空组件后结构树里剩下的其他东西用 `ViewHelper.SetSuppressForPhysics` 标记抑制（**不是删除**）：

| 对象 | 类型 | 处理 |
| --- | --- | --- |
| `Missile` | DesignBody | 保留 |
| `BOI`（名字取 `boi_name`） | DesignBody | 保留 |
| 外壳 body（在 `Enclosure` 组件内，名字只读、回读为 `<unreadable>`） | DesignBodyGeneral | 保留 |
| `Enclosure`（名字取 `enclosure_name`） | Component | 保留 |
| 空组件 ×4（圆周阵列残留） | Component | **删除** |
| 默认坐标系 | CoordinateSystem | 抑制 |

清单不是写死的：脚本遍历根部件下的 `Bodies` / `Components` / `Curves` / `DatumPlanes` / `DatumLines` / `DatumPoints` / `CoordinateSystems`，再补上 `GetAllBodies()`（组件里的 body 不在 `Part.Bodies` 里，只在这里出现），把保留集之外的全部处理掉，并逐个报告类型、名称与结果。保留集按**对象身份**判定、不按名字（外壳 body 的名字只读）；`enclosure_name` 这个名字是否真的设上去了，ASCII 下靠回读组件名列表证明、非 ASCII 下靠 `String.Equals` 比对证明。命名选择是 Group、不是几何，不在遍历范围里，不会被误删或误抑制。

> **证据边界**：SpaceClaim **没有**"是否已物理抑制"的取值接口（只有一个 setter）。所以能证明的是：遍历清单被完整回报、保留集恰好是上面 4 个、其余每个对象的调用都成功、4 个被删组件删前 body 数都是 0、删后 body 总数不变。**抑制状态本身无法回读**，这一点不冒充 `runtime-verified`。

命令行：

```powershell
node scripts/missile.mjs --list                 # 查看全部参数与默认值
node scripts/missile.mjs --k_nose 0.008 --out D:\models
node scripts/missile.mjs --preset my.json --generate-only
```

产物（`params.json`、`model.py`、`missile.scdoc`、`result.json`）写在仓库之外的工作目录，不进入版本库。

**验收门槛**：脚本运行不报错、进程退出码为 0、`.scdoc` 文件存在，都**不算**成功。每次生成都会检查（当前 45 项）：合并后实体数为 1、轴向与展向尺寸、旋转体体积、倒圆面与锥面/柱面的解析面积、**旋转体轴向质心**（钉住弹尖基准与整体轴向站位）与**每个舵面的可见前缘站位**（钉住 `L_W` / `L_F`，绝对容差 0.1 mm——包围盒、面积、体积对轴向平移不敏感，只有质心看得出来）、外壳确实由"准备 > 外壳"命令生成（独立组件 + 组件名 = `enclosure_name` + 缓冲为正）、每个仿真区域的尺寸与体积**与轴向中位**（证明长度是缩在哪一端的）、**定位规则被独立复算一遍且实测中位差 = `model_offset`**（居中站位 + 偏移的解析等式钉到 1e-12，两个实体的中位差实测钉到 1 mm）、**7 组命名选择都已创建且导弹 4 组的面数与面积之和等于实体自身**（证明没有面被漏掉或分错组，`inlet`/`wall`/`outlet` 面积分别对端盖与侧面解析值）、**阵列残留的空组件已删除且删前确认过是空的、删后结构里只剩外壳一个组件**、**物理抑制的保留集恰好是 Missile / BOI / 外壳四个对象、其余对象全部抑制成功**、以及所有文件是否本次运行新生成；全部满足才报告为 `runtime-verified`。

设计与真机验收记录见 [docs/superpowers/plans/2026-09-17-missile-parametric-scdoc.md](docs/superpowers/plans/2026-09-17-missile-parametric-scdoc.md)。

## 安装与首次配置

要求：Windows、Node.js 18+，以及本机已经安装 ANSYS SpaceClaim/SCDM。

```powershell
git clone <your-github-repository-url>
cd ansys-scdmdoc-mcp
npm ci
npm run setup
node server.mjs
```

首次执行 `npm run setup` 会打开本地浏览器配置页。配置页提供两种选择：

1. 根据 `AWP_ROOT<number>` 自动检测；
2. 手动输入 `SpaceClaim.exe` 的应用程序路径。

在自动检测模式下，程序会扫描当前环境变量中所有版本化的 AWP_ROOT，并通过下拉框让用户选择。例如：

```text
AWP_ROOT241
AWP_ROOT251
AWP_ROOT261
```

点击确认后，程序会验证所选版本对应的官方 CHM 是否存在。如果验证失败，会显示：

```text
无法根据 AWP 确定环境变量的路径，无法确定 SpaceClaim 的路径
```

并禁用自动检测，只允许用户手动输入 `SpaceClaim.exe` 路径。

配置保存在：

```text
%LOCALAPPDATA%\ansys-scdmdoc-mcp\config.json
```

程序不会把用户电脑上的 ANSYS 安装路径写入代码或 README。手动模式保存的是应用程序路径，CHM 会从对应的 SCDM 安装目录自动查找。

## 路径和文档来源

路径解析优先级如下：

1. 显式 `SCDM_CHM`；
2. 首次配置保存的 AWP 或 SpaceClaim 路径；
3. 所有可用 `AWP_ROOT<number>` 中版本号最高者；
4. 通用 `AWP_ROOT` + `SCDM_API_VERSION`。

首次读取 CHM 时，程序使用 Windows 的 `hh.exe` 将帮助文档解包到用户缓存目录，并建立搜索索引：

```text
%LOCALAPPDATA%\ansys-scdmdoc-mcp\cache
```

项目只查询用户本机的官方 CHM，不在仓库中分发或保存 ANSYS 专有文档。

## 如何理解返回结果？

本项目明确区分三类证据：

- `official-doc`：来自官方 SpaceClaim CHM 的页面、类型、方法或属性；
- `inferred`：根据官方 API 签名和建模意图推导出的 Python 候选；
- `runtime-verified`：已经在目标 SpaceClaim/SCDM 安装中实际执行，并检查过模型结果。

尤其要注意：CHM 中常见的是 C#/.NET 签名。它可以证明 API 文档存在，但不能单独证明某段 IronPython 代码已经运行成功。因此，MCP 返回的 Python 代码仍应在目标 SCDM 版本中执行验证。

## 真实几何验收案例

项目提供了一个真实 SpaceClaim 验收脚本：[spaceclaim-circular-repeat-smoke.py](scripts/spaceclaim-circular-repeat-smoke.py)。它不是简单地循环 `Copy` 和 `Move.Rotate`，而是验证完整的圆周阵列与合并流程：

```text
创建 0° 基体
    ↓
Pattern.CreateCircular
    ↓
CircularPatternData：6 个实例、360° 总角度
    ↓
检查 0/60/120/180/240/300°
    ↓
Combine.Merge
    ↓
最终 1 个合并实体
```

验收必须同时检查：

- 阵列实体数量为 6；
- 角度间隔为 60°；
- 覆盖完整 360°；
- `Combine.Merge` 成功；
- 合并后的实体数量为 1；
- 模型文件和 `status=PASS` 结果文件都是本次运行新生成的。

MCP 查询成功、缓存建立成功或 SpaceClaim 进程返回码为 0，都不能单独替代真实几何验收。

## 开发与测试

```powershell
npm ci
npm test
npm run smoke
```

如果需要在本机验证真实 CHM：

```powershell
$env:SCDM_RUN_REAL_CHM_TEST = "1"
npm test
```

真实 SpaceClaim 几何验收需要目标电脑安装有效的 ANSYS SpaceClaim/SCDM，并且输出文件应保存到仓库之外的临时目录。

## 发布和版权边界

发布到 GitHub 前，请确认以下内容没有被提交：

- ANSYS 或 SpaceClaim CHM 文件；
- CHM 解包后的 HTML、HHK、HHC 文件；
- 用户本地缓存和索引；
- SpaceClaim 模型和运行日志；
- 用户配置、凭据和机器专属安装路径。

本项目只发布 MCP 实现、测试和说明文档，不发布 ANSYS 专有文档。ANSYS、SpaceClaim、SCDM 及相关产品名称属于各自权利人。本项目不代表 ANSYS 官方，也不声明与 ANSYS 存在授权或隶属关系。

项目代码使用 MIT License，详见 [LICENSE](LICENSE)。
## 工作区运行与真实验收

本项目位于工作区的 spaceclaim 项目根。`npm run verify:geometry` 真实执行圆周阵列与合并样例；`npm run verify:real` 仅检查官方 CHM 文档及索引，不证明几何运行成功。

参数化建模入口保留原命令行与 MCP 输入方式：

```powershell
node scripts/missile.mjs --list                                  # 全部参数与默认值
node scripts/missile.mjs --preset ..\data_lib\inputs\missile_params_example.json
node scripts/missile.mjs --k_nose 0.006 --out D:\models
```

只填写图中 11 个主尺寸（`L_N` `L` `D` `L_W` `C_RW` `C_TW` `B_W` `L_F` `C_RF` `C_TF` `B_F`）的示例见
`../data_lib/inputs/missile_params_example.json`；其余几何与区域配置沿用旧版默认值，仍可逐项覆盖。

工作区默认运行文件位于 `../data_lib/runs`，异常包位于 `../feedback/spaceclaim`；`data_lib/runs` 与
`feedback` 都按工作区配置解析，不依赖调用者的临时目录。安装选择仍由 `npm run setup` 或 AWP 环境
变量负责，工作区入口见 `../README.md`。MCP 标准输出只承载 JSON-RPC。仅生成脚本（`--generate-only`
或 MCP `execute:false`）的记录状态为 `GENERATED`，`runtimeVerified` 恒为 false。

