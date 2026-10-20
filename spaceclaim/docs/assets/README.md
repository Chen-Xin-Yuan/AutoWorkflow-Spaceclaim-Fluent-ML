# 文档视觉资源

本目录没有直接子目录。这里的文件用于说明使用流程，不是运行生成的模型、截图验收结果或官方 API 文档。

| 文件 | 用途 |
| --- | --- |
| [`setup-flow.svg`](setup-flow.svg) | 首次配置流程的 SVG 示意资源。 |
| [`setup-flow.en.png`](setup-flow.en.png) | 英文配置流程示意图。 |
| [`circular-pattern-hero.png`](circular-pattern-hero.png) | 圆周阵列工作流的说明插图。 |

配置行为由 [`scripts/setup.mjs`](../../scripts/setup.mjs) 和 [`lib/setup.mjs`](../../lib/setup.mjs) 实现；圆周阵列的真机检查由 [`scripts/verify-geometry.mjs`](../../scripts/verify-geometry.mjs) 驱动。修改对应流程时，应检查引用这些图片的 README 及图片内容是否需要同步更新。
