"""Entry point for the PyInstaller standalone `stata-code` binary.

The frozen binary bundles the Python runtime, `stata_code`, and its only hard
dependency (`pydantic`), so a user with **no Python installed** can run
`stata-code run`, `lint`, `doctor`, and `setup`. Paired with the **console
backend** (which shells out to the Stata CLI and needs no pystata), this gives a
genuinely zero-Python path to typed Stata results.

The pystata backend still needs Stata's own `pystata` on the interpreter path,
which a frozen binary cannot provide — so on a machine without a Python+pystata
setup, use `--backend console`.
"""

from __future__ import annotations

from stata_code.cli import run_main

if __name__ == "__main__":
    run_main()
