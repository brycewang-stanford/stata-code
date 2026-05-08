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


def test_normalize_executable_inside_app_finds_install_root(tmp_path):
    """User passes the Stata executable inside a .app — discovery should reach
    the install root (.app's parent) and not keep climbing into OS noise.

    Also verifies that we don't waste stat() calls on guaranteed-junk paths
    inside the .app bundle (Contents/, MacOS/) or on the .app itself —
    Stata never ships ``utilities/`` at any of those locations.
    """
    from stata_code.core._runtime import _normalize_pystata_candidate

    install_root = tmp_path / "Stata"
    app = install_root / "StataMP.app"
    exe = app / "Contents" / "MacOS" / "StataMP"
    candidates = _normalize_pystata_candidate(str(exe))

    assert str(install_root / "utilities") in candidates
    # OS-noise (one level above the install root) must not appear.
    assert str(tmp_path / "utilities") not in candidates
    # In-bundle junk: the .app itself, Contents/, and MacOS/ never have a
    # utilities directory in any Stata layout — discovery should skip them.
    assert str(app / "utilities") not in candidates
    assert str(app / "Contents" / "utilities") not in candidates
    assert str(app / "Contents" / "MacOS" / "utilities") not in candidates


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
