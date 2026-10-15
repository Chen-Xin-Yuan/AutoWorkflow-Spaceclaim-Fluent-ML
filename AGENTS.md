# Workspace rules

- Preserve `missile_project`: do not read, enumerate, edit, move or delete its contents.
- `spaceclaim` and `fluent_meshing` are independent repositories. Read each repository's own instructions before editing it.
- Keep folder structure and `README.md` files in sync. Any change that adds, removes, renames or moves a directory must update the corresponding `README.md` in the same change, so that it lists that directory's direct children and their purpose. The workspace root covers the top level and one level below; every other directory documents its own direct children.
- A structure change without its matching README update is incomplete work. A README entry that no longer matches the tree is a defect, not documentation debt. Dependency, generated and cache directories are documented too, marked as not version-controlled and not to be edited by hand.
- Use `workspace.json` and script-relative paths. Discover ANSYS through AWP variables or its supported local setup configuration; keep installation paths and credentials out of source and reports.
- Keep runtime models, meshes and logs outside code repositories. Write per-run records under `data_lib/runs`; retain failed artifacts under `feedback`.
- Existing historical records are evidence snapshots, not fresh acceptance results.
- Preserve dependency versions and Git/untracked content during migration. `_deps` and `.mesh_inspect_deps` use different Python/NumPy environments and must not be combined.
- Validate resolved absolute paths before filesystem moves or removals. Use a complete verified snapshot for rollback, preserving newer files rather than overwriting them.
- `fluent_solution` and `ML_training` are reserved modules. `experiments/autoCFD` is retained research code and has not been validated as this workspace's solver.
- Keep SpaceClaim MCP stdout exclusively JSON-RPC. External MCP registration is outside this migration.
