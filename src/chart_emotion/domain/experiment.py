"""실험 설정(JSON) 계약. 구조 검증만 담당하며 DB 참조 검증은 application.validate 에서 한다.

필수 필드: experiment_id, revision, data_mode, snapshot_ids, top_n, labelset_version,
labels, counting_unit, weighting, annotation_role, coverage_threshold
선택 필드: schema, question, comparability_note
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    ANNOTATION_ROLES,
    COUNTING_UNITS,
    DATA_MODES,
    EXPERIMENT_SCHEMA,
    LABEL_IDS,
    SUPPORTED_LABELSET_VERSIONS,
    WEIGHTINGS,
)
from .jsonfile import load_json_object
from .validation import (
    FieldErrors,
    check_enum,
    check_identifier,
    check_int,
    check_number,
    check_str,
    check_str_list,
    check_unknown_fields,
)

EXPERIMENT_REQUIRED_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "revision",
    "data_mode",
    "snapshot_ids",
    "top_n",
    "labelset_version",
    "labels",
    "counting_unit",
    "weighting",
    "annotation_role",
    "coverage_threshold",
)
EXPERIMENT_OPTIONAL_FIELDS: tuple[str, ...] = ("schema", "question", "comparability_note")

MAX_TOP_N = 1000


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    revision: int
    data_mode: str
    snapshot_ids: tuple[str, str]
    top_n: int
    labelset_version: str
    labels: tuple[str, ...]
    counting_unit: str
    weighting: str
    annotation_role: str
    coverage_threshold: float
    question: str | None
    comparability_note: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "revision": self.revision,
            "data_mode": self.data_mode,
            "snapshot_ids": list(self.snapshot_ids),
            "top_n": self.top_n,
            "labelset_version": self.labelset_version,
            "labels": list(self.labels),
            "counting_unit": self.counting_unit,
            "weighting": self.weighting,
            "annotation_role": self.annotation_role,
            "coverage_threshold": self.coverage_threshold,
            "question": self.question,
            "comparability_note": self.comparability_note,
        }


def parse_experiment_config(obj: dict[str, Any], *, context: str = "실험 설정") -> ExperimentConfig:
    errors = FieldErrors(context)
    check_unknown_fields(errors, obj, EXPERIMENT_REQUIRED_FIELDS + EXPERIMENT_OPTIONAL_FIELDS)

    schema = check_str(errors, obj, "schema", required=False)
    if schema is not None and schema != EXPERIMENT_SCHEMA:
        errors.add(
            "schema", "unsupported_schema", f"지원하는 실험 계약은 '{EXPERIMENT_SCHEMA}' 뿐입니다 (현재: '{schema}')"
        )

    experiment_id = check_identifier(errors, obj, "experiment_id")
    revision = check_int(errors, obj, "revision", minimum=1)
    data_mode = check_enum(errors, obj, "data_mode", DATA_MODES)
    snapshot_ids = check_str_list(errors, obj, "snapshot_ids", min_items=2, max_items=2, unique=True)
    top_n = check_int(errors, obj, "top_n", minimum=1, maximum=MAX_TOP_N)
    labelset_version = check_enum(errors, obj, "labelset_version", SUPPORTED_LABELSET_VERSIONS)
    labels = check_str_list(errors, obj, "labels", min_items=1, unique=True)
    if labels is not None:
        unknown = [label for label in labels if label not in LABEL_IDS]
        if unknown:
            errors.add(
                "labels",
                "unknown_label",
                f"라벨셋 {labelset_version or '?'} 에 없는 라벨: {', '.join(unknown)}. 허용 라벨: {', '.join(LABEL_IDS)}",
            )
            labels = None
    counting_unit = check_enum(errors, obj, "counting_unit", COUNTING_UNITS)
    weighting = check_enum(errors, obj, "weighting", WEIGHTINGS)
    annotation_role = check_enum(errors, obj, "annotation_role", ANNOTATION_ROLES)
    coverage_threshold = check_number(errors, obj, "coverage_threshold", minimum=0.0, maximum=1.0)
    question = check_str(errors, obj, "question", required=False, allow_empty=True)
    comparability_note = check_str(errors, obj, "comparability_note", required=False)

    errors.raise_if_any("experiment_config_invalid", "실험 설정에 오류가 있습니다")

    assert experiment_id and data_mode and snapshot_ids and labelset_version and labels
    assert counting_unit and weighting and annotation_role
    assert revision is not None and top_n is not None and coverage_threshold is not None
    return ExperimentConfig(
        experiment_id=experiment_id,
        revision=revision,
        data_mode=data_mode,
        snapshot_ids=(snapshot_ids[0], snapshot_ids[1]),
        top_n=top_n,
        labelset_version=labelset_version,
        labels=tuple(labels),
        counting_unit=counting_unit,
        weighting=weighting,
        annotation_role=annotation_role,
        coverage_threshold=coverage_threshold,
        question=question,
        comparability_note=comparability_note,
    )


def load_experiment_config(path: Path) -> ExperimentConfig:
    obj = load_json_object(path, f"실험 설정 {path.name}")
    return parse_experiment_config(obj, context=f"실험 설정 {path.name}")
