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
        self.assertEqual(again["counts"], {"sources": 1, "snapshots": 1, "entries": 5})

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
