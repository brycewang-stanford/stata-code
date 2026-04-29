"""Tests for the unified result schema."""

import pytest

from stata_code.core.result import StataResult, StataGraph


class TestStataGraph:
    def test_from_bytes(self):
        g = StataGraph(format="png", data=b"\x89PNG")
        assert g.to_base64() == "iVBORw=="

    def test_to_data_uri(self):
        g = StataGraph(format="png", data=b"\\x89PNG")
        assert g.to_data_uri().startswith("data:image/png;base64,")

    def test_save_roundtrip(self, tmp_path):
        path = tmp_path / "test.png"
        g = StataGraph(format="png", data=b"fake png data")
        g.save(str(path))
        assert g.path == str(path)
        assert path.read_bytes() == b"fake png data"


class TestStataResult:
    def test_success_true_when_no_error(self):
        r = StataResult()
        assert r.success is True

    def test_success_false_with_error(self):
        r = StataResult(error="something went wrong")
        assert r.success is False

    def test_success_false_with_nonzero_rc(self):
        r = StataResult(return_code=198)
        assert r.success is False

    def test_has_graphs(self):
        r = StataResult(graphs=[StataGraph(format="png", data=b"x")])
        assert r.has_graphs is True
        r_empty = StataResult()
        assert r_empty.has_graphs is False

    def test_add_warning_deduplicates(self):
        r = StataResult()
        r.add_warning("converged at boundary")
        r.add_warning("converged at boundary")
        assert len(r.warnings) == 1

    def test_summary_ok(self):
        r = StataResult(return_code=0, elapsed_seconds=1.5)
        r.graphs.append(StataGraph(format="png", data=b"x"))
        summary = r.summary()
        assert "OK" in summary
        assert "1.50s" in summary
        assert "1 graph" in summary

    def test_summary_err(self):
        r = StataResult(return_code=198, error="variable not found")
        summary = r.summary()
        assert "ERR" in summary
        assert "variable not found" in summary
