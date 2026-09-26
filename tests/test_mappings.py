"""D05: 차트 항목 매핑 개정 이력 - 계약, 동시성, 원자성, 조회."""

from __future__ import annotations

import unittest

from chart_emotion.application.mappings import import_mappings, show_mappings
from chart_emotion.application.recordings import import_recordings
from chart_emotion.domain.mappings import parse_mappings_file
from chart_emotion.errors import InputError

from helpers import INVALID, MAPPINGS, WorkspaceCase, mapping_dict, mappings_file, recording_dict, recordings_file, write_json


def error_fields(exc: InputError) -> dict[str, str]:
    return {item["field"]: item["code"] for item in exc.details if "field" in item}


class MappingsFileParseTest(unittest.TestCase):
    def test_valid_and_reviewer_override(self) -> None:
        parsed = parse_mappings_file(mappings_file([mapping_dict(), mapping_dict(rank=2, reviewer_id="reviewer-2", state="unresolved", recording_id=None, lyric_version_id=None)]))
        self.assertEqual(parsed.mappings[0].reviewer_id, "reviewer-1")
        self.assertEqual(parsed.mappings[1].reviewer_id, "reviewer-2")
        self.assertEqual(parsed.mappings[1].content()["recording_id"], None)

    def test_state_rules(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(state="confirmed", recording_id=None, lyric_version_id=None)]))
        self.assertEqual(error_fields(ctx.exception)["mappings[0].recording_id"], "missing_field")
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(state="candidate", recording_id=None, lyric_version_id=None)]))
        self.assertEqual(error_fields(ctx.exception)["mappings[0].recording_id"], "missing_field")
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(state="unresolved")]))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["mappings[0].recording_id"], "state_conflict")
        self.assertEqual(fields["mappings[0].lyric_version_id"], "state_conflict")
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(state="matched")]))
        self.assertEqual(error_fields(ctx.exception)["mappings[0].state"], "invalid_enum")

    def test_types_and_ranges(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(rank=True, base_revision="0", reason="")]))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["mappings[0].rank"], "wrong_type")
        self.assertEqual(fields["mappings[0].base_revision"], "wrong_type")
        self.assertEqual(fields["mappings[0].reason"], "empty_value")
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(rank=0, base_revision=-1)]))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["mappings[0].rank"], "out_of_range")
        self.assertEqual(fields["mappings[0].base_revision"], "out_of_range")
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(base_revision=10**400)]))
        self.assertEqual(error_fields(ctx.exception)["mappings[0].base_revision"], "out_of_range")
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(extra=1)], reviewer_id=None))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["mappings[0].extra"], "unknown_field")
        self.assertEqual(fields["reviewer_id"], "missing_field")

    def test_duplicate_entry_in_file_and_empty_list(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([mapping_dict(), mapping_dict(state="unresolved", recording_id=None, lyric_version_id=None)]))
        self.assertEqual(error_fields(ctx.exception)["mappings[1]"], "duplicate_entry_in_file")
        with self.assertRaises(InputError) as ctx:
            parse_mappings_file(mappings_file([]))
        self.assertEqual(error_fields(ctx.exception)["mappings"], "too_few_items")


class MappingCase(WorkspaceCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_demo()
        self.import_demo_recordings()

    def mfile(self, name: str, mappings, **overrides):
        return write_json(self.path(name), mappings_file(mappings, **overrides))

    def states(self, snapshot_id: str) -> list[tuple]:
        return self.query(
            "SELECT rank, state, recording_id, revision FROM ("
            "  SELECT rank, state, recording_id, revision, ROW_NUMBER() OVER (PARTITION BY rank ORDER BY revision DESC) AS rn"
            "  FROM entry_mappings WHERE snapshot_id = ?) WHERE rn = 1 ORDER BY rank",
            (snapshot_id,),
        )


class AcceptanceTest(MappingCase):
    def test_demo_periods_map_10_entries_with_shared_recording(self) -> None:
        self.import_demo_mappings()
        self.assertEqual(self.table_counts()["entry_mappings"], 10)
        a = self.states("demo-period-a-r1")
        b = self.states("demo-period-b-r1")
        self.assertEqual([s[1] for s in a], ["confirmed", "candidate", "confirmed", "confirmed", "unresolved"])
        self.assertEqual([s[1] for s in b], ["confirmed", "confirmed", "confirmed", "candidate", "confirmed"])
        # 같은 녹음이 두 기간에 매핑됨
        self.assertEqual(a[0][2], "rec-demo-001")
        self.assertEqual(b[1][2], "rec-demo-001")
        # 후보는 확정이 아니다
        report = show_mappings(self.ws)
        self.assertEqual(report["matching"]["states"], {"confirmed": 7, "candidate": 2, "unresolved": 1, "unmapped": 0})
        self.assertEqual(report["matching"]["confirmed_recordings_in_all_snapshots"], ["rec-demo-001"])
        self.assertEqual(report["matching"]["status"], "in_progress")

    def test_full_demo_sequence_corrections_conflict_reconfirm(self) -> None:
        self.import_demo_mappings()
        out = import_mappings(self.ws, MAPPINGS / "corrections.json")
        self.assertEqual([(m["rank"], m["revision"], m["state"], m["previous_state"]) for m in out["appended"]],
                         [(2, 2, "confirmed", "candidate"), (5, 2, "confirmed", "unresolved"), (4, 2, "unresolved", "candidate")])
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, MAPPINGS / "stale_conflict.json")
        self.assertEqual(ctx.exception.code, "stale_base_revision")
        detail = ctx.exception.details[0]
        self.assertEqual((detail["requested_base_revision"], detail["current_revision"], detail["current_state"]), (1, 2, "confirmed"))
        # 첫 항목(rank 1)도 반영되지 않았다: rank1 은 여전히 revision 1
        self.assertEqual(self.states("demo-period-a-r1")[0][3], 1)
        out = import_mappings(self.ws, MAPPINGS / "reconfirm.json")
        self.assertEqual(out["appended"][0]["mapping_id"], "demo-period-b-r1.rank4.r3")
        history = show_mappings(self.ws, snapshot_id="demo-period-b-r1", history=True)["snapshots"][0]["entries"][3]["history"]
        self.assertEqual([(h["revision"], h["state"], h["recording_id"], h["reviewer_id"]) for h in history],
                         [(1, "candidate", "rec-demo-002", "reviewer-1"), (2, "unresolved", None, "reviewer-1"), (3, "confirmed", "rec-demo-002", "reviewer-2")])
        self.assertEqual([h["base_revision"] for h in history], [0, 1, 2])
        final = show_mappings(self.ws)["matching"]
        self.assertEqual(final["status"], "complete")
        self.assertEqual(final["unique_confirmed_recordings"], 8)
        self.assertEqual(final["confirmed_recordings_in_all_snapshots"], ["rec-demo-001", "rec-demo-002"])

    def test_same_title_variants_stay_distinct(self) -> None:
        from chart_emotion.application.import_chart import import_chart

        from helpers import VERSIONS

        import_chart(self.ws, VERSIONS / "same_title_versions.csv", VERSIONS / "batch_versions.json")
        import_mappings(self.ws, MAPPINGS / "versions_chart.json")
        rows = self.states("demo-versions-r1")
        self.assertEqual([r[2] for r in rows], ["rec-demo-001", "rec-demo-001-rerecord-2025", "rec-demo-009-cover"])
        entries = show_mappings(self.ws, snapshot_id="demo-versions-r1")["snapshots"][0]["entries"]
        self.assertEqual({e["title"] for e in entries}, {"가상곡 가"})
        self.assertEqual([e["current"]["recording"]["version_kind"] for e in entries], ["original", "rerecording", "cover"])


class ConcurrencyAndHistoryTest(MappingCase):
    def test_exact_replay_is_noop(self) -> None:
        import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        before = self.table_counts()
        out = import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        self.assertEqual(out["appended"], [])
        self.assertEqual(len(out["unchanged"]), 5)
        self.assertEqual(self.table_counts(), before)
        # 같은 내용을 최신 revision 기준으로 다시 내도 새 revision 을 만들지 않는다
        same = self.mfile("same.json", [mapping_dict(reason="provider_track_id demo-001 과 표기 확인 (가상)", base_revision=1)])
        out = import_mappings(self.ws, same)
        self.assertEqual(out["unchanged"][0]["revision"], 1)
        self.assertEqual(self.table_counts(), before)

    def test_replay_after_intervening_revision_is_stale_not_overwrite(self) -> None:
        import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        import_mappings(self.ws, self.mfile("w.json", [mapping_dict(state="unresolved", recording_id=None, lyric_version_id=None, reason="철회", base_revision=1)]))
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, MAPPINGS / "period_a_initial.json")  # rank1 판정이 r1 과 같지만 r2(철회)가 끼어 있음
        self.assertEqual(ctx.exception.code, "stale_base_revision")
        self.assertEqual(ctx.exception.details[0]["current_state"], "unresolved")
        self.assertEqual(self.table_counts(), before)
        self.assertEqual(self.states("demo-period-a-r1")[0][1], "unresolved")

    def test_stale_edit_fails_atomically(self) -> None:
        import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        before = self.table_counts()
        batch = self.mfile("b.json", [
            mapping_dict(rank=3, state="unresolved", recording_id=None, lyric_version_id=None, reason="철회", base_revision=1),
            mapping_dict(rank=2, state="confirmed", recording_id="rec-demo-002", lyric_version_id="lyr-demo-002-ko", reason="확정", base_revision=0),
        ])
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, batch)
        self.assertEqual(ctx.exception.code, "stale_base_revision")
        self.assertEqual(ctx.exception.details[0]["field"], "mappings[1].base_revision")
        self.assertEqual(self.table_counts(), before)
        self.assertEqual(self.states("demo-period-a-r1")[2][1], "confirmed", "앞 항목의 철회도 반영되지 않았다")

    def test_first_mapping_requires_base_zero(self) -> None:
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, self.mfile("b.json", [mapping_dict(base_revision=1)]))
        self.assertEqual(ctx.exception.details[0]["current_state"], "unmapped")
        self.assertEqual(self.table_counts()["entry_mappings"], 0)

    def test_history_rows_are_immutable_and_ids_stable(self) -> None:
        import_mappings(self.ws, self.mfile("a.json", [mapping_dict()]))
        first = self.query("SELECT mapping_id, state, recording_id, lyric_version_id, reviewer_id, reason FROM entry_mappings")[0]
        import_mappings(self.ws, self.mfile("b.json", [mapping_dict(state="unresolved", recording_id=None, lyric_version_id=None, reason="철회", base_revision=1)]))
        import_mappings(self.ws, self.mfile("c.json", [mapping_dict(reason="재확정", base_revision=2)], reviewer_id="reviewer-2"))
        rows = self.query("SELECT mapping_id, state, recording_id, lyric_version_id, reviewer_id, reason FROM entry_mappings ORDER BY revision")
        self.assertEqual(rows[0], first)
        self.assertEqual([r[0] for r in rows], ["demo-period-a-r1.rank1.r1", "demo-period-a-r1.rank1.r2", "demo-period-a-r1.rank1.r3"])
        self.assertEqual([r[4] for r in rows], ["reviewer-1", "reviewer-1", "reviewer-2"])
        # 녹음 정의는 매핑이 가리키는 동안 바뀌지 않는다
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, write_json(self.path("r.json"), recordings_file([recording_dict(recording_id="rec-demo-001", title="바뀐 제목", artist="가상가수 A", provider_track_ids=["demo-001"], notes="가상 자료. 두 기간에 모두 등장")])))
        self.assertEqual(ctx.exception.code, "recording_immutable")


class MappingReferenceTest(MappingCase):
    def test_entry_and_recording_must_exist(self) -> None:
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, self.mfile("a.json", [mapping_dict(rank=9)]))
        self.assertEqual(ctx.exception.code, "entry_not_found")
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, self.mfile("b.json", [mapping_dict(snapshot_id="ghost-r1")]))
        self.assertEqual(ctx.exception.code, "entry_not_found")
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, self.mfile("c.json", [mapping_dict(recording_id="rec-ghost", lyric_version_id=None)]))
        self.assertEqual(ctx.exception.code, "recording_not_found")
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, self.mfile("d.json", [mapping_dict(lyric_version_id="lyr-ghost")]))
        self.assertEqual(ctx.exception.code, "lyric_version_not_found")
        self.assertEqual(self.table_counts()["entry_mappings"], 0)

    def test_lyric_version_must_belong_to_selected_recording(self) -> None:
        import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, INVALID / "mapping_lyric_mismatch.json")
        self.assertEqual(ctx.exception.code, "lyric_recording_mismatch")
        self.assertEqual(ctx.exception.details[0]["lyric_recording_id"], "rec-demo-002")
        self.assertEqual(self.table_counts(), before)

    def test_data_mode_mismatch_between_recording_and_snapshot(self) -> None:
        import_recordings(self.ws, write_json(self.path("r.json"), recordings_file([recording_dict(recording_id="rec-real", data_mode="real")])))
        with self.assertRaises(InputError) as ctx:
            import_mappings(self.ws, self.mfile("m.json", [mapping_dict(recording_id="rec-real", lyric_version_id=None)]))
        self.assertEqual(ctx.exception.code, "data_mode_mismatch")
        self.assertEqual(self.table_counts()["entry_mappings"], 0)

    def test_candidate_with_exact_provider_id_is_not_confirmed(self) -> None:
        import_mappings(self.ws, self.mfile("m.json", [mapping_dict(state="candidate", lyric_version_id=None, reason="provider_track_id 완전 일치")]))
        entry = show_mappings(self.ws, snapshot_id="demo-period-a-r1")["snapshots"][0]["entries"][0]
        self.assertEqual(entry["state"], "candidate")
        self.assertEqual(entry["provider_track_id"], "demo-001")
        self.assertEqual(show_mappings(self.ws)["matching"]["states"]["confirmed"], 0)

    def test_show_mappings_unknown_snapshot_and_unmapped_state(self) -> None:
        with self.assertRaises(InputError) as ctx:
            show_mappings(self.ws, snapshot_id="ghost")
        self.assertEqual(ctx.exception.code, "snapshot_not_found")
        report = show_mappings(self.ws, snapshot_id="demo-period-a-r1")
        self.assertEqual({e["state"] for e in report["snapshots"][0]["entries"]}, {"unmapped"})
        self.assertEqual(report["matching"]["status"], "not_started")
        self.assertIsNone(report["snapshots"][0]["entries"][0]["current"])


if __name__ == "__main__":
    unittest.main()
