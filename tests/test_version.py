"""Tests for Stata version detection."""

import pytest

from stata_code.core.version import (
    detect_stata,
    StataEdition,
    StataVersion,
)


class TestStataVersion:
    def test_supports_pystata_17plus(self):
        v = StataVersion(edition=StataEdition.SE, version="17.0", major=17, minor=0)
        assert v.supports_pystata is True

    def test_supports_pystata_16(self):
        v = StataVersion(edition=StataEdition.SE, version="16.0", major=16, minor=0)
        assert v.supports_pystata is False

    def test_supports_pystata_18(self):
        v = StataVersion(edition=StataEdition.MP, version="18.0", major=18, minor=0)
        assert v.supports_pystata is True

    def test_is_stata_installed_unknown(self):
        v = StataVersion(edition=StataEdition.UNKNOWN, version="", major=0, minor=0)
        assert v.is_stata_installed is False

    def test_is_stata_installed_found(self):
        v = StataVersion(edition=StataEdition.SE, version="18.0", major=18, minor=0)
        assert v.is_stata_installed is True


class TestDetectStata:
    def test_returns_stata_version(self):
        v = detect_stata()
        assert isinstance(v, StataVersion)
        # On a machine without Stata, edition is UNKNOWN and major is 0
        assert isinstance(v.supports_pystata, bool)
        assert isinstance(v.is_stata_installed, bool)
