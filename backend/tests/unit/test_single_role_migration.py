"""Single-role contract and legacy account migration tests."""

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

from app.models.user import UserRole


def test_application_exposes_only_user_role() -> None:
    assert list(UserRole) == [UserRole.USER]
    assert UserRole.USER.value == "user"


def test_migration_normalizes_legacy_admin_accounts() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE users (role TEXT NOT NULL)")
    connection.executemany(
        "INSERT INTO users (role) VALUES (?)",
        [("admin",), ("user",)],
    )
    migration = load_single_role_migration()
    original_execute = migration.op.execute
    migration.op.execute = connection.execute
    try:
        migration.upgrade()
    finally:
        migration.op.execute = original_execute

    roles = [row[0] for row in connection.execute("SELECT role FROM users")]
    assert roles == ["user", "user"]


def load_single_role_migration() -> ModuleType:
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "20260728_0008_normalize_user_roles.py"
    )
    spec = importlib.util.spec_from_file_location(
        "single_role_migration",
        migration_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load single-role Alembic migration")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
