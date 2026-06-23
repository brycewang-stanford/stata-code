from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_registers_top_level_cli():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'stata-code = "stata_code.cli:run_main"' in pyproject


def test_doctor_reports_ok_when_runtime_surface_is_available(monkeypatch):
    from stata_code import doctor

    monkeypatch.setattr(doctor, "_module_available", lambda _name: True)
    monkeypatch.setattr(doctor, "_first_existing_pystata_candidate", lambda: None)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        doctor,
        "pool_stata_info",
        lambda **_kwargs: {
            "version": "18.0",
            "edition": "mp",
            "backend": "pystata",
        },
    )

    report = doctor.run_doctor(include_user_configs=False)

    assert report.ok is True
    checks = {check.id: check for check in report.checks}
    assert checks["mcp_extra"].status == "ok"
    assert checks["kernel_extra"].status == "ok"
    assert checks["pystata_discovery"].status == "ok"
    assert checks["console_scripts"].status == "ok"
    assert checks["stata_probe"].summary.startswith("Stata initialized successfully")


def test_doctor_surfaces_degraded_environment_without_crashing(monkeypatch):
    from stata_code import doctor

    monkeypatch.setattr(doctor, "_module_available", lambda _name: False)
    monkeypatch.setattr(doctor, "_first_existing_pystata_candidate", lambda: None)
    monkeypatch.setattr(doctor, "_candidate_pystata_paths", lambda: ["/missing"])
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    def fail_probe(**_kwargs):
        raise RuntimeError("no Stata license")

    monkeypatch.setattr(doctor, "pool_stata_info", fail_probe)

    report = doctor.run_doctor(include_user_configs=False)
    checks = {check.id: check for check in report.checks}

    assert report.ok is False
    assert checks["mcp_extra"].status == "warn"
    assert checks["kernel_extra"].status == "warn"
    assert checks["pystata_discovery"].status == "warn"
    assert checks["console_scripts"].status == "warn"
    assert checks["client_config"].status == "ok"
    assert checks["stata_probe"].status == "fail"
    assert "no Stata license" in (checks["stata_probe"].detail or "")


def test_doctor_can_skip_live_stata_probe(monkeypatch):
    from stata_code import doctor

    monkeypatch.setattr(doctor, "_module_available", lambda name: name != "pystata")
    monkeypatch.setattr(
        doctor,
        "_first_existing_pystata_candidate",
        lambda: "/Applications/Stata/utilities",
    )
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/bin/{name}")

    def should_not_probe(**_kwargs):
        raise AssertionError("pool_stata_info should not be called")

    monkeypatch.setattr(doctor, "pool_stata_info", should_not_probe)

    report = doctor.run_doctor(probe_stata=False, include_user_configs=False)
    checks = {check.id: check for check in report.checks}

    assert report.ok is True
    assert checks["pystata_discovery"].status == "ok"
    assert checks["stata_probe"].status == "skip"


def test_doctor_json_renderer_is_stable(monkeypatch):
    from stata_code import doctor

    monkeypatch.setattr(doctor, "_module_available", lambda _name: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/bin/{name}")
    report = doctor.run_doctor(probe_stata=False, include_user_configs=False)

    payload = json.loads(doctor.format_json(report))

    assert payload["ok"] is True
    assert payload["counts"]["skip"] == 1
    assert [check["id"] for check in payload["checks"]][-1] == "stata_probe"


def test_doctor_reports_workspace_mcp_config(tmp_path, monkeypatch):
    from stata_code import doctor

    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stata-code": {
                        "command": "python",
                        "args": ["-m", "stata_code.mcp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor, "_module_available", lambda _name: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    report = doctor.run_doctor(
        probe_stata=False,
        workspace=tmp_path,
        include_user_configs=False,
    )
    checks = {check.id: check for check in report.checks}

    assert checks["client_config"].status == "ok"
    assert "mention stata-code" in checks["client_config"].summary
    assert ".mcp.json=mentions-stata-code" in (checks["client_config"].detail or "")


def test_doctor_warns_on_invalid_workspace_mcp_config(tmp_path, monkeypatch):
    from stata_code import doctor

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(doctor, "_module_available", lambda _name: True)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/bin/{name}")

    report = doctor.run_doctor(
        probe_stata=False,
        workspace=tmp_path,
        include_user_configs=False,
    )
    checks = {check.id: check for check in report.checks}

    assert report.ok is True
    assert checks["client_config"].status == "warn"
    assert "could not be read as JSON" in checks["client_config"].summary
    assert ".cursor/mcp.json=invalid-json" in (checks["client_config"].detail or "")


def test_cli_doctor_json_exit_code(monkeypatch, capsys):
    from stata_code import cli, doctor

    report = doctor.DoctorReport(
        ok=True,
        checks=[
            doctor.DiagnosticCheck(
                id="package",
                status="ok",
                summary="ok",
            )
        ],
    )
    monkeypatch.setattr(cli, "run_doctor", lambda **_kwargs: report)

    rc = cli.main(["doctor", "--json", "--no-stata-probe"])
    out = capsys.readouterr().out

    assert rc == 0
    assert json.loads(out)["ok"] is True


def test_cli_doctor_passes_workspace_and_config_scan_options(
    tmp_path,
    monkeypatch,
    capsys,
):
    from stata_code import cli, doctor

    captured: dict[str, object] = {}
    report = doctor.DoctorReport(
        ok=True,
        checks=[
            doctor.DiagnosticCheck(
                id="package",
                status="ok",
                summary="ok",
            )
        ],
    )

    def fake_run_doctor(**kwargs):
        captured.update(kwargs)
        return report

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    rc = cli.main(
        [
            "doctor",
            "--workspace",
            str(tmp_path),
            "--no-user-config-scan",
            "--no-stata-probe",
        ]
    )
    capsys.readouterr()

    assert rc == 0
    assert captured["workspace"] == str(tmp_path)
    assert captured["include_user_configs"] is False
    assert captured["probe_stata"] is False


def test_cli_verify_alias_returns_failure(monkeypatch, capsys):
    from stata_code import cli, doctor

    report = doctor.DoctorReport(
        ok=False,
        checks=[
            doctor.DiagnosticCheck(
                id="stata_probe",
                status="fail",
                summary="failed",
            )
        ],
    )
    monkeypatch.setattr(cli, "run_doctor", lambda **_kwargs: report)

    rc = cli.main(["verify", "--no-stata-probe"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "stata-code doctor" in out
    assert "[FAIL] stata_probe" in out
