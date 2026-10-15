# ansys-scdmdoc-mcp

![ANSYS SpaceClaim MCP](docs/assets/circular-pattern-hero.png)

用 MCP 更快找到 SpaceClaim 的 Python 建模命令。  
Use MCP to find SpaceClaim Python modeling commands faster.

## 你是否遇到过这些问题？ / Problems this project addresses

在 SpaceClaim 中进行几何建模时，很多操作并不是“知道功能名称”就能直接写出 Python：  
When modeling geometry in SpaceClaim, knowing the feature name is often not enough to write Python directly:

- 不知道“几何体命名”“共享拓扑”“圆周阵列”等功能对应哪个命令；  
  You may not know which command implements named geometry, shared topology, or a circular pattern.
- 除了打开脚本录制器，不知道还有什么方法可以获取可用脚本；  
  You may not know how to discover usable scripts beyond opening the script recorder.
- 官方提供了 CHM 帮助文档，但 CHM 只是汇总了大量 HTML 页面；  
  The official help is provided as a CHM file, which is essentially a large collection of HTML pages.
- API 文档通常以 C#/.NET 签名为主，类型、命名空间、参数和返回值不容易理解；  
  API documentation is usually expressed as C#/.NET signatures, making types, namespaces, parameters, and return values difficult to interpret.
- 即使搜到了一个方法，也很难判断它能否组合成真正可运行的 SCDM Python 脚本。  
  Even after finding a method, it can be difficult to tell whether it can be combined into a runnable SCDM Python script.

如果不是经验非常丰富的 SpaceClaim 二次开发用户，直接阅读 CHM 往往需要花费大量时间。  
Unless you are an experienced SpaceClaim automation developer, searching the CHM directly can take considerable time.

## 我们的解决方法：用 MCP 做 SpaceClaim API 助手 / Our approach: an MCP assistant for the SpaceClaim API

MCP（Model Context Protocol，模型上下文协议）是一种让 AI 应用安全调用外部工具和数据源的标准协议。它可以把本地的文档、数据库、代码工具或业务服务，以结构化工具的形式提供给 AI。  
MCP (Model Context Protocol) is a standard protocol that lets AI applications call external tools and data sources. It can expose local documentation, databases, code tools, or business services as structured tools.

在本项目中，MCP 连接的是用户电脑上 ANSYS SpaceClaim/SCDM 安装目录中的官方 API CHM。AI 不需要凭记忆猜命令，而是可以通过 MCP：  
This project connects MCP to the official API CHM installed with ANSYS SpaceClaim/SCDM on the user’s computer. Instead of guessing from memory, an AI can use MCP to:

1. 搜索官方 SpaceClaim API 文档；  
   Search the official SpaceClaim API documentation;
2. 读取对应的类、方法或属性说明；  
   Read the relevant class, method, or property documentation;
3. 根据你的建模意图整理相关 API；  
   Organize related APIs around your modeling intent;
4. 返回一段带有证据边界的 SCDM Python 候选代码；  
   Return an SCDM Python candidate with an explicit evidence boundary;
5. 在需要时继续生成有顺序的几何建模步骤。  
   Generate ordered geometry-modeling steps when needed.

这样可以把“翻阅大量 CHM 页面”变成“用自然语言快速定位命令”，减少 SpaceClaim 几何建模中的试错时间。  
This turns “searching through many CHM pages” into “locating commands with natural language” and reduces trial and error in SpaceClaim geometry modeling.

![MCP setup and search flow](docs/assets/setup-flow.svg)

## 两种主要使用方式 / Two main usage patterns

### 方式一：直接向 MCP 提问功能对应的命令 / Ask MCP which command implements a feature

当你只知道想实现什么功能，但不知道 SpaceClaim 命令时，可以直接描述目标。  
When you know the desired outcome but not the SpaceClaim command, describe the goal directly.

例如 / Examples:

```text
SpaceClaim 中如何给几何体创建命名选择？
How do I create a named selection for geometry in SpaceClaim?
```

```text
SpaceClaim 中如何创建共享拓扑？请给出相关 API 和 Python 候选。
How do I create shared topology in SpaceClaim? Please provide the relevant API and a Python candidate.
```

```text
先创建一个 0° 的实体，再用圆周阵列每隔 60° 生成共 6 个实体，最后合并，应该使用哪些命令？
Create a 0-degree solid, make six instances at 60-degree intervals with a circular pattern, and merge them. Which commands should I use?
```

MCP 会优先检索官方 CHM 中的相关 API，例如：  
MCP first searches the relevant APIs in the official CHM, such as:

```text
Pattern.CreateCircular
CircularPatternData
Combine.Merge
```

然后返回命令含义、官方文档页面、参数信息和可进一步验证的 Python 候选。  
It then returns the command meaning, official documentation page, parameter information, and a Python candidate for further verification.

这种方式适合 / This pattern is useful for:

- 查询某个 SpaceClaim 功能对应的 API； / Finding the API for a SpaceClaim feature;
- 了解一个命令需要哪些输入； / Understanding a command’s required inputs;
- 快速比较多个可能的建模方法； / Comparing possible modeling approaches;
- 让 AI 帮你把多个 API 组织成建模步骤。 / Asking AI to organize several APIs into modeling steps.

### 方式二：提供完整建模输入，让 AI 按参数复现 / Provide complete modeling inputs for parameter-based reproduction

如果你已经有明确的建模任务，可以把输入参数、几何约束、参考图片和目标结果一起提供给 AI，让它按照这些信息生成复现脚本。  
For a defined modeling task, provide the AI with input parameters, geometric constraints, reference images, and target results so it can generate a reproduction script.

建议提供 / Recommended inputs:

- 几何尺寸和单位； / Geometry dimensions and units;
- 点、线、面、轴、坐标系等基准信息； / Datum information such as points, lines, faces, axes, and coordinate systems;
- 阵列数量、角度、间距和旋转方向； / Pattern count, angle, spacing, and rotation direction;
- 拉伸、倒角、圆角、布尔运算等操作顺序； / The order of extrusions, chamfers, fillets, Boolean operations, and other operations;
- 命名选择、共享拓扑或装配关系要求； / Requirements for named selections, shared topology, or assembly relationships;
- 输入图片、草图、截图或目标几何示意图； / Input images, sketches, screenshots, or target-geometry diagrams;
- 最终验收标准，例如实体数量、角度、尺寸和输出文件。 / Acceptance criteria such as body count, angles, dimensions, and output files.

这种方式适合 / This pattern is useful for:

- 根据图片或已有设计复现几何体； / Reproducing geometry from images or existing designs;
- 生成参数化建模脚本； / Generating parametric modeling scripts;
- 把复杂建模任务拆分成可验证的阶段； / Breaking complex modeling tasks into verifiable stages;
- 对已有 SpaceClaim 脚本进行补全、重构或排错。 / Completing, refactoring, or debugging existing SpaceClaim scripts.

## 这个 MCP 提供什么工具？ / Tools provided by this MCP

| 工具 / Tool | 用途 / Purpose |
| --- | --- |
| `search_api` | 搜索官方 CHM 中的类、方法、属性、事件和几何关键词 / Search classes, methods, properties, events, and geometry keywords in the official CHM |
| `get_doc` | 读取搜索结果对应的单个官方 API 页面 / Read the official API page for one search result |
| `find_python_command` | 根据自然语言建模意图返回 API 关联的 Python 候选 / Return Python candidates associated with a natural-language modeling intent |
| `get_geometry_recipe` | 将几何任务整理为有顺序的建模步骤 / Organize a geometry task into ordered modeling steps |

示例意图 / Example intent:

```text
圆周阵列 6 个，每隔 60 度，生成后合并
Circular pattern: 6 instances at 60-degree intervals, then merge
```

该意图会关联圆周阵列和合并操作，并返回：  
This intent associates circular-pattern and merge operations and returns:

```python
Pattern.CreateCircular(...)
Combine.Merge(...)
```

## 安装与首次配置 / Installation and first-run setup

要求：Windows、Node.js 18+，以及本机已经安装 ANSYS SpaceClaim/SCDM。  
Requirements: Windows, Node.js 18+, and ANSYS SpaceClaim/SCDM installed locally.

```powershell
git clone <your-github-repository-url>
cd ansys-scdmdoc-mcp
npm ci
npm run setup
node server.mjs
```

首次执行 `npm run setup` 会打开本地浏览器配置页。配置页提供两种选择：  
The first `npm run setup` opens a local browser configuration page with two choices:

1. 根据 `AWP_ROOT<number>` 自动检测； / Detect an installation through `AWP_ROOT<number>`;
2. 手动输入 `SpaceClaim.exe` 的应用程序路径。 / Enter the `SpaceClaim.exe` application path manually.

在自动检测模式下，程序会扫描当前环境变量中所有版本化的 AWP_ROOT，并通过下拉框让用户选择。点击确认后，程序会验证所选版本对应的官方 CHM。如果验证失败，会显示下面的固定错误，并只允许手动输入路径：  
In automatic-detection mode, the program scans all versioned AWP_ROOT environment variables and lets the user choose one from a drop-down list. After confirmation, it validates the official CHM for that version. If validation fails, it shows the fixed error below and allows only manual path entry:

```text
无法根据 AWP 确定环境变量的路径，无法确定 SpaceClaim 的路径
Unable to determine the environment-variable path from AWP; unable to determine the SpaceClaim path.
```

配置保存在 / Configuration is stored at:

```text
%LOCALAPPDATA%\ansys-scdmdoc-mcp\config.json
```

程序不会把用户电脑上的 ANSYS 安装路径写入代码或 README。手动模式保存的是应用程序路径，CHM 会从对应的 SCDM 安装目录自动查找。  
The program does not write a user’s ANSYS installation path into source code or the README. Manual mode stores the application path, and the CHM is discovered from the corresponding SCDM installation directory.

## 路径和文档来源 / Path resolution and documentation source

路径解析优先级如下 / Path resolution uses this priority order:

1. 显式 `SCDM_CHM`； / Explicit `SCDM_CHM`;
2. 首次配置保存的 AWP 或 SpaceClaim 路径； / The saved AWP or SpaceClaim path from first-run setup;
3. 所有可用 `AWP_ROOT<number>` 中版本号最高者； / The highest-version available `AWP_ROOT<number>`;
4. 通用 `AWP_ROOT` + `SCDM_API_VERSION`。 / Generic `AWP_ROOT` plus `SCDM_API_VERSION`.

首次读取 CHM 时，程序使用 Windows 的 `hh.exe` 将帮助文档解包到用户缓存目录，并建立搜索索引：  
On the first CHM read, the program uses Windows `hh.exe` to unpack the help content into the user cache and build a search index:

```text
%LOCALAPPDATA%\ansys-scdmdoc-mcp\cache
```

项目只查询用户本机的官方 CHM，不在仓库中分发或保存 ANSYS 专有文档。  
The project queries only the official CHM on the user’s computer; it does not distribute or store ANSYS proprietary documentation in the repository.

## 如何理解返回结果？ / How to interpret results

本项目明确区分三类证据 / This project distinguishes three evidence levels:

- `official-doc`：来自官方 SpaceClaim CHM 的页面、类型、方法或属性；  
  `official-doc`: a page, type, method, or property from the official SpaceClaim CHM;
- `inferred`：根据官方 API 签名和建模意图推导出的 Python 候选；  
  `inferred`: a Python candidate inferred from an official API signature and modeling intent;
- `runtime-verified`：已经在目标 SpaceClaim/SCDM 安装中实际执行，并检查过模型结果。  
  `runtime-verified`: executed in the target SpaceClaim/SCDM installation and checked against model results.

CHM 中常见的是 C#/.NET 签名。它可以证明 API 文档存在，但不能单独证明某段 IronPython 代码已经运行成功。因此，MCP 返回的 Python 代码仍应在目标 SCDM 版本中执行验证。  
The CHM commonly contains C#/.NET signatures. That proves the API documentation exists, but does not by itself prove that an IronPython snippet runs successfully. Python returned by MCP must therefore still be executed and verified in the target SCDM version.

## 真实几何验收案例 / Real geometry acceptance example

项目提供了一个真实 SpaceClaim 验收脚本：[spaceclaim-circular-repeat-smoke.py](scripts/spaceclaim-circular-repeat-smoke.py)。它验证的是完整的圆周阵列与合并流程，而不是简单循环 `Copy` 和 `Move.Rotate`：  
The project provides a real SpaceClaim acceptance script: [spaceclaim-circular-repeat-smoke.py](scripts/spaceclaim-circular-repeat-smoke.py). It validates the complete circular-pattern and merge workflow rather than a simple loop of `Copy` and `Move.Rotate`:

```text
创建 0° 基体 → Pattern.CreateCircular → CircularPatternData：6 个实例、360° 总角度
→ 检查 0/60/120/180/240/300° → Combine.Merge → 最终 1 个合并实体

Create a 0-degree seed → Pattern.CreateCircular → CircularPatternData: 6 instances, 360-degree total angle
→ Check 0/60/120/180/240/300 degrees → Combine.Merge → One final merged body
```

验收必须同时检查 / Acceptance must verify all of the following:

- 阵列实体数量为 6； / six patterned bodies;
- 角度间隔为 60°； / 60-degree spacing;
- 覆盖完整 360°； / full 360-degree coverage;
- `Combine.Merge` 成功； / successful `Combine.Merge`;
- 合并后的实体数量为 1； / one merged body;
- 模型文件和 `status=PASS` 结果文件都是本次运行新生成的。 / a fresh model file and a fresh result file with `status=PASS`.

MCP 查询成功、缓存建立成功或 SpaceClaim 进程返回码为 0，都不能单独替代真实几何验收。  
Successful MCP queries, a populated cache, or a SpaceClaim process exit code of 0 cannot replace real geometry acceptance on their own.

## 开发与测试 / Development and testing

```powershell
npm ci
npm test
npm run smoke
```

如果需要在本机验证真实 CHM / To verify the real CHM locally:

```powershell
$env:SCDM_RUN_REAL_CHM_TEST = "1"
npm test
```

真实 SpaceClaim 几何验收需要目标电脑安装有效的 ANSYS SpaceClaim/SCDM，并且输出文件应保存到仓库之外的临时目录。  
Real SpaceClaim geometry acceptance requires a valid ANSYS SpaceClaim/SCDM installation on the target computer. Save its output files in a temporary directory outside the repository.

## 发布和版权边界 / Release and copyright boundaries

发布到 GitHub 前，请确认以下内容没有被提交：  
Before publishing to GitHub, confirm that none of the following are committed:

- ANSYS 或 SpaceClaim CHM 文件； / ANSYS or SpaceClaim CHM files;
- CHM 解包后的 HTML、HHK、HHC 文件； / extracted CHM HTML, HHK, or HHC files;
- 用户本地缓存和索引； / local user caches and indexes;
- SpaceClaim 模型和运行日志； / SpaceClaim models and runtime logs;
- 用户配置、凭据和机器专属安装路径。 / user configuration, credentials, and machine-specific installation paths.

本项目只发布 MCP 实现、测试和说明文档，不发布 ANSYS 专有文档。ANSYS、SpaceClaim、SCDM 及相关产品名称属于各自权利人。本项目不代表 ANSYS 官方，也不声明与 ANSYS 存在授权或隶属关系。  
This project publishes only the MCP implementation, tests, and documentation; it does not publish ANSYS proprietary documentation. ANSYS, SpaceClaim, SCDM, and related product names belong to their respective owners. This project is not official ANSYS software and does not claim authorization, affiliation, or endorsement by ANSYS.

项目代码使用 MIT License，详见 [LICENSE](LICENSE)。  
The project code is released under the MIT License; see [LICENSE](LICENSE).
