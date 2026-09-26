"""D04: 차트 CSV 검증."""

from __future__ import annotations

import unittest

from chart_emotion.errors import InputError
from chart_emotion.importers.chart_csv import parse_chart_csv

from helpers import CHARTS, INVALID, VALID_CSV, VERSIONS, TempDirCase, write_text


def codes(exc: InputError) -> list[str]:
    return [item["code"] for item in exc.details]


class ChartCsvTest(TempDirCase):
    def parse(self, text: str, *, expected_n: int = 5, bom: bool = False, name: str = "chart.csv"):
        return parse_chart_csv(write_text(self.path(name), text, bom=bom), expected_n=expected_n)

    def test_valid_utf8_without_bom(self) -> None:
        result = self.parse(VALID_CSV)
        self.assertFalse(result.had_bom)
        self.assertTrue(result.is_complete)
        self.assertEqual([e.rank for e in result.entries], [1, 2, 3, 4, 5])
        self.assertEqual(result.entries[2].title, "가상곡 다, 그리고 밤")
        self.assertIsNone(result.entries[4].provider_track_id)
        self.assertEqual(len(result.file_sha256), 64)

    def test_bom_and_no_bom_share_content_hash(self) -> None:
        plain = self.parse(VALID_CSV, name="plain.csv")
        bom = self.parse(VALID_CSV, bom=True, name="bom.csv")
        self.assertTrue(bom.had_bom)
        self.assertEqual(plain.content_sha256, bom.content_sha256)
        self.assertNotEqual(plain.file_sha256, bom.file_sha256)
        self.assertEqual(bom.entries[0].rank, 1)  # BOM 이 rank 열 이름을 오염시키지 않음

    def test_quoted_newline_and_escaped_quotes(self) -> None:
        text = (
            "rank,title,artist,provider_track_id\n"
            '1,"두 줄\n제목",가수,demo-1\n'
            '2,"따옴표 ""안"" 제목","가수, 쉼표",demo-2\n'
        )
        result = self.parse(text, expected_n=2)
        self.assertEqual(result.entries[0].title, "두 줄\n제목")
        self.assertEqual(result.entries[1].title, '따옴표 "안" 제목')
        self.assertEqual(result.entries[1].artist, "가수, 쉼표")

    def test_crlf_and_column_order(self) -> None:
        text = "title,rank,provider_track_id,artist\r\n가상곡,1,,가수\r\n"
        result = self.parse(text, expected_n=1)
        self.assertEqual(result.entries[0].title, "가상곡")
        self.assertEqual(result.entries[0].artist, "가수")

    def test_empty_file(self) -> None:
        with self.assertRaises(InputError) as ctx:
            self.parse("")
        self.assertEqual(ctx.exception.code, "csv_empty")
        with self.assertRaises(InputError) as ctx:
            self.parse("\n\n", bom=True)
        self.assertEqual(ctx.exception.code, "csv_empty")

    def test_header_only(self) -> None:
        with self.assertRaises(InputError) as ctx:
            self.parse("rank,title,artist,provider_track_id\n")
        self.assertEqual(ctx.exception.code, "csv_header_only")

    def test_missing_column(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_chart_csv(INVALID / "missing_column.csv", expected_n=5)
        self.assertEqual(ctx.exception.code, "csv_header_invalid")
        self.assertIn("csv_missing_column", codes(ctx.exception))
        self.assertIn("artist", ctx.exception.details[0]["message"])

    def test_duplicate_and_extra_header(self) -> None:
        with self.assertRaises(InputError) as ctx:
            self.parse("rank,title,artist,provider_track_id,title\n1,a,b,c,d\n")
        self.assertIn("csv_duplicate_header", codes(ctx.exception))
        with self.assertRaises(InputError) as ctx:
            self.parse("rank,title,artist,provider_track_id,score\n1,a,b,c,d\n")
        self.assertIn("csv_unexpected_column", codes(ctx.exception))

    def test_extra_and_missing_cells(self) -> None:
        text = "rank,title,artist,provider_track_id\n1,a,b,c,extra\n2,a,b\n"
        with self.assertRaises(InputError) as ctx:
            self.parse(text)
        self.assertEqual(ctx.exception.code, "csv_invalid")
        self.assertEqual(codes(ctx.exception), ["csv_cell_count", "csv_cell_count"])
        self.assertEqual([d["line"] for d in ctx.exception.details], [2, 3])

    def test_invalid_utf8(self) -> None:
        path = self.path("bad.csv")
        path.write_bytes("rank,title,artist,provider_track_id\n1,".encode("utf-8") + b"\xea\xb0" + b",b,c\n")
        with self.assertRaises(InputError) as ctx:
            parse_chart_csv(path, expected_n=1)
        self.assertEqual(ctx.exception.code, "csv_invalid_utf8")
        self.assertIn("byte_offset", ctx.exception.details[0])
        cp949 = self.path("cp949.csv")
        cp949.write_bytes("rank,title,artist,provider_track_id\n1,가상곡,가수,x\n".encode("cp949"))
        with self.assertRaises(InputError) as ctx:
            parse_chart_csv(cp949, expected_n=1)
        self.assertEqual(ctx.exception.code, "csv_invalid_utf8")

    def test_malformed_unterminated_quote(self) -> None:
        with self.assertRaises(InputError) as ctx:
            self.parse('rank,title,artist,provider_track_id\n1,"열린 따옴표,가수,x\n2,a,b,c\n')
        self.assertEqual(ctx.exception.code, "csv_malformed")

    def test_duplicate_rank_fixture(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_chart_csv(INVALID / "duplicate_rank.csv", expected_n=5)
        self.assertEqual(codes(ctx.exception), ["csv_duplicate_rank"])
        self.assertEqual(ctx.exception.details[0]["line"], 4)

    def test_rank_rules(self) -> None:
        text = (
            "rank,title,artist,provider_track_id\n"
            "0,a,b,\n"
            "6,a,b,\n"
            "1.0,a,b,\n"
            "+2,a,b,\n"
            "-1,a,b,\n"
            "abc,a,b,\n"
            "３,a,b,\n"
        )
        with self.assertRaises(InputError) as ctx:
            self.parse(text)
        self.assertEqual(
            codes(ctx.exception),
            ["csv_rank_out_of_range", "csv_rank_out_of_range", "csv_rank_not_integer", "csv_rank_not_integer",
             "csv_rank_not_integer", "csv_rank_not_integer", "csv_rank_not_integer"],
        )

    def test_huge_rank_digits_are_row_errors(self) -> None:
        huge = "1" * 5000
        text = f"rank,title,artist,provider_track_id\n{huge},가상곡,가수,demo-x\n2,a,b,\n"
        with self.assertRaises(InputError) as ctx:
            self.parse(text)
        self.assertEqual(ctx.exception.code, "csv_invalid")
        self.assertEqual(codes(ctx.exception), ["csv_rank_out_of_range"])
        self.assertEqual(ctx.exception.details[0]["line"], 2)
        self.assertLess(len(ctx.exception.details[0]["message"]), 200)
        # 앞자리 0 은 자릿수 판정에서 제외되어 정상 순위로 읽힌다
        result = self.parse("rank,title,artist,provider_track_id\n0003,a,b,\n", expected_n=5)
        self.assertEqual(result.entries[0].rank, 3)

    def test_thousands_of_leading_zeros_never_reach_int_conversion(self) -> None:
        zeros = "0" * 5000
        # 5000개의 0 → 값 0 → 범위 오류(처리 실패가 아님)
        with self.assertRaises(InputError) as ctx:
            self.parse(f"rank,title,artist,provider_track_id\n{zeros},가상곡,가수,demo-x\n")
        self.assertEqual(ctx.exception.code, "csv_invalid")
        self.assertEqual(codes(ctx.exception), ["csv_rank_out_of_range"])
        self.assertEqual(ctx.exception.exit_code, 2)
        # 5000개의 0 뒤에 1 → 정상 순위 1 로 정규화
        result = self.parse(f"rank,title,artist,provider_track_id\n{zeros}1,가상곡,가수,demo-x\n", expected_n=5)
        self.assertEqual(result.entries[0].rank, 1)
        # 5000개의 0 뒤에 6 (expected_n=5 초과) → 범위 오류
        with self.assertRaises(InputError) as ctx:
            self.parse(f"rank,title,artist,provider_track_id\n{zeros}6,가상곡,가수,demo-x\n")
        self.assertEqual(codes(ctx.exception), ["csv_rank_out_of_range"])

    def test_empty_title_or_artist(self) -> None:
        with self.assertRaises(InputError) as ctx:
            self.parse("rank,title,artist,provider_track_id\n1, ,b,\n2,a,,\n", expected_n=2)
        self.assertEqual(codes(ctx.exception), ["csv_empty_field", "csv_empty_field"])
        self.assertEqual([d["column"] for d in ctx.exception.details], ["title", "artist"])

    def test_missing_ranks_reported_not_rejected(self) -> None:
        result = parse_chart_csv(CHARTS / "period_c_incomplete.csv", expected_n=5)
        self.assertEqual(result.missing_ranks, (3,))
        self.assertFalse(result.is_complete)
        self.assertEqual(len(result.entries), 4)

    def test_blank_lines_skipped(self) -> None:
        result = self.parse("rank,title,artist,provider_track_id\n\n1,a,b,\n\n", expected_n=1)
        self.assertEqual(len(result.entries), 1)

    def test_same_title_rows_are_kept_separately(self) -> None:
        result = parse_chart_csv(VERSIONS / "same_title_versions.csv", expected_n=3)
        self.assertEqual(len(result.entries), 3)
        self.assertEqual({e.title for e in result.entries}, {"가상곡 가"})
        self.assertEqual(len({e.provider_track_id for e in result.entries}), 3)

    def test_demo_fixtures_are_consistent(self) -> None:
        a = parse_chart_csv(CHARTS / "period_a.csv", expected_n=5)
        b = parse_chart_csv(CHARTS / "period_b.csv", expected_n=5)
        self.assertTrue(b.had_bom)
        keys_a = {(e.title, e.artist, e.provider_track_id) for e in a.entries}
        keys_b = {(e.title, e.artist, e.provider_track_id) for e in b.entries}
        self.assertEqual(len(keys_a), 5)
        self.assertEqual(len(keys_b), 5)
        self.assertEqual(len(keys_a & keys_b), 2, "두 기간이 공유하는 가상곡은 2개")
        self.assertEqual(len(keys_a | keys_b), 8, "가상곡은 총 8개")

    def test_file_not_found(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_chart_csv(self.path("nope.csv"), expected_n=5)
        self.assertEqual(ctx.exception.code, "csv_not_found")


if __name__ == "__main__":
    unittest.main()
