"""Regression tests for bugs found in the 2026-07 correctness review.

Each test pins the FIXED behavior; see the commit message for the failure
scenario each bug produced.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from stata_code.core import runner
from stata_code.core._runtime import _extract_rc
from stata_code.core.log_artifacts import snapshot_working_dir_files


class TestExtractRc:
    """rc must come from the LAST r(NNN); in the transcript, not the first."""

    def test_takes_trailing_rc_not_echoed_literal(self):
        text = 'display "see r(198); for details"\nvariable mpgg not found\nr(111);'
        assert _extract_rc(text) == 111

    def test_single_rc(self):
        assert _extract_rc("variable x not found\nr(111);") == 111

    def test_no_rc_returns_minus_one(self):
        assert _extract_rc("something exploded with no return code") == -1

    def test_negative_synthetic_rc(self):
        assert _extract_rc("worker gave up\nr(-2);") == -2


class TestGetGraphFormat:
    """get_graph(format=...) must not silently return mismatched bytes."""

    def _put_graph(self, ref: str) -> None:
        from stata_code.core import _refs

        _refs.put(
            ref,
            {"format": "png", "bytes": b"\x89PNG fake", "width": 10, "height": 10},
        )

    def test_matching_format_returns_payload(self):
        ref = "graph://test-format-match/g1"
        self._put_graph(ref)
        payload = runner.get_graph(ref, "png")
        assert payload["format"] == "png"

    def test_none_format_returns_stored(self):
        ref = "graph://test-format-none/g1"
        self._put_graph(ref)
        assert runner.get_graph(ref)["format"] == "png"

    def test_mismatched_format_raises_value_error(self):
        ref = "graph://test-format-mismatch/g1"
        self._put_graph(ref)
        with pytest.raises(ValueError, match="stored as 'png'"):
            runner.get_graph(ref, "svg")


class TestSessionDefaultAliasing:
    """A session literally named "default" must not alias "main"'s frame."""

    def test_default_maps_to_private_frame(self):
        frame = runner._frame_for_session("default")
        assert frame != "default"
        assert frame.startswith("_sc_")

    def test_default_round_trips(self):
        frame = runner._frame_for_session("default")
        assert runner._session_for_frame(frame) == "default"

    def test_main_still_owns_the_default_frame(self):
        assert runner._frame_for_session("main") == "default"
        assert runner._session_for_frame("default") == "main"


class TestSnapshotEscapingSymlink:
    """A symlink pointing outside the working dir must be skipped, not crash."""

    def test_symlink_out_of_root_is_skipped(self, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "target.csv"
        target.write_text("a,b\n1,2\n")

        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "normal.csv").write_text("x\n")
        os.symlink(target, workdir / "escaping-link.csv")

        snapshot = snapshot_working_dir_files(str(workdir))
        assert any(p.endswith("normal.csv") for p in snapshot)
        assert not any("target.csv" in p for p in snapshot)
