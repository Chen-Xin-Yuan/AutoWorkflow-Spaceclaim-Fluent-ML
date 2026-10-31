# AGENTS.md

## Project identity

`ansys-scdmdoc-mcp` is a Windows-local MCP server for searching the official ANSYS SpaceClaim/SCDM API Help CHM and producing API-grounded SCDM Python candidates.

The parent workspace describes missile geometry with 16 parameters: 11 length dimensions, a fin-section type (`hex` or `naca`), the three section parameters that type selects, and the nose shape (`cone` or `ogive`). This module's schema mirrors those 16 plus `k_nose` and `k_tip`, and it reads its 16 values from the database, never from a config file. Fin planforms stay trapezoidal. `fin_section` selects the cross-section: `hexagon` (the database default, matching the workspace's `hex`) is a symmetric hexagon, and `naca` is the symmetric NACA four-digit airfoil from section 4 (`NacaSection`) of `DATCOM/scripts/missile_spec.py`. The hexagon has exactly one section knob, `flat_percent`: the constant-thickness flat is centred on the chord and `lib/missile-params.mjs` derives `hex_flat_start = (1 - p/100)/2` and `hex_flat_end = (1 + p/100)/2` from it, which is the same quantity as DATCOM's `LFLATU` / `LMAXU`. Those two names are no longer schema fields and stay derived values in `params.json`. Each corner carries an absolute circular fillet (shoulders and the trailing edge `HEX_FILLET_RADIUS_M` = 0.001 m, leading edge `HEX_FILLET_LE_RADIUS_M` = 0.0018 m). Both hexagon fins stay radial and coplanar; the forward fin is not canted. The cant existed because a root face tangent to the fuselage was assumed to give an included angle `ConstantRound` would refuse, but V241 measurement shows the radial forward seam accepts the full 3 mm round (48 edges, 8 measured blend cylinders), so the cant only produced a scissor silhouette in axial views. A successful `npm run missile` directory keeps `params.json`, `model.py`, and the model file, plus the text records `run.json` and `result.json` (and `diagnostic.txt` when the script recorded an exception) so a finished run stays auditable; previews and `request.json` / `error.txt` are removed. The fillet keeps the chord extremes on the design leading and trailing edges. Each corner is one tangent-continuous circular arc drawn with `SketchArc.CreateSweepArc`, not a chord or a polyline. An arc the kernel would drop is sketched at `sketchScale` and scaled back about the origin, so the edge stays one circle of the true radius. Hexagonal fin tips keep their sharp design planform corners: the tip chord meets the leading and trailing edges at the design stations, with no planform arc and no corner cut, so the tip chord is the full design tip chord. The hexagon flat end lands at 0.823 of the local chord because `flat_percent` defaults to 64.6, and that value decides whether the forward fin-root seam can hold the 3 mm round. A symmetric flat cannot hold the end at 0.823 and the start at the old 0.20 at the same time, so the start sits at 0.177; DATCOM's hexagon is symmetric by construction, so both sides must share one `flat_percent` or the same `geometry_id` would mean two different sections. At 0.80 the round was clamped to its 1.5 mm floor and the un-rounded band left 24 non-round slivers (0.18–0.98 mm²) that cannot be deleted. That failure was reproduced on real hardware on 2026-10-31 with `flat_percent` 60.995 (end 0.805): `clamped=true` and `belowFloor=24` in `feedback/spaceclaim/g692bcb5d5e50__shared__20261031T104432Z/run.json`. The knee sits between 0.8220 and 0.8221, so 0.823 keeps one step of margin (+2.875% of the chord). `thickness_percent` is the shared airfoil maximum thickness, so changing it alters the cross-section and the aerodynamic result, and it did not clear the slivers — 4.50 / 4.52 / 4.54 / 4.55 at flat end 0.816 all still left 8 slivers. The design thickness is 4.462 and it is the same number in both places: the database row the pipeline dispatches from, and the schema default a manual run falls back to, so both build the same section. NACA still rounds the written card value to two decimals (4.46); the hexagon path keeps 4.462. Before acceptance the script repairs the fin-root damage the merge leaves behind: `FixInexactEdges` clears every inexact edge, and small faces are attempted one at a time with `FixSmallFaces.FixSpecific`. Every cylindrical face is excluded from that candidate list, because each junction round is 48 narrow strips and at the 1.5 mm floor one strip is ~0.094 mm² — deleting by area erases the fillet (measured 205 → 197 faces). Round counts must return unchanged, the pass is capped below the fin tip, and a final audit re-checks the body that is saved since later document edits can reintroduce inexact edges. Volume and area shifts are gated at 0.5% each. A `ConstantRound` is applied separately to the forward and aft fin-to-body junctions, over the whole seam loop of each station: every body edge whose midpoint lies on the fuselage cylinder (`HEX_ROOT_JUNCTION_SEAM_TOLERANCE_M` = 0.001 m), including the short shoulder-arc segments. The loop is rounded as one chain and is never cut down to the long edges; a partial chain has free ends at tangent transitions and SpaceClaim refuses one at every millimetre-scale radius, while the complete loop is accepted. The aft junction stays at `HEX_ROOT_JUNCTION_RADIUS_M` = 0.003 m. The canted forward junction tries that radius first, then steps down through `HEX_ROOT_JUNCTION_PROBE_RADII_M` and keeps the largest radius SpaceClaim accepts, which is 1.5 mm at the defaults, never below `HEX_ROOT_JUNCTION_FORWARD_MIN_RADIUS_M` = 0.0015 m. No smaller forward round is substituted and the aft junction is never reduced. The used radius is written into the kept `params.json`. NACA fins do not. NACA is scaled by the local chord; the hexagonal fillet is rebuilt at each station so the radius stays that absolute length. Both are ruled from the buried root to the tip. Leave the NACA fields in section 4 of `DATCOM/scripts/missile_spec.py`; this module copies the equation and the default values rather than importing that file.

## Non-negotiable boundaries

- Runtime target: Windows and Node.js 18+.
- MCP transport: stdio; stdout is reserved for JSON-RPC.
- Documentation source: an official CHM already installed on the user machine.
- Do not distribute, extract into the repository, or commit ANSYS proprietary documentation.
- Do not commit `node_modules`, user caches, generated models, runtime logs, or `%LOCALAPPDATA%` configuration.
- Do not write machine-specific ANSYS installation paths into source, tests, README examples, or fixtures.
- Resolve installations from `AWP_ROOT<number>`, generic `AWP_ROOT`, or an explicitly selected local configuration.
- Local Python knowledge-base files and environment variables are out of scope; default search is official CHM only.

## First-run configuration contract

The supported first-run entry point is `npm run setup`. It starts a loopback browser page with two modes:

1. AWP mode lists every non-empty `AWP_ROOT<number>` environment variable and lets the user choose one.
2. Manual mode accepts a `SpaceClaim.exe` path and discovers the API CHM beside that installation.

AWP confirmation must validate the selected official CHM. On failure, the UI must show exactly:

```text
无法根据 AWP 确定环境变量的路径，无法确定 SpaceClaim 的路径
```

and leave manual entry as the only available recovery path. Configuration is stored under `%LOCALAPPDATA%\ansys-scdmdoc-mcp\config.json`.

## Evidence rules

- Label CHM-derived facts as `official-doc`.
- Label Python code inferred from C#/.NET signatures as `inferred`.
- Use `runtime-verified` only after executing the target SpaceClaim installation and checking fresh model/result artifacts.
- Never treat process exit code, HTTP success, or a populated cache as proof that geometry is correct.

## Geometry acceptance contract

The acceptance fixture must use the actual circular-pattern workflow, not a manual copy-and-rotate loop:

```text
0-degree seed
→ Pattern.CreateCircular(CircularPatternData, count=6, angle=360 degrees)
→ verify 0/60/120/180/240/300 degrees
→ Combine.Merge
→ verify one merged body
```

The fixture must save a fresh SpaceClaim model and write a structured PASS result containing pattern body count, angles, coverage, merged body count, and a non-zero merged topology count.

## Development workflow

Before editing, inspect the relevant files and preserve unrelated user changes. Prefer small, testable changes and update tests and README together.

### Mandatory README synchronization

- Every code change must include a corresponding README update in the same change. This applies to features, fixes, refactors, scripts, parameters, defaults, configuration, dependencies, and validation behavior.
- Update the affected directory's `README.md` and the project-level README whenever its usage, capabilities, configuration, outputs, or limitations are affected. Only one README is maintained (Chinese): the former `README.en.md` and `README.bilingual.md` were removed on the user's request, so there is nothing to keep in sync across languages. Parameter values in the README are generated from code by `../scripts/docs.py` (`--check` / `--write`); do not hand-edit generated blocks.
- Parameters follow the code, never the other way round: a README value is superseded by the current code (front-of-file configuration blocks, schema defaults, and the rows in `../data_lib/datasets/SQLite/missile.db`). When code and prose disagree, change the prose. Only an explicit user request changes a parameter value. Precedence: database row > schema/config default > documentation. Generated parameter tables come from `../scripts/docs.py --write` and `--check` must pass before finishing; historical run records are evidence of the values at that time and are never rewritten to match today.
- For internal-only changes, update the relevant directory README with the current responsibility, data flow, or verification guidance; do not leave stale descriptions or add a date-only synchronization note.
- When adding, removing, renaming, or moving a directory, update its parent's README in the same change to list all direct child directories and their purposes. Document dependency, generated, and cache directories as not version-controlled and not to be edited by hand; do not edit their bundled READMEs.
- Before finishing, compare README commands, MCP tools, parameters, defaults, output paths, evidence claims, and directory listings against the code. A code change without its matching README update is incomplete.

Required checks for normal code changes:

```powershell
npm ci
npm test
npm run smoke
```

Real CHM and SpaceClaim checks are opt-in and require a local ANSYS installation. Keep their output outside the repository.

## Security and release

- Do not print or persist secrets, tokens, credentials, or private installation details.
- Review `git diff --cached` before staging.
- Confirm that CHM files, extracted Help content, caches, logs, models, and local config are absent before a GitHub release.
- Do not push, publish, create releases, or modify external GitHub state without explicit user confirmation in the current turn.
- PR descriptions must state test commands, environment assumptions, and whether real ANSYS verification was performed.
