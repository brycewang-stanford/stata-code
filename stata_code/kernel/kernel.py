"""Stata Jupyter kernel — exposes the v1.0 stata_code pipeline.

The kernel uses `runner.execute()` for every cell. Defaults are tuned for
human/notebook use rather than agent use:
- `include_full_log=True`: full log shown in stdout (no head/tail truncation)
- `include_graphs="inline"`: graph bytes embedded for direct rendering
- `session_id="main"`: single-session unless the kernel is configured with
  multiple kernel specs

Install via `python -m stata_code.kernel install --user`.
"""

from __future__ import annotations

import base64
import json
import sys
import traceback
from pathlib import Path
from typing import Any

try:
    from ipykernel.kernelbase import Kernel

    _HAS_IPYKERNEL = True
except ImportError:
    Kernel = object  # type: ignore[misc,assignment]
    _HAS_IPYKERNEL = False

from stata_code.core._runtime import PystataNotAvailable
from stata_code.core.runner import execute
from stata_code.core.schema import RunResult


# ─────────────────────────────────────────────────────────────────────────────
# Static keyword / help tables (carried over verbatim — independent of
# pipeline; used by do_complete / do_inspect)
# ─────────────────────────────────────────────────────────────────────────────


STATA_KEYWORDS: tuple[str, ...] = (
    " quietly", " noisily", " capture",
    " summarize", " summarize, detail", " describe", " browse",
    " list", " inspect", " count", " assert",
    " generate", " egen", " replace", " recode", " destring", " tostring",
    " merge", " append", " joinby", " cross",
    " sort", " gsort", " by", " bysort", " collapse", " contract", " stack",
    " reshape", " xpose", " fillin",
    " regress", " logistic", " probit", " tobit", " ivreg", " areg",
    " xtreg", " logit", " ologit", " oprobit", " mlogit",
    " estimates", " eststo", " esttab", " estpost",
    " label", " label variable", " label define", " label values",
    " keep", " drop", " use", " save", " clear", " insheet", " infile",
    " infix", " import", " export", " outfile", " outreg",
    " graph", " graph bar", " graph box", " graph twoway", " graph export",
    " display", " putexcel", " putdocx",
    " tempfile", " tempvar", " global", " local",
    " foreach", " forvalues", " while", " if", " else", " continue",
    " set", " update", " restore", " preserve",
    " version", " mata", " python",
)


STATA_HELP: dict[str, str] = {
    "summarize": "summarize [varlist] [if] [in] [weight] [, detail]\n\nCompute summary statistics.",
    "regress": "regress depvar [indepvars] [if] [in] [weight] [, options]\n\nLinear regression.",
    "logistic": "logistic depvar [indepvars] [if] [in] [weight] [, options]\n\nLogistic regression.",
    "generate": "generate newvar = exp\n\ngenerate creates a new variable.",
    "replace": "replace oldvar = exp [if] [in]\n\nreplace replaces the values of an existing variable.",
    "merge": "merge [n] 1:1 varlist using filename [, options]\n\nmerge joins data from disk.",
    "graph": "graph [type] plot [if] [in] [, options]\n\ngraph creates twoway plots.",
    "by": "by varlist: command\n\nby repeats command for each subset of data.",
}


# ─────────────────────────────────────────────────────────────────────────────
# Kernel
# ─────────────────────────────────────────────────────────────────────────────


class StataKernel(Kernel if _HAS_IPYKERNEL else object):
    protocol_version = "5.3"
    implementation = "stata_code.kernel"
    implementation_version = "0.2.0"
    language_info: dict[str, Any] = {
        "name": "stata",
        "codemirror_mode": "stata",
        "file_extension": ".do",
        "mimetype": "text/x-stata",
        "pygments_lexer": "stata",
        "version": "18.0",
    }
    banner = "Stata kernel (stata_code) — backed by the v1.0 runner pipeline"
    help_links = [{"text": "Stata Help", "url": "https://www.stata.com/help.cgi?"}]

    _last_result: RunResult | None = None

    # ── Execution ──────────────────────────────────────────────────────────

    def do_execute(
        self,
        code: str,
        silent: bool = False,
        store_history: bool = True,
        user_expressions: dict[str, Any] | None = None,
        allow_stdin: bool = False,
    ) -> dict[str, Any]:
        if not _HAS_IPYKERNEL:
            return self._error_reply("ipykernel not installed")

        try:
            result = execute(
                code.strip(),
                include_full_log=True,
                include_graphs="inline",
            )
        except PystataNotAvailable as exc:
            return self._error_reply(f"Stata not available: {exc}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._error_reply(str(exc))

        self._last_result = result

        if not silent:
            if result.log.head:
                self._stream("stdout", result.log.head + "\n")
            if result.warnings:
                for w in result.warnings:
                    self._stream("stderr", f"[{w.kind}] {w.message}\n")
            for graph in result.graphs:
                if graph.inline:
                    self._publish_image(graph.inline, graph.format.value)
            if result.error:
                msg = self._format_error(result)
                self._stream("stderr", msg + "\n")

        return self._reply(result)

    # ── Reply helpers ──────────────────────────────────────────────────────

    def _reply(self, r: RunResult) -> dict[str, Any]:
        if r.error is None:
            return {
                "status": "ok",
                "execution_count": self.execution_count,
                "payload": [],
                "user_expressions": {},
            }
        return {
            "status": "error",
            "execution_count": self.execution_count,
            "ename": f"StataError({r.error.kind.value})",
            "evalue": r.error.message,
            "traceback": [self._format_error(r)],
        }

    def _format_error(self, r: RunResult) -> str:
        e = r.error
        assert e is not None
        parts = [f"!!! Stata error: {e.kind.value} (rc={e.rc})", f"    {e.message}"]
        if e.line is not None:
            parts.append(f"    at line {e.line}: {e.context.failing!r}")
        for s in e.suggestions:
            parts.append(f"    → {s.action}")
        return "\n".join(parts)

    def _error_reply(self, msg: str) -> dict[str, Any]:
        return {
            "status": "error",
            "execution_count": self.execution_count,
            "ename": "RuntimeError",
            "evalue": msg,
            "traceback": [msg],
        }

    def _stream(self, name: str, text: str) -> None:
        if not text:
            return
        try:
            self.send_response(
                self.iopub_socket, "stream", {"name": name, "text": text}
            )
        except Exception:  # noqa: BLE001
            pass  # non-kernel context (tests)

    def _publish_image(self, b64_data: str, fmt: str) -> None:
        mime = {
            "png": "image/png",
            "svg": "image/svg+xml",
            "pdf": "application/pdf",
        }.get(fmt, "image/png")
        try:
            self.send_response(
                self.iopub_socket,
                "display_data",
                {
                    "data": {mime: b64_data, "text/plain": f"[graph: {fmt}]"},
                    "metadata": {},
                },
            )
        except Exception:  # noqa: BLE001
            pass

    # ── Completion / Inspection (unchanged from prior kernel) ──────────────

    def do_complete(self, code: str, cursor_pos: int) -> dict[str, Any]:
        line = code[:cursor_pos]
        token_start = len(line) - 1
        while token_start > 0 and line[token_start - 1] not in (" \t\n\r(,"):
            token_start -= 1
        token = line[token_start:cursor_pos]
        matches = sorted(kw for kw in STATA_KEYWORDS if kw.lstrip().startswith(token))
        return {
            "status": "ok",
            "matches": matches,
            "cursor_start": token_start,
            "cursor_end": cursor_pos,
        }

    def do_inspect(
        self, code: str, cursor_pos: int, detail_level: int = 0
    ) -> dict[str, Any]:
        word_end = cursor_pos
        word_start = word_end - 1
        while word_start > 0 and code[word_start - 1].isalnum():
            word_start -= 1
        word = code[word_start:word_end]
        found = STATA_HELP.get(word.lower())
        if found:
            return {
                "status": "ok",
                "found": True,
                "name": word,
                "documentation": found,
                "cursor_start": word_start,
                "cursor_end": word_end,
            }
        return {"status": "ok", "found": False}

    def do_kernel_info(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "implementation": self.implementation,
            "implementation_version": self.implementation_version,
            "language_info": self.language_info,
            "banner": self.banner,
            "help_links": self.help_links,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Kernel installation CLI
# ─────────────────────────────────────────────────────────────────────────────


def install_kernel(user: bool = True, system: bool = False) -> None:
    """Register the Stata kernel with Jupyter."""
    import os
    import shutil

    py_exec = Path(sys.executable).resolve()
    kernel_json = {
        "argv": [
            str(py_exec),
            "-m",
            "stata_code.kernel",
            "-f",
            "{connection_file}",
        ],
        "display_name": "Stata",
        "language": "stata",
        "metadata": {"debugger": False},
    }

    src_dir = Path(__file__).parent / "stata_kernel"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "kernel.json").write_text(json.dumps(kernel_json, indent=2))

    dest = src_dir
    if user:
        dest = Path(os.path.expanduser("~/.local/share/jupyter/kernels"))
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / "stata"
        shutil.copytree(src_dir, dest, dirs_exist_ok=True)

    print(f"Kernel installed to: {dest}")
    print("Restart Jupyter and select 'Stata' as the kernel.")


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Stata Jupyter kernel (stata_code)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    install_cmd = sub.add_parser("install", help="Install the Stata kernel")
    install_cmd.add_argument("--system", action="store_true")
    install_cmd.add_argument("--user", action="store_true", default=True)

    args = parser.parse_args()
    if args.cmd == "install":
        install_kernel(user=args.user, system=args.system)
    else:
        from ipykernel.kernelapp import IPKernelApp

        IPKernelApp.launch_instance(kernel_class=StataKernel)
