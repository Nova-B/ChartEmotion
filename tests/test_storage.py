"""D03: 스키마·마이그레이션·작업 폴더 상태."""

from __future__ import annotations

import sqlite3
import unittest

from chart_emotion.application.init_workspace import init_workspace
from chart_emotion.application.sources import SYNTHETIC_DEMO_SOURCE_ID
from chart_emotion.errors import WorkspaceError
from chart_emotion.storage.database import SCHEMA_VERSION, database_path, open_workspace

from helpers import BATCHES, CHARTS, TABLES, TempDirCase, WorkspaceCase


class InitTest(TempDirCase):
    def test_init_creates_schema_and_seed(self) -> None:
        ws = self.path("ws")
        result = init_workspace(ws)
        self.assertTrue(result["database_created"])
        self.assertEqual(result["schema_version"], SCHEMA_VERSION)
        self.assertEqual(result["seeded_sources"], [SYNTHETIC_DEMO_SOURCE_ID])
        conn = sqlite3.connect(str(database_path(ws)))
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(set(TABLES).issubset(tables))
            self.assertIn("schema_meta", tables)
            version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
            self.assertEqual(int(version), SCHEMA_VERSION)
        finally:
            conn.close()

    def test_init_is_idempotent_and_preserves_data(self) -> None:
        from chart_emotion.application.import_chart import import_chart

        ws = self.path("ws")
        init_workspace(ws)
        import_chart(ws, CHARTS / "period_a.csv", BATCHES / "period_a.json")
        again = init_workspace(ws)
        self.assertFalse(again["database_created"])
        self.assertTrue(again["existing_data_preserved"])
        self.assertEqual(again["seeded_sources"], [])
        self.assertEqual(
            again["counts"],
            {"sources": 1, "snapshots": 1, "entries": 5, "recordings": 0, "lyric_versions": 0, "mapping_revisions": 0},
        )

    def test_init_creates_nested_korean_path(self) -> None:
        ws = self.path("한글 폴더", "작업")
        result = init_workspace(ws)
        self.assertTrue(database_path(ws).is_file())
        self.assertEqual(result["counts"]["sources"], 1)

    def test_foreign_keys_enabled_on_every_connection(self) -> None:
        ws = self.path("ws")
        init_workspace(ws)
        conn = open_workspace(ws)
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO chart_entries (snapshot_id, rank, title, artist) VALUES ('ghost', 1, 't', 'a')"
                )
        finally:
            conn.close()

    def test_read_command_does_not_create_database(self) -> None:
        ws = self.path("missing")
        with self.assertRaises(WorkspaceError) as ctx:
            open_workspace(ws)
        self.assertEqual(ctx.exception.code, "workspace_not_initialized")
        self.assertFalse(ws.exists())
        ws.mkdir()
        with self.assertRaises(WorkspaceError):
            open_workspace(ws)
        self.assertFalse(database_path(ws).exists())

    def test_newer_schema_is_refused_without_mutation(self) -> None:
        ws = self.path("ws")
        init_workspace(ws)
        db = database_path(ws)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 5),))
        conn.commit()
        conn.close()
        before = db.read_bytes()
        with self.assertRaises(WorkspaceError) as ctx:
            open_workspace(ws)
        self.assertEqual(ctx.exception.code, "schema_too_new")
        with self.assertRaises(WorkspaceError) as ctx:
            init_workspace(ws)
        self.assertEqual(ctx.exception.code, "schema_too_new")
        self.assertEqual(db.read_bytes(), before, "거부 시 DB 파일이 바뀌면 안 된다")

    def test_unknown_layout_is_refused(self) -> None:
        ws = self.path("ws")
        ws.mkdir()
        conn = sqlite3.connect(str(database_path(ws)))
        conn.execute("CREATE TABLE something_else (x INTEGER)")
        conn.commit()
        conn.close()
        with self.assertRaises(WorkspaceError) as ctx:
            open_workspace(ws)
        self.assertEqual(ctx.exception.code, "schema_unknown_layout")
        with self.assertRaises(WorkspaceError) as ctx:
            init_workspace(ws)
        self.assertEqual(ctx.exception.code, "schema_unknown_layout")

    def test_failed_first_init_leaves_no_half_database(self) -> None:
        # 파일 자리에 폴더가 있으면 DB 를 만들 수 없다 → 오류, 새 파일 없음
        ws = self.path("ws")
        database_path(ws).mkdir(parents=True)
        with self.assertRaises((WorkspaceError, sqlite3.Error)):
            init_workspace(ws)


def create_populated_v1_workspace(ws) -> None:
    """스키마 v1 만 적용된 DB 를 만들고 v1 자료(출처·차트·스냅샷·항목 5개)를 채운다."""
    from chart_emotion.storage.database import MIGRATIONS, _split_statements

    ws.mkdir(parents=True)
    conn = sqlite3.connect(str(database_path(ws)))
    for statement in _split_statements(MIGRATIONS[1]):
        conn.execute(statement)
    conn.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1')")
    conn.execute(
        "INSERT INTO sources (source_id, name, kind, data_mode, status, allowed_operations, registered_at, updated_at) "
        "VALUES ('synthetic-demo', '가상', 'chart', 'synthetic', 'permitted', '[\"import\", \"store\", \"analyze\"]', 't', 't')"
    )
    conn.execute("INSERT INTO chart_definitions VALUES ('synthetic-annual-top5', '가상', 'synthetic', 'synthetic_score', 'yearly', 't')")
    conn.execute(
        "INSERT INTO chart_snapshots (snapshot_id, chart_id, source_id, period_start, period_end, display_year, methodology_version, "
        "expected_n, data_mode, revision, status, csv_filename, file_sha256, content_sha256, config_sha256, imported_at) "
        "VALUES ('demo-period-a-r1', 'synthetic-annual-top5', 'synthetic-demo', '2015-01-01', '2015-12-31', 2015, 'synthetic-v1', "
        "5, 'synthetic', 1, 'complete', 'a.csv', 'f', 'c', 'g', 't')"
    )
    for rank in range(1, 6):
        conn.execute("INSERT INTO chart_entries VALUES ('demo-period-a-r1', ?, ?, ?, ?)", (rank, f"가상곡 {rank}", "가상가수", f"demo-{rank:03d}"))
    conn.commit()
    conn.close()


class MigrationV1ToV2Test(TempDirCase):
    def test_v1_workspace_refuses_operations_until_init(self) -> None:
        from chart_emotion.application.import_chart import import_chart
        from chart_emotion.application.mappings import show_mappings
        from chart_emotion.application.validate import validate_workspace

        ws = self.path("ws")
        create_populated_v1_workspace(ws)
        before = database_path(ws).read_bytes()
        for call in (
            lambda: open_workspace(ws),
            lambda: validate_workspace(ws),
            lambda: show_mappings(ws),
            lambda: import_chart(ws, CHARTS / "period_b.csv", BATCHES / "period_b.json"),
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                call()
            self.assertEqual(ctx.exception.code, "schema_migration_required")
            self.assertEqual(ctx.exception.details[0], {"found": 1, "supported": SCHEMA_VERSION})
        self.assertEqual(database_path(ws).read_bytes(), before, "거부 시 DB 가 바뀌면 안 된다")

    def test_init_migrates_v1_to_v2_preserving_data(self) -> None:
        ws = self.path("ws")
        create_populated_v1_workspace(ws)
        result = init_workspace(ws)
        self.assertEqual(result["schema_version_before"], 1)
        self.assertEqual(result["schema_version"], 2)
        self.assertTrue(result["migrated"])
        self.assertFalse(result["database_created"])
        self.assertEqual(result["counts"]["entries"], 5)
        self.assertEqual(result["counts"]["snapshots"], 1)
        self.assertEqual(result["counts"]["recordings"], 0)
        conn = sqlite3.connect(str(database_path(ws)))
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"recordings", "lyric_versions", "entry_mappings"}.issubset(tables))
            self.assertEqual(conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0], "2")
            rows = conn.execute("SELECT rank, title FROM chart_entries ORDER BY rank").fetchall()
            self.assertEqual(rows[0], (1, "가상곡 1"))
            self.assertEqual(len(rows), 5)
        finally:
            conn.close()
        # 마이그레이션 후 D05 작업이 동작한다
        from chart_emotion.application.mappings import import_mappings
        from chart_emotion.application.recordings import import_recordings

        from helpers import mapping_dict, mappings_file, recording_dict, recordings_file, write_json

        import_recordings(ws, write_json(self.path("r.json"), recordings_file([recording_dict()])))
        out = import_mappings(ws, write_json(self.path("m.json"), mappings_file([mapping_dict(recording_id="rec-test-001", lyric_version_id=None)])))
        self.assertEqual(out["appended"][0]["mapping_id"], "demo-period-a-r1.rank1.r1")
        again = init_workspace(ws)
        self.assertFalse(again["migrated"])
        self.assertEqual(again["counts"]["mapping_revisions"], 1)

    def test_entry_mappings_foreign_keys(self) -> None:
        ws = self.path("ws")
        init_workspace(ws)
        conn = open_workspace(ws)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO entry_mappings (mapping_id, snapshot_id, rank, revision, base_revision, state, recording_id, reviewer_id, reason, created_at) "
                    "VALUES ('x', 'ghost', 1, 1, 0, 'confirmed', 'ghost-rec', 'r', 'why', 't')"
                )
            with self.assertRaises(sqlite3.IntegrityError):  # unknown 정밀도인데 날짜 있음
                conn.execute(
                    "INSERT INTO recordings (recording_id, title, artist, version_kind, release_precision, release_date, vocal_type, data_mode, "
                    "provider_track_ids, content_sha256, registered_by, registered_at) VALUES ('r', 't', 'a', 'original', 'unknown', '2015', 'lyrical', 'synthetic', '[]', 'h', 'x', 't')"
                )
        finally:
            conn.close()


class WorkspaceStateTest(WorkspaceCase):
    def test_seeded_synthetic_source(self) -> None:
        rows = self.query("SELECT source_id, data_mode, status, allowed_operations FROM sources")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], SYNTHETIC_DEMO_SOURCE_ID)
        self.assertEqual(rows[0][1], "synthetic")
        self.assertEqual(rows[0][2], "permitted")
        self.assertIn("import", rows[0][3])


if __name__ == "__main__":
    unittest.main()
