"""D02: 라벨·판정 값·실험 설정 계약."""

from __future__ import annotations

import math
import unittest

from chart_emotion.domain.contracts import (
    ANNOTATION_VALUES,
    LABEL_IDS,
    LABELS,
    AnnotationState,
    is_valid_identifier,
    parse_annotation_value,
)
from chart_emotion.domain.experiment import parse_experiment_config
from chart_emotion.errors import InputError

from helpers import experiment_dict


def error_fields(exc: InputError) -> dict[str, str]:
    return {item["field"]: item["code"] for item in exc.details}


class LabelContractTest(unittest.TestCase):
    def test_v01_labels_are_exactly_three(self) -> None:
        self.assertEqual(LABEL_IDS, ("theme.romance", "emotion.anxiety", "function.comfort"))
        self.assertEqual({label.axis for label in LABELS.values()}, {"theme", "emotion", "function"})

    def test_annotation_values(self) -> None:
        self.assertEqual(ANNOTATION_VALUES, ("present", "absent", "uncertain"))

    def test_blank_is_unreviewed_not_absent(self) -> None:
        for raw in ("", "   ", None):
            state = parse_annotation_value(raw)
            self.assertIs(state, AnnotationState.UNREVIEWED)
            self.assertIsNot(state, AnnotationState.ABSENT)
            self.assertFalse(state.is_decided)

    def test_known_values_parse(self) -> None:
        self.assertIs(parse_annotation_value("present"), AnnotationState.PRESENT)
        self.assertIs(parse_annotation_value(" Absent "), AnnotationState.ABSENT)
        self.assertIs(parse_annotation_value("uncertain"), AnnotationState.UNCERTAIN)
        self.assertTrue(AnnotationState.PRESENT.is_decided)
        self.assertFalse(AnnotationState.UNCERTAIN.is_decided)

    def test_unknown_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_annotation_value("yes")
        with self.assertRaises(ValueError):
            parse_annotation_value("0")

    def test_identifier_rule(self) -> None:
        self.assertTrue(is_valid_identifier("demo-period-a"))
        self.assertTrue(is_valid_identifier("chart.v1_2"))
        self.assertFalse(is_valid_identifier("-leading"))
        self.assertFalse(is_valid_identifier("한글"))
        self.assertFalse(is_valid_identifier("has space"))
        self.assertFalse(is_valid_identifier(""))
        self.assertFalse(is_valid_identifier(12))


class ExperimentConfigTest(unittest.TestCase):
    def test_valid_config(self) -> None:
        config = parse_experiment_config(experiment_dict(question="가상 질문"))
        self.assertEqual(config.snapshot_ids, ("demo-period-a-r1", "demo-period-b-r1"))
        self.assertEqual(config.labels, ("theme.romance", "emotion.anxiety", "function.comfort"))
        self.assertEqual(config.coverage_threshold, 0.8)
        self.assertEqual(config.to_dict()["experiment_id"], "test-exp")

    def test_missing_required_fields(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config({})
        fields = error_fields(ctx.exception)
        for name in ("experiment_id", "revision", "data_mode", "snapshot_ids", "top_n", "labelset_version",
                     "labels", "counting_unit", "weighting", "annotation_role", "coverage_threshold"):
            self.assertEqual(fields.get(name), "missing_field", name)
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_bool_is_not_integer(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(revision=True, top_n=False))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["revision"], "wrong_type")
        self.assertEqual(fields["top_n"], "wrong_type")

    def test_bool_is_not_number(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(coverage_threshold=True))
        self.assertEqual(error_fields(ctx.exception)["coverage_threshold"], "wrong_type")

    def test_coverage_threshold_range_and_finite(self) -> None:
        for bad, code in ((1.5, "out_of_range"), (-0.1, "out_of_range"), (math.nan, "not_finite"), (math.inf, "not_finite"), ("0.8", "wrong_type")):
            with self.subTest(value=bad):
                with self.assertRaises(InputError) as ctx:
                    parse_experiment_config(experiment_dict(coverage_threshold=bad))
                self.assertEqual(error_fields(ctx.exception)["coverage_threshold"], code)
        config = parse_experiment_config(experiment_dict(coverage_threshold=1))
        self.assertEqual(config.coverage_threshold, 1.0)

    def test_huge_numbers_are_input_errors_not_crashes(self) -> None:
        # 10**400 은 JSON 으로는 읽히지만 float 로 바꿀 수 없다 → 입력 오류
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(coverage_threshold=10**400))
        self.assertEqual(error_fields(ctx.exception)["coverage_threshold"], "not_finite")
        self.assertEqual(ctx.exception.exit_code, 2)
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(top_n=10**400, revision=2**63))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["top_n"], "out_of_range")
        self.assertEqual(fields["revision"], "out_of_range")

    def test_top_n_and_revision_minimum(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(top_n=0, revision=0))
        fields = error_fields(ctx.exception)
        self.assertEqual(fields["top_n"], "out_of_range")
        self.assertEqual(fields["revision"], "out_of_range")

    def test_unknown_label_rejected(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(labels=["theme.romance", "emotion.despair"]))
        self.assertEqual(error_fields(ctx.exception)["labels"], "unknown_label")
        self.assertIn("emotion.despair", ctx.exception.details[0]["message"])

    def test_labels_must_be_nonempty_unique(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(labels=[]))
        self.assertEqual(error_fields(ctx.exception)["labels"], "too_few_items")
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(labels=["theme.romance", "theme.romance"]))
        self.assertEqual(error_fields(ctx.exception)["labels"], "duplicate_items")

    def test_exactly_two_distinct_snapshots(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(snapshot_ids=["a-r1"]))
        self.assertEqual(error_fields(ctx.exception)["snapshot_ids"], "too_few_items")
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(snapshot_ids=["a-r1", "b-r1", "c-r1"]))
        self.assertEqual(error_fields(ctx.exception)["snapshot_ids"], "too_many_items")
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(snapshot_ids=["a-r1", "a-r1"]))
        self.assertEqual(error_fields(ctx.exception)["snapshot_ids"], "duplicate_items")
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(snapshot_ids="a-r1,b-r1"))
        self.assertEqual(error_fields(ctx.exception)["snapshot_ids"], "wrong_type")

    def test_enums(self) -> None:
        cases = {
            "data_mode": "fake",
            "counting_unit": "unique_song",
            "weighting": "plays",
            "annotation_role": "model_proposal",
            "labelset_version": "v9.9",
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                with self.assertRaises(InputError) as ctx:
                    parse_experiment_config(experiment_dict(**{field: value}))
                self.assertEqual(error_fields(ctx.exception)[field], "invalid_enum")

    def test_unknown_field_and_schema(self) -> None:
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(top_N=5))
        self.assertEqual(error_fields(ctx.exception)["top_N"], "unknown_field")
        with self.assertRaises(InputError) as ctx:
            parse_experiment_config(experiment_dict(schema="chart-emotion.experiment.v2"))
        self.assertEqual(error_fields(ctx.exception)["schema"], "unsupported_schema")
        parse_experiment_config(experiment_dict(schema="chart-emotion.experiment.v1"))

    def test_not_an_object(self) -> None:
        from chart_emotion.domain.validation import require_object

        with self.assertRaises(InputError) as ctx:
            require_object([1, 2], "실험 설정")
        self.assertEqual(ctx.exception.code, "json_not_object")


if __name__ == "__main__":
    unittest.main()
