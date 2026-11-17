"""五类故障注入的降级不变式（C-FAULT@1）。"""

from __future__ import annotations

import pytest

from scripts.fault_injection import CASES


@pytest.mark.parametrize("name,case", CASES, ids=[name for name, _ in CASES])
def test_fault_case(name, case, tmp_path) -> None:
    outcome = case(tmp_path)
    assert outcome["pass"], f"{name}: {outcome['detail']}"