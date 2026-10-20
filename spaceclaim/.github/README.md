# GitHub 自动检查

本目录维护 GitHub Actions 配置。当前工作流只安装依赖并运行 Node.js 测试，不执行本机 ANSYS、真实 CHM 检查或 SpaceClaim 几何验收。

| 直接子目录 | 用途 |
| --- | --- |
| [`workflows/`](workflows/README.md) | Windows 与 Node.js 版本矩阵的测试工作流。 |

修改工作流时同步更新本 README 和 [`workflows/README.md`](workflows/README.md)，使触发条件、运行环境和检查范围与 YAML 一致。项目使用方式见[主 README](../README.md)。
