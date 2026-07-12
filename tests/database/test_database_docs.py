from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_database_docs_reference_v2_runtime_users():
    architecture_doc = (REPO_ROOT / "docs" / "database-architecture.md").read_text(
        encoding="utf-8"
    )
    assert "prod_rob_bot" in architecture_doc
    assert "prod_rob_webhook" in architecture_doc
    assert "rob_dev_v2" in architecture_doc
    assert "rob_prod" in architecture_doc


def test_env_example_excludes_migration_and_portal_urls():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "MIGRATION_DATABASE_URL" not in env_example
    assert "PORTAL_DATABASE_URL" not in env_example


def test_required_rebuild_docs_exist():
    for path in (
        REPO_ROOT / "docs" / "database-build.md",
        REPO_ROOT / "docs" / "sqlite-to-postgres-data-migration.md",
        REPO_ROOT / "docs" / "server-rebuild.md",
        REPO_ROOT / "docs" / "domain-routing.md",
    ):
        assert path.exists(), f"Missing expected doc: {path.name}"
