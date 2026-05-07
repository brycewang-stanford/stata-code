"""Allow `python -m stata_code.kernel` to launch or install the kernel."""

from stata_code.kernel.kernel import run_main

if __name__ == "__main__":  # pragma: no cover
    run_main()
