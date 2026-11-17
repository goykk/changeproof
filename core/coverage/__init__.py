"""覆盖率上下文解析、覆盖映射与四源测试选择。"""

from .mapper import build_coverage_records, coverage_recall, parse_coverage_json
from .selector import fallback_full_selection, select_tests

__all__ = [
    "parse_coverage_json",
    "build_coverage_records",
    "coverage_recall",
    "select_tests",
    "fallback_full_selection",
]