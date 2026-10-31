# 输入表

人工接口，**只在初始化导入时读**。导入之后数据库是唯一真相源，改数据请改库（或用 `--file` 指向新表再导入一次），不要指望脚本每次去读这些文件。

| 下级 | 类型 | 作用 |
|---|---|---|
| `high_fidelity.csv` | 文件 | 高保真输入表（23 列，无 ID），导入到 `cfd_task` |
| `low_fidelity.csv` | 文件 | 低保真输入表（23 列，无 ID），导入到 `datcom_task` |
| `cases.csv` | 文件 | **旧体系**总表（17 列、带 `geometry_id=1`），留作历史证据，不迁入新库；仍是 `01_batch_datcom.py` 的默认 `--input` |
| `README.md` | 文件 | 本文件 |

## 23 列与含义

```text
L_N,L,D,L_W,C_RW,C_TW,B_W,L_F,C_RF,C_TF,B_F,          ← 11 个主尺寸（m）
fin_section_type,thickness_percent,flat_percent,le_radius,   ← hex 翼型
le_radius_index,max_thickness_tenths,                  ← naca 翼型（与上面二选一）
nose_shape,                                            ← cone / ogive
mach,alpha_deg,altitude_m,                             ← 3 个工况参数
origin,label                                           ← 来源与可读标签
```

- **不含 `geometry_id` / `condition_id`**：它们由 16 / 3 个参数算出来（`sha256` 前 12 位）。
  同一组参数永远得到同一个身份，所以表里重复填同一行不会有第二个身份。
- 翼型参数按 `fin_section_type` 二选一：`hex` 填 `flat_percent` / `le_radius`，
  `naca` 填 `le_radius_index` / `max_thickness_tenths`；另一组留空。
- `origin` ∈ `human` / `active_learning`，`label` 是可读标签（`C01`…），不参与身份。

## 导入

```powershell
python scripts/db.py import --table high --file data_lib/inputs/high_fidelity.csv --dry-run
python scripts/db.py import --table high
python scripts/db.py import --table low
```

- **追加型幂等**：已存在的几何/工况/任务行原样跳过，重复导入行数不变。
- **有一行不合格就整份不写**：宁可让人改表，也不要半份数据悄悄进库。
- 导入只会新增；删数据或改参数请显式操作数据库（改参数 = 新几何、新 `geometry_id`）。
