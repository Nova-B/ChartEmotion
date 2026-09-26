"""D05: 녹음·가사 버전 등록 계약과 불변 등록."""

from __future__ import annotations

import json
import unittest

from chart_emotion.application.recordings import import_recordings
from chart_emotion.domain.recordings import parse_recordings_file
from chart_emotion.errors import InputError

from helpers import INVALID, RECORDINGS, WorkspaceCase, lyric_dict, recording_dict, recordings_file, write_json, write_text


def error_fields(exc: InputError) -> dict[str, str]:
    return {item["field"]: item["code"] for item in exc.details if "field" in item}


class RecordingsFileParseTest(unittest.TestCase):
    def test_valid_file(self) -> None:
        parsed = parse_recordings_file(recordings_file([recording_dict()], [lyric_dict()]))
        self.assertEqual(parsed.registered_by, "reviewer-1")
        self.assertEqual(parsed.recordings[0].release_date, "2015-03-14")
        self.assertEqual(parsed.lyric_versions[0].status, "available")
        self.assertEqual(len(parsed.recordings[0].content_sha256()), 64)

    def test_release_precision_matrix(self) -> None:
        ok_cases = [
            ("unknown", None), ("year", "2014"), ("month", "2015-06"), ("day", "2015-03-14"),
            ("year", "0001"), ("year", "9999"), ("month", "0001-01"), ("month", "9999-12"),
            ("day", "2024-02-29"),  # 윤년 2월 29일
        ]
        for precision, date_value in ok_cases:
            with self.subTest(precision=precision):
                parsed = parse_recordings_file(recordings_file([recording_dict(release_precision=precision, release_date=date_value)]))
                self.assertEqual(parsed.recordings[0].release_date, date_value)
        bad_cases = [
            ("unknown", "2015", "precision_conflict"),
            ("year", "2015-03-14", "invalid_date"),
            ("year", None, "missing_field"),
            ("month", "2015-13", "invalid_date"),
            ("month", "2015", "invalid_date"),
            ("day", "2015-02-30", "invalid_date"),
            ("day", "2015-03", "invalid_date"),
            ("day", "2023-02-29", "invalid_date"),  # 평년 2월 29일
            ("year", "0000", "invalid_date"),
            ("month", "0000-01", "invalid_date"),
            ("day", "0000-01-01", "invalid_date"),
            ("year", "٢٠٢٥", "invalid_date"),  # 아라비아-인도 숫자 ٢٠٢٥
            ("month", "٢٠٢٥-01", "invalid_date"),
            ("day", "٢٠٢٥-01-01", "invalid_date"),
            ("year", "２０２５", "invalid_date"),  # 전각 숫자
            ("year", " 2025", "invalid_date"),
            ("year", "+2025", "invalid_date"),
            ("decade", "2010", "invalid_enum"),
        ]
        for precision, date_value, code in bad_cases:
            with self.subTest(precision=precision, date=date_value):
                with self.assertRaises(InputError) as ctx:
                    parse_recordings_file(recordings_file([recording_dict(release_precision=precision, release_date=date_value)]))
                fields = error_fields(ctx.exception)
                target = "recordings[0].release_precision" if code == "invalid_enum" else "recordings[0].release_date"
                self.assertEqual(fields[target], code)

    def test_strict_types_and_unknown_fields(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file([recording_dict(vocal_type=True, release_date=2015, lyrics_text="본문", provider_track_ids="demo-1")]))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["recordings[0].vocal_type"], "wrong_type")
        self.assertEqual(fields["recordings[0].release_date"], "wrong_type")
        self.assertEqual(fields["recordings[0].lyrics_text"], "unknown_field")
        self.assertEqual(fields["recordings[0].provider_track_ids"], "wrong_type")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file({"registered_by": "r", "recordings": [recording_dict()], "extra": 1, "schema": "chart-emotion.recordings.v9"})
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["extra"], "unknown_field")
        self.assertEqual(fields["schema"], "unsupported_schema")

    def test_missing_registered_by_and_empty_file(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file({"recordings": []})
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["registered_by"], "missing_field")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file({"registered_by": "r"})
        self.assertEqual(error_fields(ctx.exception)["recordings"], "missing_field")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file({"registered_by": "r", "recordings": [], "lyric_versions": []})
        self.assertEqual(error_fields(ctx.exception)["recordings"], "too_few_items")

    def test_version_rules(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file([recording_dict(version_kind="cover", original_recording_id="rec-test-001")]))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["recordings[0].original_recording_id"], "self_reference")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file([recording_dict(version_kind="original", original_recording_id="rec-x")]))
        self.assertEqual(error_fields(ctx.exception)["recordings[0].original_recording_id"], "version_conflict")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file([recording_dict(), recording_dict(title="다른 제목")]))
        self.assertEqual(error_fields(ctx.exception)["recordings[1].recording_id"], "duplicate_in_file")

    def test_lyric_status_rules(self) -> None:
        # available/partial/translation_only 는 language + source_id 필요
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file(lyric_versions=[lyric_dict(status="partial", language=None, source_id=None)]))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["lyric_versions[0].language"], "missing_field")
        self.assertEqual(fields["lyric_versions[0].source_id"], "missing_field")
        # missing 은 출처·언어 없이 등록 가능, reference 는 불가
        parsed = parse_recordings_file(recordings_file(lyric_versions=[lyric_dict(status="missing", language=None, source_id=None, reference=None)]))
        self.assertEqual(parsed.lyric_versions[0].status, "missing")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file(lyric_versions=[lyric_dict(status="missing", language=None, source_id=None)]))
        self.assertEqual(error_fields(ctx.exception)["lyric_versions[0].reference"], "status_conflict")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file(lyric_versions=[lyric_dict(status="not_applicable", source_id=None, reference=None)]))
        self.assertEqual(error_fields(ctx.exception)["lyric_versions[0].language"], "status_conflict")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file(lyric_versions=[lyric_dict(language="Korean")]))
        self.assertEqual(error_fields(ctx.exception)["lyric_versions[0].language"], "invalid_language")
        with self.assertRaises(InputError) as ctx:
            parse_recordings_file(recordings_file(lyric_versions=[lyric_dict(status="obtained")]))
        self.assertEqual(error_fields(ctx.exception)["lyric_versions[0].status"], "invalid_enum")

    def test_no_raw_lyrics_field_exists(self) -> None:
        for field in ("text", "lyrics", "body", "content"):
            with self.subTest(field=field):
                with self.assertRaises(InputError) as ctx:
                    parse_recordings_file(recordings_file(lyric_versions=[lyric_dict(**{field: "가사 본문"})]))
                self.assertEqual(error_fields(ctx.exception)[f"lyric_versions[0].{field}"], "unknown_field")

    def test_invalid_fixture(self) -> None:
        from chart_emotion.domain.recordings import load_recordings_file

        with self.assertRaises(InputError) as ctx:
            load_recordings_file(INVALID / "recordings_invalid.json")
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["recordings[0].release_date"], "invalid_date")
        self.assertEqual(fields["recordings[1].release_date"], "precision_conflict")
        self.assertEqual(fields["recordings[2].vocal_type"], "wrong_type")
        self.assertEqual(fields["recordings[2].lyrics_text"], "unknown_field")


class ImportRecordingsTest(WorkspaceCase):
    def file(self, name: str, recordings=None, lyric_versions=None, **overrides):
        return write_json(self.path(name), recordings_file(recordings, lyric_versions, **overrides))

    def test_demo_fixture_registers_and_replays(self) -> None:
        self.register_lyrics_source()
        first = import_recordings(self.ws, RECORDINGS / "recordings.json")
        self.assertEqual(len(first["recordings"]["registered"]), 10)
        self.assertEqual(len(first["lyric_versions"]["registered"]), 10)
        again = import_recordings(self.ws, RECORDINGS / "recordings.json")
        self.assertEqual(again["recordings"]["registered"], [])
        self.assertEqual(len(again["recordings"]["unchanged"]), 10)
        self.assertEqual(len(again["lyric_versions"]["unchanged"]), 10)
        self.assertEqual(self.table_counts()["recordings"], 10)
        rows = self.query("SELECT recording_id, release_precision, release_date, vocal_type FROM recordings WHERE recording_id IN ('rec-demo-003','rec-demo-004','rec-demo-007') ORDER BY recording_id")
        self.assertEqual(rows, [("rec-demo-003", "year", "2014", "lyrical"), ("rec-demo-004", "unknown", None, "lyrical"), ("rec-demo-007", "month", "2025-04", "instrumental")])
        same_title = self.query("SELECT COUNT(*) FROM recordings WHERE title = '가상곡 가'")
        self.assertEqual(same_title[0][0], 3, "같은 제목 3개 녹음(원곡·재녹음·커버)이 별도로 남는다")

    def test_demo_fixture_needs_lyrics_source_and_is_atomic(self) -> None:
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, RECORDINGS / "recordings.json")
        self.assertEqual(ctx.exception.code, "source_not_registered")
        self.assertEqual(self.table_counts(), before, "가사 버전 하나가 실패하면 녹음도 남지 않는다")

    def test_changed_content_under_same_id_is_rejected(self) -> None:
        import_recordings(self.ws, self.file("a.json", [recording_dict()]))
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("b.json", [recording_dict(release_date="2015-03-15")]))
        self.assertEqual(ctx.exception.code, "recording_immutable")
        self.assertEqual(ctx.exception.details[0]["differences"][0]["field"], "release_date")
        self.assertEqual(self.table_counts(), before)
        # 메모만 달라도 내용 변경으로 본다(새 ID 로 등록)
        with self.assertRaises(InputError):
            import_recordings(self.ws, self.file("c.json", [recording_dict(notes="메모 추가")]))
        out = import_recordings(self.ws, self.file("d.json", [recording_dict(recording_id="rec-test-001-v2", notes="메모 추가")]))
        self.assertEqual(out["recordings"]["registered"], ["rec-test-001-v2"])

    def test_lyric_version_immutable_and_recording_required(self) -> None:
        self.register_lyrics_source()
        import_recordings(self.ws, self.file("a.json", [recording_dict()], [lyric_dict()]))
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("b.json", lyric_versions=[lyric_dict(status="partial")]))
        self.assertEqual(ctx.exception.code, "lyric_version_immutable")
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("c.json", lyric_versions=[lyric_dict(lyric_version_id="lyr-x", recording_id="rec-ghost")]))
        self.assertEqual(ctx.exception.code, "recording_not_found")

    def test_original_reference_must_exist_and_order_matters(self) -> None:
        cover = recording_dict(recording_id="rec-cover", version_kind="cover", original_recording_id="rec-test-001")
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("a.json", [cover, recording_dict()]))
        self.assertEqual(ctx.exception.code, "recording_not_found")
        self.assertEqual(self.table_counts(), before)
        out = import_recordings(self.ws, self.file("b.json", [recording_dict(), cover]))
        self.assertEqual(out["recordings"]["registered"], ["rec-test-001", "rec-cover"])

    def test_instrumental_lyric_status_rules(self) -> None:
        self.register_lyrics_source()
        inst = recording_dict(recording_id="rec-inst", vocal_type="instrumental")
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("a.json", [inst], [lyric_dict(lyric_version_id="lyr-inst", recording_id="rec-inst")]))
        self.assertEqual(ctx.exception.code, "lyric_status_conflict")
        self.assertEqual(self.table_counts()["recordings"], 0)
        out = import_recordings(self.ws, self.file("b.json", [inst], [lyric_dict(lyric_version_id="lyr-inst", recording_id="rec-inst", status="not_applicable", language=None, source_id=None, reference=None)]))
        self.assertEqual(out["lyric_versions"]["registered"], ["lyr-inst"])
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("c.json", [recording_dict()], [lyric_dict(status="not_applicable", language=None, source_id=None, reference=None)]))
        self.assertEqual(ctx.exception.code, "lyric_status_conflict")

    def test_missing_lyrics_needs_no_source_access(self) -> None:
        # 가사 출처를 전혀 등록하지 않아도 '미확보' 메타데이터는 등록된다
        out = import_recordings(self.ws, self.file("a.json", [recording_dict()], [lyric_dict(status="missing", language=None, source_id=None, reference=None)]))
        self.assertEqual(out["lyric_versions"]["registered"], ["lyr-test-001"])
        self.assertEqual(self.query("SELECT status FROM lyric_versions")[0][0], "missing")

    def test_lyrics_source_gates(self) -> None:
        # 실제(pending) 출처로 available 가사 → 차단
        self.register_lyrics_source(data_mode="real", source_id="real-lyrics")
        real_rec = recording_dict(recording_id="rec-real", data_mode="real")
        before = self.table_counts()
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("a.json", [real_rec], [lyric_dict(lyric_version_id="lyr-real", recording_id="rec-real", source_id="real-lyrics")]))
        self.assertEqual(ctx.exception.code, "source_not_permitted")
        self.assertIn("source_status_not_permitted", [d.get("code") for d in ctx.exception.details])
        self.assertEqual(self.table_counts(), before)
        # 같은 출처로 missing 은 허용(접근 없음)
        out = import_recordings(self.ws, self.file("b.json", [real_rec], [lyric_dict(lyric_version_id="lyr-real-missing", recording_id="rec-real", status="missing", language=None, source_id="real-lyrics", reference=None)]))
        self.assertEqual(out["lyric_versions"]["registered"], ["lyr-real-missing"])
        # synthetic 녹음에 real 출처 → data_mode 불일치
        self.register_lyrics_source()
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("c.json", [recording_dict()], [lyric_dict(source_id="real-lyrics", status="missing", language=None, reference=None)]))
        self.assertEqual(ctx.exception.code, "data_mode_mismatch")
        # 차트 종류 출처를 가사 출처로 → 종류 불일치
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("d.json", [recording_dict()], [lyric_dict(source_id="synthetic-demo")]))
        self.assertIn("source_kind_mismatch", [d.get("code") for d in ctx.exception.details])

    def test_recording_metadata_source_gate(self) -> None:
        out = import_recordings(self.ws, self.file("a.json", [recording_dict(source_id="synthetic-demo")]))  # chart 출처도 메타데이터 출처로 허용
        self.assertEqual(out["recordings"]["registered"], ["rec-test-001"])
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("b.json", [recording_dict(recording_id="rec-2", source_id="ghost")]))
        self.assertEqual(ctx.exception.code, "source_not_registered")
        self.register_lyrics_source(data_mode="real", source_id="real-meta", kind="metadata")
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("c.json", [recording_dict(recording_id="rec-3", data_mode="real", source_id="real-meta")]))
        self.assertEqual(ctx.exception.code, "source_not_permitted")
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("d.json", [recording_dict(recording_id="rec-4", data_mode="real", source_id="synthetic-demo")]))
        self.assertIn("real_requires_real_source", [d.get("code") for d in ctx.exception.details])

    def test_original_data_mode_mismatch(self) -> None:
        import_recordings(self.ws, self.file("a.json", [recording_dict()]))
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, self.file("b.json", [recording_dict(recording_id="rec-real-cover", data_mode="real", version_kind="cover", original_recording_id="rec-test-001")]))
        self.assertEqual(ctx.exception.code, "data_mode_mismatch")

    def test_non_canonical_release_dates_via_cli_write_nothing(self) -> None:
        from helpers import run_cli

        before = self.table_counts()
        for precision, value in (("year", "0000"), ("month", "0000-01"), ("year", "٢٠٢٥"), ("day", "2023-02-29")):
            with self.subTest(precision=precision, value=value):
                path = self.file(f"bad-{precision}.json", [recording_dict(recording_id="rec-good-first"), recording_dict(recording_id="rec-bad", release_precision=precision, release_date=value)])
                code, out, err = run_cli(["import-recordings", str(path), "--workspace", str(self.ws)])
                self.assertEqual(code, 2, err)
                payload = json.loads(err)
                self.assertEqual(payload["error"]["code"], "recordings_file_invalid")
                self.assertEqual({d["field"]: d["code"] for d in payload["error"]["details"]}["recordings[1].release_date"], "invalid_date")
                self.assertEqual(self.table_counts(), before, "앞 항목이 유효해도 아무것도 남지 않는다")

    def test_json_file_errors_exit_two_semantics(self) -> None:
        bad = write_text(self.path("dup.json"), '{"registered_by": "r", "recordings": [], "registered_by": "x"}')
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, bad)
        self.assertEqual(ctx.exception.code, "json_duplicate_key")
        huge = write_json(self.path("huge.json"), recordings_file([recording_dict(release_precision="year", release_date="9" * 5000)]))
        with self.assertRaises(InputError) as ctx:
            import_recordings(self.ws, huge)
        self.assertEqual(ctx.exception.code, "recordings_file_invalid")


if __name__ == "__main__":
    unittest.main()
