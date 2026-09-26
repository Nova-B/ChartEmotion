"""validate: 작업 폴더 보고와 실험 참조·비교 가능성 검증."""

from __future__ import annotations

import unittest

from chart_emotion.application.import_chart import import_chart
from chart_emotion.application.sources import SourceSpec, register_source
from chart_emotion.application.validate import validate_experiment, validate_workspace
from chart_emotion.errors import InputError

from helpers import (
    BATCHES,
    CHARTS,
    EXPERIMENTS,
    MAPPINGS,
    VALID_CSV,
    WorkspaceCase,
    batch_dict,
    experiment_dict,
    mapping_dict,
    mappings_file,
    write_json,
    write_text,
)


def error_codes(report: dict) -> list[str]:
    return [e["code"] for e in report["errors"]]


def warning_codes(report: dict) -> list[str]:
    return [w["code"] for w in report["warnings"]]


def collect_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(str(k))
            keys |= collect_keys(v)
    elif isinstance(value, list):
        for item in value:
            keys |= collect_keys(item)
    return keys


class WorkspaceValidateTest(WorkspaceCase):
    def test_empty_workspace(self) -> None:
        report = validate_workspace(self.ws)
        self.assertTrue(report["ok"])
        self.assertEqual(report["totals"]["snapshots"], 0)
        self.assertEqual(report["matching"]["status"], "no_entries")
        self.assertEqual(report["lyrics"]["status"], "metadata_only")
        self.assertEqual(report["labeling"]["status"], "not_analyzed")
        self.assertEqual(report["analysis"]["status"], "not_performed")
        self.assertTrue(report["foreign_keys_enabled"])

    def test_demo_totals_and_notice(self) -> None:
        self.import_demo()
        report = validate_workspace(self.ws)
        self.assertTrue(report["ok"])
        self.assertIn("가상", report["data_mode_notice"])
        self.assertEqual(report["totals"]["entries_all_revisions"], 10)
        self.assertEqual(report["totals"]["entries_latest_revisions"], 10)
        self.assertEqual([s["snapshot_id"] for s in report["snapshots"]], ["demo-period-a-r1", "demo-period-b-r1"])
        self.assertEqual(report["issues"], [])
        # 어떤 정서 수치도 출력하지 않는다: 금지 키 이름을 재귀적으로 확인
        forbidden = {"scores", "metrics", "ratio", "ratios", "present_ratio", "coverage", "p", "a", "u", "anxiety_index"}
        self.assertEqual(collect_keys(report) & forbidden, set())

    def test_incomplete_and_superseded_reported(self) -> None:
        import_chart(self.ws, CHARTS / "period_c_incomplete.csv", BATCHES / "period_c.json")
        report = validate_workspace(self.ws)
        self.assertEqual([i["code"] for i in report["issues"]], ["snapshot_incomplete"])
        self.assertEqual(report["issues"][0]["missing_ranks"], [3])
        self.assertTrue(report["ok"], "incomplete 는 경고이지 저장소 오류가 아니다")
        import_chart(self.ws, CHARTS / "period_c_corrected.csv", BATCHES / "period_c.json", revision=2)
        report = validate_workspace(self.ws)
        codes = sorted(i["code"] for i in report["issues"])
        self.assertEqual(codes, ["snapshot_incomplete", "snapshot_superseded"])
        self.assertEqual(report["totals"]["entries_all_revisions"], 9)
        self.assertEqual(report["totals"]["entries_latest_revisions"], 5)
        self.assertEqual(report["totals"]["snapshots_latest_revision"], 1)

    def test_matching_scope_uses_latest_revisions_only(self) -> None:
        from chart_emotion.application.mappings import import_mappings

        self.import_demo()
        self.import_demo_recordings()
        import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        import_chart(self.ws, CHARTS / "period_c_incomplete.csv", BATCHES / "period_c.json")
        import_chart(self.ws, CHARTS / "period_c_corrected.csv", BATCHES / "period_c.json", revision=2)
        report = validate_workspace(self.ws)
        matching = report["matching"]
        self.assertEqual(matching["scope_snapshot_ids"], ["demo-period-a-r1", "demo-period-c-r2", "demo-period-b-r1"])
        self.assertEqual(matching["entries"], 15)
        self.assertEqual(matching["states"], {"confirmed": 3, "candidate": 1, "unresolved": 1, "unmapped": 10})
        self.assertEqual(matching["status"], "in_progress")
        self.assertEqual(report["totals"]["recordings"], 10)
        self.assertEqual(report["totals"]["lyric_versions"], 10)
        self.assertEqual(report["totals"]["mapping_revisions"], 5)
        self.assertEqual(report["lyrics"]["by_status"]["missing"], 3)
        self.assertTrue(report["ok"])

    def test_pending_real_source_listed(self) -> None:
        register_source(self.ws, SourceSpec(source_id="real-x", name="실제 예시", kind="chart", data_mode="real", status="pending", allowed_operations=()))
        report = validate_workspace(self.ws)
        self.assertIn("source_pending", [i["code"] for i in report["issues"]])
        self.assertEqual(len(report["sources"]), 2)


class ExperimentValidateTest(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_demo()

    def exp(self, **overrides):
        return write_json(self.path("exp.json"), experiment_dict(**overrides))

    def test_demo_experiment_is_valid(self) -> None:
        report = validate_experiment(self.ws, EXPERIMENTS / "demo_comparison.json")
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["errors"], [])
        comp = report["comparability"]
        self.assertTrue(comp["same_chart"] and comp["distinct_periods"] and comp["same_methodology"] and comp["same_top_n"])
        self.assertEqual(comp["periodicity"], "yearly")
        self.assertEqual(comp["identical_string_rows_across_snapshots"], 2)
        self.assertEqual(report["analysis"]["status"], "not_performed")
        self.assertEqual(report["labeling"]["status"], "not_analyzed")

    def test_experiment_matching_progress_scoped_to_selected_snapshots(self) -> None:
        from chart_emotion.application.mappings import import_mappings

        self.import_demo_recordings()
        report = validate_experiment(self.ws, EXPERIMENTS / "demo_comparison.json")
        self.assertTrue(report["ok"])
        self.assertEqual(report["matching"]["status"], "not_started")
        self.assertIn("matching_incomplete", warning_codes(report))
        import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        import_mappings(self.ws, MAPPINGS / "period_b_initial.json")
        import_chart(self.ws, write_text(self.path("o.csv"), VALID_CSV), write_json(self.path("o.json"), batch_dict(snapshot_key="other", period_start="2016-01-01", period_end="2016-12-31", display_year=2016)))
        report = validate_experiment(self.ws, EXPERIMENTS / "demo_comparison.json")
        matching = report["matching"]
        self.assertEqual(matching["scope_snapshot_ids"], ["demo-period-a-r1", "demo-period-b-r1"])
        self.assertEqual(matching["entries"], 10, "다른 스냅샷(other-r1)은 범위에 들어가지 않는다")
        self.assertEqual(matching["states"], {"confirmed": 7, "candidate": 2, "unresolved": 1, "unmapped": 0})
        self.assertEqual(matching["confirmed_recordings_in_all_snapshots"], ["rec-demo-001"])
        b_entries = matching["entries_by_snapshot"]["demo-period-b-r1"]
        self.assertEqual([(e["rank"], e["state"], e["lyric_status"], e["vocal_type"]) for e in b_entries][:3],
                         [(1, "confirmed", "missing", "lyrical"), (2, "confirmed", "available", "lyrical"), (3, "confirmed", "not_applicable", "instrumental")])
        self.assertEqual([w["code"] for w in report["warnings"]], ["matching_incomplete"])
        self.assertTrue(report["ok"], "매칭 미완료는 검증 오류가 아니라 경고다")
        import_mappings(self.ws, MAPPINGS / "corrections.json")
        import_mappings(self.ws, MAPPINGS / "reconfirm.json")
        report = validate_experiment(self.ws, EXPERIMENTS / "demo_comparison.json")
        self.assertEqual(report["matching"]["status"], "complete")
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["labeling"]["status"], "not_analyzed")
        self.assertEqual(report["analysis"]["status"], "not_performed")

    def test_experiment_lyrics_summary_scoped_to_selected_snapshots(self) -> None:
        from chart_emotion.application.mappings import import_mappings
        from chart_emotion.application.recordings import import_recordings

        from helpers import lyric_dict, recording_dict, recordings_file

        self.import_demo_recordings()
        import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        import_mappings(self.ws, MAPPINGS / "period_b_initial.json")
        # 어떤 항목에도 매핑되지 않은 카탈로그 등록(가사 available 2건)
        import_recordings(self.ws, write_json(self.path("extra.json"), recordings_file(
            [recording_dict(recording_id="rec-extra-1"), recording_dict(recording_id="rec-extra-2")],
            [lyric_dict(lyric_version_id="lyr-extra-1", recording_id="rec-extra-1"), lyric_dict(lyric_version_id="lyr-extra-2", recording_id="rec-extra-2")],
        )))
        report = validate_experiment(self.ws, EXPERIMENTS / "demo_comparison.json")
        lyrics = report["lyrics"]
        self.assertEqual(lyrics["scope"], "selected_snapshots_latest_mappings")
        self.assertEqual(lyrics["scope_snapshot_ids"], ["demo-period-a-r1", "demo-period-b-r1"])
        self.assertEqual(lyrics["entries"], 10)
        # A: confirmed 1(available),3(partial),4(missing); B: confirmed 1(missing),2(available),3(not_applicable),5(translation_only); 후보 2·보류 1
        self.assertEqual(
            lyrics["by_status"],
            {"available": 2, "partial": 1, "translation_only": 1, "missing": 2, "not_applicable": 1, "none": 0, "not_confirmed": 3},
        )
        self.assertEqual(lyrics["distinct_lyric_versions"], 6, "lyr-demo-001-ko 는 두 기간에 공통")
        self.assertNotIn("lyric_versions", lyrics, "실험 절에는 카탈로그 합계를 넣지 않는다")
        workspace = validate_workspace(self.ws)["lyrics"]
        self.assertEqual(workspace["scope"], "workspace_catalogue")
        self.assertEqual(workspace["lyric_versions"], 12)
        self.assertEqual(workspace["by_status"]["available"], 6)
        # 가사 버전 없이 확정하면 none 으로 센다
        import_mappings(self.ws, write_json(self.path("m.json"), mappings_file([mapping_dict(rank=2, state="confirmed", recording_id="rec-demo-002", lyric_version_id=None, reason="가사 미선택 확정", base_revision=1)])))
        lyrics = validate_experiment(self.ws, EXPERIMENTS / "demo_comparison.json")["lyrics"]
        self.assertEqual(lyrics["by_status"]["none"], 1)
        self.assertEqual(lyrics["by_status"]["not_confirmed"], 2)

    def test_structural_errors_raise_input_error(self) -> None:
        with self.assertRaises(InputError) as ctx:
            validate_experiment(self.ws, EXPERIMENTS / "invalid_unknown_label.json")
        codes = {d["field"]: d["code"] for d in ctx.exception.details}
        self.assertEqual(codes["labels"], "unknown_label")
        self.assertEqual(codes["revision"], "wrong_type")
        self.assertEqual(codes["snapshot_ids"], "duplicate_items")

    def test_missing_snapshot_reference(self) -> None:
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "ghost-r1"]))
        self.assertFalse(report["ok"])
        self.assertEqual(error_codes(report), ["snapshot_not_found"])
        self.assertEqual(report["errors"][0]["snapshot_id"], "ghost-r1")

    def test_incomplete_snapshot_rejected(self) -> None:
        import_chart(self.ws, CHARTS / "period_c_incomplete.csv", BATCHES / "period_c.json")
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "demo-period-c-r1"]))
        self.assertIn("snapshot_incomplete", error_codes(report))
        self.assertEqual(report["errors"][0]["missing_ranks"], [3])
        import_chart(self.ws, CHARTS / "period_c_corrected.csv", BATCHES / "period_c.json", revision=2)
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "demo-period-c-r1"]))
        self.assertIn("snapshot_incomplete", error_codes(report))
        self.assertIn("snapshot_superseded", warning_codes(report))
        # 2020년(366일) vs 2015년(365일): 윤년 하루 차이도 comparability_note 없이는 차단된다
        strict = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "demo-period-c-r2"]))
        self.assertEqual(error_codes(strict), ["period_length_mismatch"])
        fixed = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "demo-period-c-r2"], comparability_note="윤년 하루 차이 검토(가상)"))
        self.assertTrue(fixed["ok"], fixed["errors"])

    def test_top_n_mismatch(self) -> None:
        report = validate_experiment(self.ws, self.exp(top_n=3))
        self.assertEqual(error_codes(report), ["top_n_mismatch", "top_n_mismatch"])

    def test_data_mode_mismatch(self) -> None:
        report = validate_experiment(self.ws, self.exp(data_mode="real"))
        self.assertIn("data_mode_mismatch", error_codes(report))

    def test_same_period_rejected(self) -> None:
        # 같은 기간의 두 revision 을 비교하려는 경우
        import_chart(self.ws, CHARTS / "period_c_incomplete.csv", BATCHES / "period_c.json")
        import_chart(self.ws, CHARTS / "period_c_corrected.csv", BATCHES / "period_c.json", revision=2)
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-c-r1", "demo-period-c-r2"]))
        self.assertIn("same_period", error_codes(report))

    def test_chart_mismatch(self) -> None:
        other = write_json(self.path("other.json"), batch_dict(chart_id="other-chart", snapshot_key="other", period_start="2025-01-01", period_end="2025-12-31", display_year=2025))
        import_chart(self.ws, write_text(self.path("o.csv"), VALID_CSV), other)
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "other-r1"]))
        self.assertIn("chart_mismatch", error_codes(report))

    def test_methodology_mismatch_blocked_unless_note(self) -> None:
        changed = write_json(self.path("m.json"), batch_dict(snapshot_key="method-b", period_start="2025-01-01", period_end="2025-12-31", display_year=2025, methodology_version="synthetic-v2"))
        with self.assertRaises(InputError):
            import_chart(self.ws, write_text(self.path("m.csv"), VALID_CSV), changed)  # 같은 기간 → revision 필요
        import_chart(self.ws, write_text(self.path("m.csv"), VALID_CSV), changed, revision=2)
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "method-b-r2"]))
        self.assertIn("methodology_mismatch", error_codes(report))
        noted = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "method-b-r2"], comparability_note="산정 방식 변경을 검토함(가상)"))
        self.assertTrue(noted["ok"], noted["errors"])
        self.assertIn("methodology_mismatch", warning_codes(noted))

    def test_period_length_mismatch_blocked_unless_note(self) -> None:
        short = write_json(self.path("s.json"), batch_dict(snapshot_key="short", period_start="2026-01-01", period_end="2026-01-31", display_year=2026))
        import_chart(self.ws, write_text(self.path("s.csv"), VALID_CSV), short)
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "short-r1"]))
        self.assertIn("period_length_mismatch", error_codes(report))
        noted = validate_experiment(self.ws, self.exp(snapshot_ids=["demo-period-a-r1", "short-r1"], comparability_note="기간 길이 차이 검토(가상)"))
        self.assertTrue(noted["ok"])

    def _import_period(self, key: str, start: str, end: str, *, chart_id: str = "weekly-chart", periodicity: str = "weekly") -> None:
        batch = write_json(
            self.path(f"{key}.json"),
            batch_dict(chart_id=chart_id, snapshot_key=key, period_start=start, period_end=end, display_year=int(start[:4]),
                       chart={"name": "주간(가상)", "market": "synthetic", "metric": "synthetic_score", "periodicity": periodicity}),
        )
        import_chart(self.ws, write_text(self.path(f"{key}.csv"), VALID_CSV), batch)

    def test_six_vs_seven_day_weekly_periods_blocked_without_note(self) -> None:
        self._import_period("w1", "2025-01-01", "2025-01-06")  # 6일
        self._import_period("w2", "2025-01-08", "2025-01-14")  # 7일
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["w1-r1", "w2-r1"]))
        self.assertFalse(report["ok"])
        self.assertIn("period_length_mismatch", error_codes(report))
        self.assertEqual(report["comparability"]["period_length_days"], [6, 7])
        noted = validate_experiment(self.ws, self.exp(snapshot_ids=["w1-r1", "w2-r1"], comparability_note="첫 주는 6일 집계(가상 검토 메모)"))
        self.assertTrue(noted["ok"], noted["errors"])
        self.assertIn("period_length_mismatch", warning_codes(noted))
        self.assertEqual(noted["warnings"][0]["comparability_note"], "첫 주는 6일 집계(가상 검토 메모)")

    def test_leap_year_annual_periods_blocked_without_note(self) -> None:
        self._import_period("y2024", "2024-01-01", "2024-12-31", chart_id="annual-chart", periodicity="yearly")  # 366일
        self._import_period("y2025", "2025-01-01", "2025-12-31", chart_id="annual-chart", periodicity="yearly")  # 365일
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["y2024-r1", "y2025-r1"]))
        self.assertIn("period_length_mismatch", error_codes(report))
        self.assertEqual(report["comparability"]["period_length_days"], [366, 365])
        noted = validate_experiment(self.ws, self.exp(snapshot_ids=["y2024-r1", "y2025-r1"], comparability_note="윤년 하루 차이 검토(가상)"))
        self.assertTrue(noted["ok"], noted["errors"])

    def test_equal_length_periods_pass_without_note(self) -> None:
        self._import_period("w1", "2025-01-01", "2025-01-07")
        self._import_period("w2", "2025-01-08", "2025-01-14")
        report = validate_experiment(self.ws, self.exp(snapshot_ids=["w1-r1", "w2-r1"]))
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["comparability"]["period_length_days"], [7, 7])

    def test_source_without_analyze_grant_blocks_experiment(self) -> None:
        register_source(self.ws, SourceSpec(source_id="real-x", name="실제 예시", kind="chart", data_mode="real", status="permitted", allowed_operations=("import", "store"), checked_on="2026-09-26", evidence="테스트용 가짜 근거"))
        for key, year in (("ra", 2015), ("rb", 2025)):
            batch = write_json(self.path(f"{key}.json"), batch_dict(source_id="real-x", chart_id="real-chart", snapshot_key=key, data_mode="real", period_start=f"{year}-01-01", period_end=f"{year}-12-31", display_year=year))
            import_chart(self.ws, write_text(self.path(f"{key}.csv"), VALID_CSV), batch)
        report = validate_experiment(self.ws, self.exp(data_mode="real", snapshot_ids=["ra-r1", "rb-r1"]))
        self.assertEqual(error_codes(report), ["source_not_permitted", "source_not_permitted"])
        self.assertIn("analyze", report["errors"][0]["message"] + str(report["errors"][0]["reasons"]))


if __name__ == "__main__":
    unittest.main()
