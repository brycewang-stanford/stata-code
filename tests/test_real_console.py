"""The console backend's variable-format probe against a real batch Stata.

`VariableInfo.format` / `.value_label` come, on this backend, from two macro
functions the batch wrapper evaluates per variable (`: format`, `: value
label`). The parser is unit-tested on a recorded log (tests/test_console.py);
this runs the wrapper in a real `stata -b` so those functions are checked as
Stata evaluates them. Skipped when no Stata command-line executable is found.
"""

from __future__ import annotations

import pytest

from stata_code import is_available, run
from stata_code.core import console

pytestmark = [
    pytest.mark.stata_required,
    pytest.mark.skipif(
        not console.console_available(),
        reason="no Stata command-line executable found",
    ),
]


class TestConsoleFormatProbeReal:
    CODE = "\n".join(
        [
            "sysuse auto, clear",
            "keep make mpg price foreign",
            "generate long day = mdy(1, 1, 2020) + _n",
            "format day %td",
            "format price %12.2fc",
            'label variable day "Sale date | first"',
            # compound quotes: the only way to put a double quote in a label
            "label variable mpg `\"Mileage \"quoted\" (mpg)\"'",
        ]
    )

    def test_formats_and_value_labels_reach_the_result(self):
        r = console.execute(self.CODE, timeout_ms=120_000)
        assert r.ok, r.error
        assert r.dataset.n_obs == 74 and r.dataset.n_vars == 5
        by_name = {v.name: v for v in r.dataset.variables}
        assert list(by_name) == ["make", "price", "mpg", "foreign", "day"]
        # Stata's default format for the storage type is not reported ...
        assert by_name["mpg"].format is None
        # ... anything else is, exactly as `describe` shows it
        assert by_name["make"].format == "%-18s"
        assert by_name["price"].format == "%12.2fc"
        assert by_name["day"].format == "%td"
        assert by_name["foreign"].value_label == "origin"
        assert by_name["mpg"].value_label is None
        # a "|" or a double quote in a variable label does not shift the
        # fields or end the listing early (it used to drop every variable
        # after the first quoted label)
        assert by_name["day"].label == "Sale date | first"
        assert by_name["mpg"].label == 'Mileage "quoted" (mpg)'
        assert by_name["day"].type == "long"

    def test_matches_the_pystata_backend(self):
        if not is_available():
            pytest.skip("pystata / Stata 17+ not available")
        batch = console.execute(self.CODE, timeout_ms=120_000)
        live = run(self.CODE, session_id="rs_console_fmt", include_full_log=False)
        assert batch.ok and live.ok, (batch.error, live.error)

        def facts(result):
            return [
                (v.name, v.type, v.label, v.format, v.value_label)
                for v in result.dataset.variables
            ]

        assert facts(batch) == facts(live)
