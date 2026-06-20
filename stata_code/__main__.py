"""Allow `python -m stata_code` to use the top-level CLI."""

from stata_code.cli import run_main

if __name__ == "__main__":  # pragma: no cover
    run_main()
