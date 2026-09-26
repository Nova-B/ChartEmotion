"""D02/D04: 배치 설정 계약."""

from __future__ import annotations

import unittest
from datetime import date

from chart_emotion.domain.batch import load_batch_config, parse_batch_config
from chart_emotion.errors import InputError

from helpers import INVALID, TempDirCase, batch_dict, write_json, write_text


def error_fields(exc: InputError) -> dict[str, str]:
    return {item["field"]: item["code"] for item in exc.details}


class BatchConfigTest(TempDirCase):
    def test_valid_batch(self) -> None:
        batch = parse_batch_config(batch_dict())
        self.assertEqual(batch.period_start, date(2015, 1, 1))
        self.assertEqual(batch.snapshot_id_for(1), "test-period-r1")
        self.assertEqual(batch.chart.periodicity, "yearly")
        self.assertEqual(
            set(batch.identity()),
            {"source_id", "chart_id", "period_start", "period_end", "display_year", "methodology_version", "expected_n", "data_mode"},
        )

    def test_snapshot_key_defaults_from_observation(self) -> None:
        batch = parse_batch_config(batch_dict(snapshot_key=None))
        self.assertEqual(batch.snapshot_id_for(2), "synthetic-annual-top5-2015-01-01-2015-12-31-r2")

    def test_config_hash_ignores_snapshot_key_chart_and_notes(self) -> None:
        a = parse_batch_config(batch_dict())
        b = parse_batch_config(batch_dict(snapshot_key="other", chart=None, notes="메모"))
        c = parse_batch_config(batch_dict(display_year=2016))
        self.assertEqual(a.config_sha256(), b.config_sha256())
        self.assertNotEqual(a.config_sha256(), c.config_sha256())

    def test_missing_required_fields(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_batch_config({})
        fields = error_fields(ctx.exception)
        for name in ("source_id", "chart_id", "period_start", "period_end", "display_year", "methodology_version", "expected_n", "data_mode"):
            self.assertEqual(fields.get(name), "missing_field", name)

    def test_invalid_and_impossible_dates(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_batch_config(batch_dict(period_start="2015/01/01", period_end="2015-02-30"))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["period_start"], "invalid_date")
        self.assertEqual(fields["period_end"], "invalid_date")

    def test_dates_must_be_ascii_and_in_gregorian_range(self) -> None:
        for start in ("٢٠١٥-01-01", "２０１５-01-01", "0000-01-01", "2015-02-29"):
            with self.subTest(start=start):
                with self.assertRaises(InputError) as ctx:
                    parse_batch_config(batch_dict(period_start=start))
                self.assertEqual(error_fields(ctx.exception)["period_start"], "invalid_date")
        parse_batch_config(batch_dict(period_start="0001-01-01", period_end="0001-12-31", display_year=1000))
        parse_batch_config(batch_dict(period_start="2024-02-29", period_end="2024-02-29", display_year=2024))

    def test_date_order(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_batch_config(batch_dict(period_start="2015-12-31", period_end="2015-01-01"))
        self.assertEqual(error_fields(ctx.exception)["period_end"], "date_order")
        # 같은 날짜(하루짜리 기간)는 허용
        parse_batch_config(batch_dict(period_start="2015-06-01", period_end="2015-06-01"))

    def test_display_year_is_not_used_to_derive_period(self) -> None:
        batch = parse_batch_config(batch_dict(display_year=2016, period_start="2015-11-01", period_end="2016-10-31"))
        self.assertEqual(batch.display_year, 2016)
        self.assertEqual(batch.period_start, date(2015, 11, 1))

    def test_types_are_strict(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_batch_config(batch_dict(expected_n=True, display_year="2015", methodology_version=1))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["expected_n"], "wrong_type")
        self.assertEqual(fields["display_year"], "wrong_type")
        self.assertEqual(fields["methodology_version"], "wrong_type")

    def test_ranges_and_enums(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_batch_config(batch_dict(expected_n=0, data_mode="fake", display_year=99))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["expected_n"], "out_of_range")
        self.assertEqual(fields["data_mode"], "invalid_enum")
        self.assertEqual(fields["display_year"], "out_of_range")

    def test_identifiers(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_batch_config(batch_dict(source_id="한글 출처", chart_id="-bad", snapshot_key="a b"))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["source_id"], "invalid_identifier")
        self.assertEqual(fields["chart_id"], "invalid_identifier")
        self.assertEqual(fields["snapshot_key"], "invalid_identifier")

    def test_unknown_fields_and_schema(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_batch_config(batch_dict(year=2015, schema="chart-emotion.batch.v9"))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["year"], "unknown_field")
        self.assertEqual(fields["schema"], "unsupported_schema")

    def test_chart_block_validation(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_batch_config(batch_dict(chart={"name": "x", "market": "kr", "metric": "m", "periodicity": "biweekly", "extra": 1}))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["chart.periodicity"], "invalid_enum")
        self.assertEqual(fields["chart.extra"], "unknown_field")
        with self.assertRaises(InputError) as ctx:
            parse_batch_config(batch_dict(chart="yearly"))
        self.assertEqual(error_fields(ctx.exception)["chart"], "wrong_type")

    def test_fixture_invalid_dates(self) -> None:
        with self.assertRaises(InputError) as ctx:
            load_batch_config(INVALID / "invalid_dates.json")
        self.assertEqual(error_fields(ctx.exception)["period_end"], "date_order")

    def test_fixture_wrong_types(self) -> None:
        with self.assertRaises(InputError) as ctx:
            load_batch_config(INVALID / "wrong_types.json")
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["expected_n"], "wrong_type")
        self.assertEqual(fields["display_year"], "wrong_type")
        self.assertEqual(fields["data_mode"], "invalid_enum")
        self.assertEqual(fields["period_end"], "invalid_date")

    def test_duplicate_json_keys_rejected(self) -> None:
        top = write_text(self.path("dup.json"), '{"source_id": "a", "expected_n": 5, "expected_n": 6}')
        with self.assertRaises(InputError) as ctx:
            load_batch_config(top)
        self.assertEqual(ctx.exception.code, "json_duplicate_key")
        self.assertEqual(ctx.exception.details[0]["key"], "expected_n")
        nested = write_text(
            self.path("dup_nested.json"),
            '{"source_id": "a", "chart": {"name": "x", "market": "kr", "market": "jp"}}',
        )
        with self.assertRaises(InputError) as ctx:
            load_batch_config(nested)
        self.assertEqual(ctx.exception.code, "json_duplicate_key")
        self.assertEqual(ctx.exception.details[0]["key"], "market")

    def test_huge_integer_token_is_input_error(self) -> None:
        digits = "9" * 5000  # int 변환 자릿수 한계(4300)를 넘는 토큰
        huge = write_text(self.path("huge.json"), '{"source_id": "a", "expected_n": ' + digits + "}")
        with self.assertRaises(InputError) as ctx:
            load_batch_config(huge)
        self.assertEqual(ctx.exception.code, "json_value_error")
        self.assertEqual(ctx.exception.exit_code, 2)
        big = write_json(self.path("big.json"), batch_dict(expected_n=10**400, display_year=10**30))
        with self.assertRaises(InputError) as ctx:
            load_batch_config(big)
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["expected_n"], "out_of_range")
        self.assertEqual(fields["display_year"], "out_of_range")

    def test_json_file_errors(self) -> None:
        with self.assertRaises(InputError) as ctx:
            load_batch_config(self.path("missing.json"))
        self.assertEqual(ctx.exception.code, "json_not_found")
        bad = write_text(self.path("bad.json"), '{"source_id": ')
        with self.assertRaises(InputError) as ctx:
            load_batch_config(bad)
        self.assertEqual(ctx.exception.code, "json_syntax_error")
        arr = write_json(self.path("arr.json"), [1])
        with self.assertRaises(InputError) as ctx:
            load_batch_config(arr)
        self.assertEqual(ctx.exception.code, "json_not_object")
        bom = write_json(self.path("bom.json"), batch_dict())
        bom.write_bytes(b"\xef\xbb\xbf" + bom.read_bytes())
        self.assertEqual(load_batch_config(bom).chart_id, "synthetic-annual-top5")


if __name__ == "__main__":
    unittest.main()
