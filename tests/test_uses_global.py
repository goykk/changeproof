"""USES_GLOBAL 边与容错解析的不变式（C-SCHEMA@EdgeType / C-IMPACT@1）。"""

from __future__ import annotations

from pathlib import Path

from core.indexer import index_repository


def test_uses_global_edge_points_to_module_file(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        "SETTING = 1\n\n\ndef use():\n    return SETTING\n",
        encoding="utf-8",
    )
    result = index_repository(str(tmp_path), "r", "c")
    edges = [edge for edge in result.edges if edge.edge_type.value == "uses_global"]
    assert edges, "函数体引用模块级变量应产生 USES_GLOBAL 边"
    assert edges[0].target_id == "mod.py"
    assert edges[0].source_id == "mod.py::use"


def test_lenient_parse_keeps_module_imports(tmp_path: Path) -> None:
    (tmp_path / "target_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text(
        "from target_mod import VALUE\n\n\ndef f():\n"
        "    try:\n"
        "        return VALUE\n"
        "    except A, B:\n"  # Python 3 语法错误，触发降级解析
        "        return None\n",
        encoding="utf-8",
    )
    result = index_repository(str(tmp_path), "r", "c")
    files = {symbol.file_path for symbol in result.symbols if symbol.kind.value == "file"}
    assert "broken.py" in files, "降级解析的文件也必须保留 FILE 符号"
    assert any(edge.source_id == "broken.py" for edge in result.edges), "降级解析必须保留导入边"
    assert any("已降级解析" in warning for warning in result.warnings)