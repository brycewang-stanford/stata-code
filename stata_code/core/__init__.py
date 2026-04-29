"""stata_code core — pystata-first adapter with console fallback."""

from stata_code.core.version import detect_stata, StataEdition, StataVersion
from stata_code.core.result import StataResult, StataGraph
from stata_code.core.pystata_adapter import PystataAdapter
from stata_code.core.console_fallback import ConsoleFallback

__all__ = [
    "detect_stata",
    "StataEdition",
    "StataVersion",
    "StataResult",
    "StataGraph",
    "PystataAdapter",
    "ConsoleFallback",
]
