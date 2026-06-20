"""Tests for the turnkey method-level MCP prompts.

These prompts (``did_event_study``, ``iv_2sls``, ``rdd``, ``publication_table``)
scaffold complete econometric workflows that route into the
``skills/stata-code/references/recipes/`` library. They are additive to the
existing agent/notebook workflow prompts and require no Stata to render.

Kept in a separate module from ``test_mcp.py`` so the method-recipe surface can
evolve independently of the core MCP prompt tests.
"""

from __future__ import annotations

import pytest

METHOD_PROMPTS = ("did_event_study", "iv_2sls", "rdd", "publication_table")


def _defs():
    from stata_code.mcp.server import _prompt_definitions

    return {p.name: p for p in _prompt_definitions()}


def test_method_prompts_registered():
    prompts = _defs()
    for name in METHOD_PROMPTS:
        assert name in prompts, f"missing method prompt: {name}"


def test_did_event_study_required_arguments():
    prompts = _defs()
    args = {a.name: a for a in prompts["did_event_study"].arguments}
    assert args["data_path"].required is True
    assert args["outcome"].required is True
    assert args["cohort"].required is True
    # Controls and session are optional conveniences.
    assert args["controls"].required is False
    assert args["session_id"].required is False


def test_iv_2sls_required_arguments():
    prompts = _defs()
    args = {a.name: a for a in prompts["iv_2sls"].arguments}
    for required in ("data_path", "outcome", "endogenous", "instruments"):
        assert args[required].required is True


def test_rdd_required_arguments():
    prompts = _defs()
    args = {a.name: a for a in prompts["rdd"].arguments}
    assert args["data_path"].required is True
    assert args["outcome"].required is True
    assert args["running_var"].required is True
    # Cutoff defaults to 0; fuzzy take-up is optional.
    assert args["cutoff"].required is False
    assert args["fuzzy"].required is False


def test_publication_table_required_arguments():
    prompts = _defs()
    args = {a.name: a for a in prompts["publication_table"].arguments}
    assert args["models"].required is True
    assert args["output_path"].required is False


def test_did_event_study_renders_workflow():
    from stata_code.mcp.server import _get_mcp_prompt

    rendered = _get_mcp_prompt(
        "did_event_study",
        {
            "data_path": "data/cfps_panel.dta",
            "outcome": "wage",
            "cohort": "first_treat",
            "controls": "age age2 edu industry",
            "session_id": "did",
        },
    )
    body = rendered.messages[0].content.text
    # Threads the user's arguments through, and names the load-bearing steps.
    assert "data/cfps_panel.dta" in body
    assert "wage" in body
    assert "first_treat" in body
    assert "age age2 edu industry" in body
    assert "bacondecomp" in body
    assert "csdid" in body
    assert "esttab" in body
    # Points the agent at the recipe rather than re-deriving the pipeline.
    assert "recipes/did-event-study.md" in body


def test_iv_2sls_renders_first_stage_and_late():
    from stata_code.mcp.server import _get_mcp_prompt

    body = _get_mcp_prompt(
        "iv_2sls",
        {
            "data_path": "data/wage.dta",
            "outcome": "lnwage",
            "endogenous": "educ",
            "instruments": "quarter_of_birth",
        },
    ).messages[0].content.text
    assert "first stage" in body.lower()
    assert "LATE" in body
    assert "recipes/iv-2sls.md" in body


def test_rdd_renders_sharp_default_and_density_test():
    from stata_code.mcp.server import _get_mcp_prompt

    body = _get_mcp_prompt(
        "rdd",
        {"data_path": "data/rd.dta", "outcome": "y", "running_var": "score"},
    ).messages[0].content.text
    assert "rdrobust" in body
    assert "rddensity" in body
    assert "sharp RD" in body  # default when no fuzzy take-up var is given
    assert "recipes/rdd.md" in body


def test_rdd_fuzzy_branch_renders_takeup():
    from stata_code.mcp.server import _get_mcp_prompt

    body = _get_mcp_prompt(
        "rdd",
        {
            "data_path": "data/rd.dta",
            "outcome": "y",
            "running_var": "score",
            "cutoff": "50",
            "fuzzy": "enrolled",
        },
    ).messages[0].content.text
    assert "fuzzy" in body.lower()
    assert "enrolled" in body
    assert "50" in body


def test_publication_table_preview_vs_file():
    from stata_code.mcp.server import _get_mcp_prompt

    preview = _get_mcp_prompt(
        "publication_table", {"models": "m1 m2 m3"}
    ).messages[0].content.text
    assert "m1 m2 m3" in preview
    assert "esttab" in preview
    assert "recipes/publication-tables.md" in preview

    to_file = _get_mcp_prompt(
        "publication_table",
        {"models": "m1 m2", "output_path": "out/table.tex"},
    ).messages[0].content.text
    assert "out/table.tex" in to_file


@pytest.mark.parametrize("name", METHOD_PROMPTS)
def test_method_prompts_render_without_optional_args(name):
    """Each prompt renders from only its required arguments (placeholders fill in)."""
    from stata_code.mcp.server import _get_mcp_prompt

    prompts = _defs()
    required = {a.name: "x" for a in prompts[name].arguments if a.required}
    rendered = _get_mcp_prompt(name, required)
    assert rendered.messages[0].content.text
    assert rendered.description
