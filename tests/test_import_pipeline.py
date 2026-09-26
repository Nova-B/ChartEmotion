"""D03/D04: 출처 게이트, 가져오기 원자성, 동일성·개정, 미병합."""

from __future__ import annotations

import unittest

from chart_emotion.application.import_chart import import_chart
from chart_emotion.application.sources import SourceSpec, register_source
from chart_emotion.errors import InputError

from helpers import BATCHES, CHARTS, INVALID, VALID_CSV, VERSIONS, WorkspaceCase, batch_dict, write_json, write_text


class DemoPipelineTest(WorkspaceCase):
    def test_two_periods_import_and_share_two_tracks(self) -> None:
        a = import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json")
        b = import_chart(self.ws, CHARTS / "period_b.csv", BATCHES / "period_b.json")
        self.assertEqual((a["status"], b["status"]), ("imported", "imported"))
        self.assertTrue(a["chart_created"])
        self.assertFalse(b["chart_created"])
        self.assertEqual((a["snapshot_id"], b["snapshot_id"]), ("demo-period-a-r1", "demo-period-b-r1"))
        self.assertEqual(
            self.table_counts(),
            {"sources": 1, "chart_definitions": 1, "chart_snapshots": 2, "chart_entries": 10, "recordings": 0, "lyric_versions": 0, "entry_mappings": 0},
        )
        distinct = self.query("SELECT COUNT(*) FROM (SELECT DISTINCT title, artist, provider_track_id FROM chart_entries)")
        self.assertEqual(distinct[0][0], 8)
        # 문자열이 같아도 항목은 (snapshot_id, rank) 마다 따로 저장된다
        shared = self.query(
            "SELECT title FROM chart_entries WHERE snapshot_id='demo-period-a-r1' "
            "INTERSECT SELECT title FROM chart_entries WHERE snapshot_id='demo-period-b-r1'"
        )
        self.assertEqual(len(shared), 2)

    def test_reimport_identical_is_noop(self) -> None:
        import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json")
        before = self.table_counts()
        again = import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json")
        self.assertEqual(again["status"], "already_imported")
        self.assertEqual(again["entries_inserted"], 0)
        self.assertEqual(self.table_counts(), before)

    def test_bom_variant_of_same_content_is_noop(self) -> None:
        plain = write_text(self.path("a.csv"), VALID_CSV)
        bom = write_text(self.path("a_bom.csv"), VALID_CSV, bom=True)
        batch = write_json(self.path("b.json"), batch_dict())
        first = import_chart(self.ws, plain, batch)
        second = import_chart(self.ws, bom, batch)
        self.assertEqual(first["status"], "imported")
        self.assertEqual(second["status"], "already_imported")
        self.assertEqual(self.table_counts()["chart_snapshots"], 1)

    def test_different_content_requires_explicit_revision(self) -> None:
        import_chart(self.ws, CHARTS / "period_c_incomplete.csv", BATCHES / "period_c.json")
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_c_corrected.csv", BATCHES / "period_c.json")
        self.assertEqual(ctx.exception.code, "snapshot_revision_required")
        detail = ctx.exception.details[0]
        self.assertEqual(detail["next_revision"], 2)
        self.assertTrue(detail["differences"]["content_differs"])
        self.assertFalse(detail["differences"]["config_differs"])
        self.assertEqual(self.table_counts(), before, "충돌 시 아무것도 덮어쓰거나 추가하지 않는다")

    def test_explicit_revision_appends_immutable_snapshot(self) -> None:
        first = import_chart(self.ws, CHARTS / "period_c_incomplete.csv", BATCHES / "period_c.json")
        self.assertEqual(first["snapshot_status"], "incomplete")
        self.assertEqual(first["missing_ranks"], [3])
        second = import_chart(self.ws, CHARTS / "period_c_corrected.csv", BATCHES / "period_c.json", revision=2)
        self.assertEqual(second["snapshot_id"], "demo-period-c-r2")
        self.assertEqual(second["snapshot_status"], "complete")
        rows = self.query("SELECT snapshot_id, revision, status FROM chart_snapshots ORDER BY revision")
        self.assertEqual(rows, [("demo-period-c-r1", 1, "incomplete"), ("demo-period-c-r2", 2, "complete")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM chart_entries WHERE snapshot_id='demo-period-c-r1'")[0][0], 4)
        # 잘못된 revision 번호는 거부
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_c.json", revision=5)
        self.assertEqual(ctx.exception.code, "revision_mismatch")
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json", revision=2)
        self.assertEqual(ctx.exception.code, "revision_mismatch")
        with self.assertRaises(InputError):
            import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json", revision=0)

    def test_config_change_same_period_requires_revision(self) -> None:
        import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json")
        changed = write_json(self.path("a2.json"), batch_dict(snapshot_key="demo-period-a", methodology_version="synthetic-v2"))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", changed)
        diff = ctx.exception.details[0]["differences"]
        self.assertFalse(diff["content_differs"])
        self.assertTrue(diff["config_differs"])
        self.assertEqual(diff["config_fields"][0]["field"], "methodology_version")

    def test_same_title_different_versions_are_not_merged(self) -> None:
        result = import_chart(self.ws, VERSIONS / "same_title_versions.csv", VERSIONS / "batch_versions.json")
        self.assertEqual(result["entries_inserted"], 3)
        rows = self.query("SELECT rank, title, artist, provider_track_id FROM chart_entries ORDER BY rank")
        self.assertEqual(len(rows), 3)
        self.assertEqual({r[1] for r in rows}, {"가상곡 가"})
        self.assertEqual(len({r[3] for r in rows}), 3)
        # 차트 가져오기는 녹음·매핑을 만들지 않는다(문자열로 자동 매칭하지 않음)
        self.assertEqual(self.query("SELECT COUNT(*) FROM recordings")[0][0], 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM entry_mappings")[0][0], 0)


class AtomicityTest(WorkspaceCase):
    def assert_nothing_changed(self, before: dict[str, int]) -> None:
        self.assertEqual(self.table_counts(), before)

    def test_invalid_dates_with_new_chart_creates_nothing(self) -> None:
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", INVALID / "invalid_dates.json")
        self.assertEqual(ctx.exception.code, "batch_config_invalid")
        self.assert_nothing_changed(before)
        self.assertEqual(self.query("SELECT COUNT(*) FROM chart_definitions WHERE chart_id LIKE 'synthetic-weekly%'")[0][0], 0)

    def test_invalid_csv_with_new_chart_creates_nothing(self) -> None:
        before = self.table_counts()
        batch = write_json(self.path("new_chart.json"), batch_dict(chart_id="brand-new-chart", snapshot_key="nc"))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, INVALID / "duplicate_rank.csv", batch)
        self.assertEqual(ctx.exception.code, "csv_invalid")
        self.assert_nothing_changed(before)

    def test_huge_rank_csv_persists_nothing(self) -> None:
        before = self.table_counts()
        csv_path = write_text(self.path("huge.csv"), "rank,title,artist,provider_track_id\n" + "9" * 5000 + ",가상곡,가수,demo-x\n")
        batch = write_json(self.path("b.json"), batch_dict(chart_id="brand-new-chart", snapshot_key="huge"))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, csv_path, batch)
        self.assertEqual(ctx.exception.code, "csv_invalid")
        self.assert_nothing_changed(before)

    def test_all_zero_rank_csv_persists_nothing(self) -> None:
        before = self.table_counts()
        csv_path = write_text(self.path("zeros.csv"), "rank,title,artist,provider_track_id\n" + "0" * 5000 + ",가상곡,가수,demo-x\n")
        batch = write_json(self.path("b.json"), batch_dict(chart_id="brand-new-chart", snapshot_key="zeros"))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, csv_path, batch)
        self.assertEqual(ctx.exception.code, "csv_invalid")
        self.assert_nothing_changed(before)

    def test_duplicate_key_batch_persists_nothing(self) -> None:
        before = self.table_counts()
        batch = write_text(
            self.path("dup.json"),
            '{"source_id": "synthetic-demo", "chart_id": "brand-new-chart", "period_start": "2015-01-01", '
            '"period_end": "2015-12-31", "display_year": 2015, "methodology_version": "v", "expected_n": 5, '
            '"data_mode": "synthetic", "expected_n": 4, '
            '"chart": {"name": "n", "market": "synthetic", "metric": "m", "periodicity": "yearly"}}',
        )
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", batch)
        self.assertEqual(ctx.exception.code, "json_duplicate_key")
        self.assert_nothing_changed(before)

    def test_missing_source_creates_no_chart(self) -> None:
        before = self.table_counts()
        batch = write_json(self.path("b.json"), batch_dict(source_id="not-registered", chart_id="brand-new-chart"))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", batch)
        self.assertEqual(ctx.exception.code, "source_not_registered")
        self.assert_nothing_changed(before)

    def test_chart_definition_mismatch_is_rejected(self) -> None:
        import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json")
        before = self.table_counts()
        batch = write_json(
            self.path("b.json"),
            batch_dict(snapshot_key="x", period_start="2016-01-01", period_end="2016-12-31", display_year=2016,
                       chart={"name": "n", "market": "synthetic", "metric": "synthetic_score", "periodicity": "weekly"}),
        )
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", batch)
        self.assertEqual(ctx.exception.code, "chart_definition_mismatch")
        self.assert_nothing_changed(before)

    def test_missing_chart_block_for_new_chart(self) -> None:
        before = self.table_counts()
        batch = write_json(self.path("b.json"), batch_dict(chart_id="unknown-chart", chart=None))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", batch)
        self.assertEqual(ctx.exception.code, "chart_not_registered")
        self.assert_nothing_changed(before)

    def test_snapshot_id_conflict_across_observations(self) -> None:
        import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json")
        before = self.table_counts()
        batch = write_json(self.path("b.json"), batch_dict(snapshot_key="demo-period-a", period_start="2016-01-01", period_end="2016-12-31", display_year=2016))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", batch)
        self.assertEqual(ctx.exception.code, "snapshot_id_conflict")
        self.assert_nothing_changed(before)

    def test_uninitialized_workspace(self) -> None:
        with self.assertRaises(InputError) as ctx:
            import_chart(self.path("nope"), CHARTS / "period_a.csv", BATCHES / "period_a.json")
        self.assertEqual(ctx.exception.code, "workspace_not_initialized")
        self.assertFalse(self.path("nope").exists())


class SourceGateTest(WorkspaceCase):
    def real_spec(self, **overrides):
        base = dict(
            source_id="real-chart-x",
            name="실제 차트 예시(등록만)",
            kind="chart",
            data_mode="real",
            status="pending",
            allowed_operations=(),
        )
        base.update(overrides)
        return SourceSpec(**base)

    def real_batch(self, **overrides):
        return write_json(
            self.path("real.json"),
            batch_dict(source_id="real-chart-x", chart_id="real-chart", snapshot_key="real-a", data_mode="real", **overrides),
        )

    def test_synthetic_batch_rejects_real_source(self) -> None:
        register_source(self.ws, self.real_spec(status="permitted", allowed_operations=("import", "store", "analyze"), checked_on="2026-09-26", evidence="테스트용 가짜 근거 문자열"))
        batch = write_json(self.path("b.json"), batch_dict(source_id="real-chart-x"))
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", batch)
        self.assertEqual(ctx.exception.code, "source_not_permitted")
        self.assertIn("synthetic_requires_synthetic_source", [d["code"] for d in ctx.exception.details])
        self.assertEqual(self.table_counts(), before)

    def test_real_batch_rejects_synthetic_source(self) -> None:
        batch = write_json(self.path("b.json"), batch_dict(data_mode="real", chart_id="real-chart"))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", batch)
        self.assertIn("real_requires_real_source", [d["code"] for d in ctx.exception.details])

    def test_pending_real_source_blocks_import(self) -> None:
        register_source(self.ws, self.real_spec())
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", self.real_batch())
        self.assertEqual(ctx.exception.code, "source_not_permitted")
        self.assertIn("source_status_not_permitted", [d["code"] for d in ctx.exception.details])
        self.assertEqual(self.table_counts(), before)

    def test_permitted_real_source_requires_evidence_and_checked_on(self) -> None:
        with self.assertRaises(InputError) as ctx:
            register_source(self.ws, self.real_spec(status="permitted", allowed_operations=("import",)))
        codes = {d["field"]: d["code"] for d in ctx.exception.details}
        self.assertEqual(codes["checked_on"], "evidence_required")
        self.assertEqual(codes["evidence"], "evidence_required")
        self.assertEqual(self.table_counts()["sources"], 1)

    def test_grants_only_with_permitted_status(self) -> None:
        with self.assertRaises(InputError) as ctx:
            register_source(self.ws, self.real_spec(status="pending", allowed_operations=("import",)))
        self.assertEqual(ctx.exception.details[0]["code"], "grant_requires_permitted")

    def test_permitted_without_store_grant_blocks_import(self) -> None:
        register_source(self.ws, self.real_spec(status="permitted", allowed_operations=("import",), checked_on="2026-09-26", evidence="테스트용 가짜 근거 문자열"))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", self.real_batch())
        reasons = [d["code"] for d in ctx.exception.details]
        self.assertIn("operation_not_granted", reasons)

    def test_permitted_with_grants_imports(self) -> None:
        register_source(self.ws, self.real_spec(status="permitted", allowed_operations=("import", "store"), checked_on="2026-09-26", evidence="테스트용 가짜 근거 문자열"))
        result = import_chart(self.ws, CHARTS / "period_a.csv", self.real_batch())
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["data_mode"], "real")
        self.assertIn("real", result["notice"])

    def test_register_source_idempotent_and_replace(self) -> None:
        spec = self.real_spec()
        first = register_source(self.ws, spec)
        self.assertEqual(first["status"], "registered")
        self.assertEqual(register_source(self.ws, spec)["status"], "unchanged")
        changed = self.real_spec(name="다른 이름")
        with self.assertRaises(InputError) as ctx:
            register_source(self.ws, changed)
        self.assertEqual(ctx.exception.code, "source_exists")
        self.assertEqual(register_source(self.ws, changed, replace_existing=True)["status"], "updated")
        with self.assertRaises(InputError) as ctx:
            register_source(self.ws, self.real_spec(data_mode="synthetic"), replace_existing=True)
        self.assertEqual(ctx.exception.code, "source_data_mode_immutable")

    def test_lyrics_source_cannot_import_charts(self) -> None:
        register_source(self.ws, SourceSpec(source_id="synthetic-lyrics", name="가상 가사", kind="lyrics", data_mode="synthetic", status="permitted", allowed_operations=("import", "store", "analyze")))
        batch = write_json(self.path("b.json"), batch_dict(source_id="synthetic-lyrics"))
        with self.assertRaises(InputError) as ctx:
            import_chart(self.ws, CHARTS / "period_a.csv", batch)
        self.assertIn("source_kind_mismatch", [d["code"] for d in ctx.exception.details])


if __name__ == "__main__":
    unittest.main()
