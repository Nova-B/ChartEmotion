"""`init <workspace>`: 폴더·DB 생성, 마이그레이션, 가상 출처 시드. 재실행해도 기존 자료를 보존한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import __version__
from ..storage.database import SCHEMA_VERSION, database_path, initialize_workspace, integrity_check
from .sources import SYNTHETIC_DEMO_SOURCE_ID, ensure_synthetic_demo_source, list_sources

SYNTHETIC_NOTICE = "가상 자료(synthetic) 시연용 작업 폴더입니다. 실제 차트·가사 분석 결과가 아닙니다."


def init_workspace(workspace: Path) -> dict[str, Any]:
    workspace = Path(workspace)
    conn, previous_version, created = initialize_workspace(workspace)
    try:
        seeded = ensure_synthetic_demo_source(conn)
        sources = list_sources(conn)
        snapshot_count = conn.execute("SELECT COUNT(*) FROM chart_snapshots").fetchone()[0]
        entry_count = conn.execute("SELECT COUNT(*) FROM chart_entries").fetchone()[0]
        fk_violations = integrity_check(conn)
    finally:
        conn.close()
    return {
        "command": "init",
        "ok": True,
        "app_version": __version__,
        "workspace": str(workspace.resolve()),
        "database": str(database_path(workspace).resolve()),
        "database_created": created,
        "schema_version_before": previous_version,
        "schema_version": SCHEMA_VERSION,
        "migrated": previous_version != SCHEMA_VERSION,
        "seeded_sources": [SYNTHETIC_DEMO_SOURCE_ID] if seeded else [],
        "existing_data_preserved": not created,
        "counts": {"sources": len(sources), "snapshots": snapshot_count, "entries": entry_count},
        "foreign_key_violations": fk_violations,
        "notice": SYNTHETIC_NOTICE,
    }
