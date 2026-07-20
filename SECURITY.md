# Security Policy

## Supported Versions

Only the latest released version of `stata-code` (PyPI) and
`stata-code-vscode` (VS Code Marketplace) receive security fixes.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security-sensitive reports.

Instead, email <brycewang@stanford.edu> with:

- a description of the issue and its impact,
- steps to reproduce (a minimal example if possible),
- the version(s) affected.

You should receive an acknowledgement within a few days. Once a fix is
available, the vulnerability will be disclosed in the release notes.

## Scope notes

`stata-code` executes user-supplied Stata code in a local Stata process by
design — arbitrary Stata code execution by the *local user* is the product, not
a vulnerability. Reports we care about include: paths where untrusted *remote*
input could reach the Stata session or the filesystem, privilege escalation,
and vulnerabilities in the MCP server's handling of tool arguments.
