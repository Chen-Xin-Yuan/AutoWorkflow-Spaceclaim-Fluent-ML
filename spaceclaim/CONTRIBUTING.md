# Contributing

Contributions are welcome for the MCP implementation, tests, documentation, and platform diagnostics.

Before opening a pull request:

1. Run `npm ci`.
2. Run `npm test`.
3. Run `npm run smoke` when a valid ANSYS environment is available.
4. Confirm that no CHM, extracted Help files, local cache, private path, credential, or proprietary ANSYS content is included.
5. Describe the Node.js version, Windows version, and whether a local ANSYS installation was required.

Keep Python output evidence-labelled. The official CHM can support API documentation, but an `inferred` Python candidate is not runtime proof.

Please keep pull requests focused. Changes to MCP tool names or input schemas should include a compatibility note in `CHANGELOG.md`.
