# Security Policy

## Reporting a vulnerability

Do not include credentials, ANSYS CHM files, extracted documentation, or private logs in a public issue. Report security problems privately through the repository owner's configured GitHub security channel.

## Security boundaries

- The server reads a local CHM selected through environment variables.
- `get_doc` only reads files below the extracted documentation root.
- Generated cache data is kept outside the repository.
- The server does not accept or execute arbitrary Python code.
- Users remain responsible for reviewing inferred commands before running them in SCDM.
