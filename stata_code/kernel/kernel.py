"""Stata Jupyter kernel — exposes stata_code.run() via the Jupyter kernel protocol."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

try:
    from ipykernel.kernelbase import Kernel
    from ipykernel.displayhook import ZMQShellDisplayHook
    from IPython.display import display, Image, SVG, publish_display_data
    _HAS_IPYKERNEL = True
except ImportError:
    Kernel = object  # type: ignore[misc,assignment]
    _HAS_IPYKERNEL = False

from stata_code import run
from stata_code.core.result import StataResult, StataGraph


class StataKernel(Kernel if _HAS_IPYKERNEL else object):
    """
    Jupyter kernel for Stata backed by ``stata_code``.

    Supports Stata 17+ via pystata and falls back to ConsoleFallback for older versions.

    Install the kernel locally::

        python -m stata_code.kernel install --user

    Or system-wide (requires sudo)::

        sudo python -m stata_code.kernel install
    """

    protocol_version = "5.3"
    implementation = "stata_code.kernel"
    implementation_version = "0.1.0"
    language_info = {
        "name": "stata",
        "codemirror_mode": "stata",
        "file_extension": ".do",
        "mimetype": "text/x-stata",
        "pygments_lexer": "stata",
        "version": "17.0",
    }
    banner = "Stata kernel (stata_code) — backed by pystata / ConsoleFallback"

    # ---- Kernel personality ----
    help_links = [
        {"text": "Stata Help", "url": "https://www.stata.com/help.cgi?"},
    ]

    _last_result: StataResult | None = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter = None  # lazy; reuse via run()

    # ─────────────────────────────────────────────────────────────────
    # Execution
    # ─────────────────────────────────────────────────────────────────

    def do_execute(
        self,
        code: str,
        silent: bool = False,
        store_history: bool = True,
        user_expressions: dict[str, Any] | None = None,
        allow_stdin: bool = False,
    ) -> dict[str, Any]:
        """Execute Stata ``code`` and return execution results."""
        if not _HAS_IPYKERNEL:
            return self._error_reply("ipykernel not installed", silent=silent)

        try:
            result: StataResult = run(
                code.strip(),
                capture_graphs=True,
                capture_log=True,
                timeout=None,
            )
            self._last_result = result
        except Exception as exc:
            traceback.print_exc()
            return self._error_reply(str(exc), silent=silent)

        if not silent:
            # Text output
            if result.log:
                self._send_stream("stdout", result.log)

            # Structured results (r() / e())
            if result.results:
                self._publish_result_dict(result.results)

            # Graphs
            for graph in result.graphs:
                self._publish_graph(graph)

            # Errors
            if result.error:
                self._send_stream("stderr", f"!!! ERROR: {result.error}\n")
                if result.log:
                    self._send_stream("stderr", f"STATA LOG:\n{result.log}\n")
            elif result.warnings:
                for w in result.warnings:
                    self._send_stream("stdout", f"[warning] {w}\n")

        # Return execution reply
        reply: dict[str, Any] = {
            "status": "error" if result.error else "ok",
            "execution_count": self.execution_count,
        }
        if result.error:
            reply["ename"] = "StataError"
            reply["evalue"] = result.error
            reply["traceback"] = [result.error]

        return reply

    def _send_stream(self, name: str, text: str) -> None:
        """Send a stream (stdout / stderr) message."""
        if not text:
            return
        try:
            content = {"name": name, "text": text}
            self.send_response(self.iopub_socket, "stream", content)
        except Exception:
            pass  # suppress in non-kernel context

    def _publish_result_dict(self, results: dict[str, Any]) -> None:
        """Publish return values as a pretty-printed structured output."""
        lines = []
        for key, val in results.items():
            if isinstance(val, list) and len(val) > 10:
                val_str = f"[list of {len(val)} items]"
            elif isinstance(val, str) and len(val) > 200:
                val_str = val[:200] + " ..."
            else:
                val_str = str(val)
            lines.append(f"  {key:<30} = {val_str}")

        text = "\n".join(lines) if lines else "(no return values)"
        content = {
            "data": {"text/plain": text},
            "metadata": {},
        }
        try:
            self.send_response(self.iopub_socket, "display_data", content)
        except Exception:
            pass

    def _publish_graph(self, graph: StataGraph) -> None:
        """Publish a graph as image data."""
        import zmq

        mime_map = {
            "png": "image/png",
            "svg": "image/svg+xml",
            "pdf": "application/pdf",
        }
        mime = mime_map.get(graph.format, "image/png")
        data_b64 = graph.to_base64()
        content = {
            "data": {mime: data_b64, "text/plain": f"[graph: {graph.format}]"},
            "metadata": {"image/png": {"width": 900}},
        }
        msg = self.session.msg("display_data", content)
        self._send_message(msg)

    def _send_message(self, msg: list) -> None:
        """Send a Jupyter message via ZMQ."""
        try:
            import zmq

            self.session.send(self.iopub_socket, msg)
        except Exception:
            pass  # suppress in non-kernel context

    def _error_reply(self, msg: str, silent: bool = False) -> dict[str, Any]:
        return {
            "status": "error",
            "execution_count": self.execution_count,
            "ename": "RuntimeError",
            "evalue": msg,
            "traceback": [msg],
        }

    # ─────────────────────────────────────────────────────────────────
    # Autocompletion (optional but expected by Jupyter)
    # ─────────────────────────────────────────────────────────────────

    def do_complete(self, code: str, cursor_pos: int) -> dict[str, Any]:
        """Return completion candidates for Stata keywords."""
        # Stata's keyword list is small and stable; provide a static list
        STATA_KEYWORDS = [
            " quietly", " noisily", " capture", " verbose",
            " if", " in", " using", " replace", " append",
            " summarize", " summarize, detail", " describe", " browse",
            " list", " inspect", " count", " assert",
            " generate", " egen", " replace", " recode", " destring", " tostring",
            " merge", " append", " joinby", " cross",
            " sort", " gsort", " by", " bysort", " collapse", " contract", " stack",
            " reshape", " xpose", " fillin",
            " regress", " logistic", " probit", " tobit", " ivreg", " areg",
            " xtreg", " areg", " logit", " ologit", " oprobit", " mlogit",
            " svy: regress", " svy: logit", " svy: probit",
            " estimates", " eststo", " esttab", " estpost",
            " label", " label variable", " label define", " label values",
            " keep", " drop", " use", " save", " clear", " insheet", " infile",
            " infix", " import", " export", " outfile", " outreg",
            " graph", " graph bar", " graph box", " graph twoway", " graph export",
            " display", " putexcel", " putdocx",
            " tempfile", " tempvar", " global", " local",
            " foreach", " forvalues", " while", " if", " else", " continue",
            " set", " update", " restore", " preserve",
            " version", " mata", " python", " rshell",
        ]

        line = code[:cursor_pos]
        # Find the token being completed
        token_start = len(line) - 1
        while token_start > 0 and line[token_start - 1] not in (" \t\n\r(,"):
            token_start -= 1
        token = line[token_start:cursor_pos]

        matches = [kw for kw in STATA_KEYWORDS if kw.lstrip().startswith(token)]
        matches.sort()

        return {
            "status": "ok",
            "matches": matches,
            "cursor_start": token_start,
            "cursor_end": cursor_pos,
        }

    # ─────────────────────────────────────────────────────────────────
    # Inspection / introspection
    # ─────────────────────────────────────────────────────────────────

    def do_inspect(self, code: str, cursor_pos: int, detail_level: int = 0) -> dict[str, Any]:
        """Return tooltips / documentation for Stata commands."""
        STATA_HELP = {
            "summarize": "summarize [varlist] [if] [in] [weight] [, detail]\n\nCompute summary statistics.",
            "regress": "regress depvar [indepvars] [if] [in] [weight] [, options]\n\nLinear regression.",
            "logistic": "logistic depvar [indepvars] [if] [in] [weight] [, options]\n\nLogistic regression.",
            "generate": "generate newvar = exp\n\ngenerate creates a new variable.",
            "replace": "replace oldvar = exp [if] [in]\n\nreplace replaces the values of an existing variable.",
            "merge": "merge [n] 1:1 varlist using filename [, options]\n\nmerge joins data from disk.",
            "graph": "graph [type] plot [if] [in] [, options]\n\ngraph creates twoway plots.",
            "by": "by varlist: command\n\nby repeats command for each subset of data.",
        }
        # find the command at cursor
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

    # ─────────────────────────────────────────────────────────────────
    # Kernel info
    # ─────────────────────────────────────────────────────────────────

    def do_kernel_info(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "implementation": self.implementation,
            "implementation_version": self.implementation_version,
            "language_info": self.language_info,
            "banner": self.banner,
            "help_links": self.help_links,
        }


# ─────────────────────────────────────────────────────────────────
# Kernel installation CLI
# ─────────────────────────────────────────────────────────────────

def install_kernel(user: bool = True, system: bool = False) -> None:
    """
    Register this kernel with Jupyter.

    Usage::

        python -m stata_code.kernel install --user   # user install
        sudo python -m stata_code.kernel install    # system install
    """
    import shutil
    import sys
    from pathlib import Path

    # Find python executable
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

    kernel_dir = Path(__file__).parent / "stata_kernel"
    kernel_dir.mkdir(exist_ok=True)
    (kernel_dir / "kernel.json").write_text(json.dumps(kernel_json, indent=2))

    dest = kernel_dir
    if user:
        import os
        dest = Path(os.path.expanduser("~/.local/share/jupyter/kernels"))
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / "stata"
        shutil.copytree(kernel_dir, dest, dirs_exist_ok=True)

    print(f"Kernel installed to: {dest}")
    print(f"Restart Jupyter and select 'Stata' as the kernel.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Stata Jupyter kernel (stata_code)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    install_cmd = sub.add_parser("install", help="Install the Stata kernel")
    install_cmd.add_argument("--system", action="store_true", help="System-wide install")
    install_cmd.add_argument("--user", action="store_true", default=True, help="User install (default)")

    args = parser.parse_args()

    if args.cmd == "install":
        install_kernel(user=args.user, system=args.system)
    else:
        from ipykernel.kernelapp import IPKernelApp
        IPKernelApp.launch_instance(kernel_class=StataKernel)