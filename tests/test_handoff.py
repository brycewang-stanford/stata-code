"""Unit tests for the data-MCP handoff verifier - no Stata needed."""

from __future__ import annotations

from stata_code.core.handoff import DatasetCheck, verify_dataset
from stata_code.core.schema import DatasetInfo, VariableInfo


def _ds(n_obs: int = 100, n_vars: int = 3, with_vars: bool = True) -> DatasetInfo:
    variables = (
        [
            VariableInfo(name="year", type="int"),
            VariableInfo(name="gdp", type="double"),
            VariableInfo(name="cpi", type="double"),
        ]
        if with_vars
        else None
    )
    return DatasetInfo(n_obs=n_obs, n_vars=n_vars, variables=variables)


class TestVerifyDataset:
    def test_all_pass(self):
        check = verify_dataset(
            _ds(),
            n_obs=100,
            n_vars=3,
            required_vars=["year", "gdp"],
        )
        assert isinstance(check, DatasetCheck)
        assert check.ok is True
        assert check.issues == []
        assert set(check.checks) == {"n_obs", "n_vars", "required_vars"}

    def test_no_constraints_is_ok_and_empty(self):
        check = verify_dataset(_ds())
        assert check.ok is True
        assert check.checks == []
        assert check.issues == []

    def test_n_obs_mismatch(self):
        check = verify_dataset(_ds(n_obs=99), n_obs=100)
        assert check.ok is False
        assert any("100" in i and "99" in i for i in check.issues)

    def test_min_obs_floor(self):
        assert verify_dataset(_ds(n_obs=50), min_obs=100).ok is False
        assert verify_dataset(_ds(n_obs=150), min_obs=100).ok is True

    def test_max_obs_ceiling(self):
        assert verify_dataset(_ds(n_obs=150), max_obs=100).ok is False
        assert verify_dataset(_ds(n_obs=50), max_obs=100).ok is True

    def test_n_vars_mismatch(self):
        check = verify_dataset(_ds(n_vars=3), n_vars=5)
        assert check.ok is False
        assert any("variable" in i for i in check.issues)

    def test_missing_required_vars_listed(self):
        check = verify_dataset(_ds(), required_vars=["year", "unemployment"])
        assert check.ok is False
        assert any("unemployment" in i for i in check.issues)
        assert all("year" not in i for i in check.issues)  # present, not flagged

    def test_required_vars_without_variable_list_is_flagged(self):
        check = verify_dataset(_ds(with_vars=False), required_vars=["year"])
        assert check.ok is False
        assert any("include_dataset_variables" in i for i in check.issues)
        assert "required_vars" in check.checks

    def test_multiple_failures_accumulate(self):
        check = verify_dataset(
            _ds(n_obs=10, n_vars=2),
            n_obs=100,
            n_vars=3,
            required_vars=["missing_a"],
        )
        assert check.ok is False
        assert len(check.issues) == 3

    def test_public_exports_include_handoff_verifier(self):
        import stata_code

        assert stata_code.DatasetCheck is DatasetCheck
        assert stata_code.verify_dataset is verify_dataset
