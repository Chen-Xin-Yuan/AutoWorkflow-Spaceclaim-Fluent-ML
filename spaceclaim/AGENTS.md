# AGENTS.md

## Project identity

`ansys-scdmdoc-mcp` is a Windows-local MCP server for searching the official ANSYS SpaceClaim/SCDM API Help CHM and producing API-grounded SCDM Python candidates.

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
