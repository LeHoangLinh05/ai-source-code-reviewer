"""Alembic contract for the qualitative report overview column."""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import Column


def test_analysis_overview_migration_upgrades_and_downgrades() -> None:
    migration = _load_migration()
    added_columns: list[tuple[str, Column[object]]] = []
    dropped_columns: list[tuple[str, str]] = []
    original_add_column = migration.op.add_column
    original_drop_column = migration.op.drop_column
    migration.op.add_column = lambda table, column: added_columns.append(
        (table, column)
    )
    migration.op.drop_column = lambda table, column: dropped_columns.append(
        (table, column)
    )
    try:
        migration.upgrade()
        migration.downgrade()
    finally:
        migration.op.add_column = original_add_column
        migration.op.drop_column = original_drop_column

    table_name, column = added_columns[0]
    assert table_name == "review_reports"
    assert column.name == "analysis_overview"
    assert column.nullable
    assert dropped_columns == [("review_reports", "analysis_overview")]


def _load_migration() -> ModuleType:
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260810_0014_add_report_analysis_overview.py"
    )
    spec = importlib.util.spec_from_file_location(
        "report_analysis_overview_migration",
        migration_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load report analysis overview migration")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
