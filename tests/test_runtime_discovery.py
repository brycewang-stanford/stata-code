from __future__ import annotations


def test_normalize_direct_utilities_path(tmp_path):
    from stata_code.core._runtime import _normalize_pystata_candidate

    utilities = tmp_path / "Stata19" / "utilities"
    assert str(utilities) in _normalize_pystata_candidate(str(utilities))


def test_normalize_pystata_package_path(tmp_path):
    from stata_code.core._runtime import _normalize_pystata_candidate

    package = tmp_path / "Stata19" / "utilities" / "pystata"
    assert str(package.parent) in _normalize_pystata_candidate(str(package))


def test_normalize_stata_app_path(tmp_path):
    from stata_code.core._runtime import _normalize_pystata_candidate

    app = tmp_path / "StataMP.app"
    candidates = _normalize_pystata_candidate(str(app))
    assert str(app.parent / "utilities") in candidates


def test_candidate_paths_honor_env(monkeypatch, tmp_path):
    from stata_code.core import _runtime

    utilities = tmp_path / "Stata19" / "utilities"
    monkeypatch.setenv("STATA_CODE_PYSTATA_PATH", str(utilities))
    monkeypatch.setenv("STATA_HOME", str(tmp_path / "Stata19"))

    candidates = _runtime._candidate_pystata_paths()
    assert str(utilities) in candidates
    assert candidates.index(str(utilities)) == 0


def test_find_pystata_path_uses_env_candidate(monkeypatch, tmp_path):
    import builtins

    from stata_code.core._runtime import PystataRuntime

    utilities = tmp_path / "Stata19" / "utilities"
    (utilities / "pystata").mkdir(parents=True)
    monkeypatch.setenv("STATA_CODE_PYSTATA_PATH", str(utilities))

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pystata" or name.startswith("pystata."):
            raise ImportError("hidden for discovery test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert PystataRuntime._find_pystata_path() == str(utilities)
