"""차트 배치 설정(JSON) 계약.

필수 필드: source_id, chart_id, period_start, period_end, display_year,
methodology_version, expected_n, data_mode
선택 필드: schema, snapshot_key, chart, notes

`display_year` 는 공급자가 붙인 표시 연도일 뿐이며 집계 기간을 이 값에서 유도하지 않는다.
실제 집계 기간은 반드시 period_start / period_end 로 적는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .contracts import BATCH_SCHEMA, DATA_MODES, PERIODICITIES
from .jsonfile import canonical_json, load_json_object
from .validation import (
    FieldErrors,
    check_date,
    check_enum,
    check_identifier,
    check_int,
    check_str,
    check_unknown_fields,
)

BATCH_REQUIRED_FIELDS: tuple[str, ...] = (
    "source_id",
    "chart_id",
    "period_start",
    "period_end",
    "display_year",
    "methodology_version",
    "expected_n",
    "data_mode",
)
BATCH_OPTIONAL_FIELDS: tuple[str, ...] = ("schema", "snapshot_key", "chart", "notes")
CHART_BLOCK_FIELDS: tuple[str, ...] = ("name", "market", "metric", "periodicity")

MAX_EXPECTED_N = 1000


@dataclass(frozen=True)
class ChartDefinitionSpec:
    name: str
    market: str
    metric: str
    periodicity: str


@dataclass(frozen=True)
class BatchConfig:
    source_id: str
    chart_id: str
    period_start: date
    period_end: date
    display_year: int
    methodology_version: str
    expected_n: int
    data_mode: str
    snapshot_key: str
    chart: ChartDefinitionSpec | None
    notes: str | None

    def identity(self) -> dict[str, Any]:
        """스냅샷 동일성 판정에 쓰는 정규 설정. snapshot_key·chart·notes 는 제외한다."""
        return {
            "source_id": self.source_id,
            "chart_id": self.chart_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "display_year": self.display_year,
            "methodology_version": self.methodology_version,
            "expected_n": self.expected_n,
            "data_mode": self.data_mode,
        }

    def config_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.identity()).encode("utf-8")).hexdigest()

    def snapshot_id_for(self, revision: int) -> str:
        return f"{self.snapshot_key}-r{revision}"


def parse_batch_config(obj: dict[str, Any], *, context: str = "배치 설정") -> BatchConfig:
    errors = FieldErrors(context)
    check_unknown_fields(errors, obj, BATCH_REQUIRED_FIELDS + BATCH_OPTIONAL_FIELDS)

    schema = check_str(errors, obj, "schema", required=False)
    if schema is not None and schema != BATCH_SCHEMA:
        errors.add("schema", "unsupported_schema", f"지원하는 배치 계약은 '{BATCH_SCHEMA}' 뿐입니다 (현재: '{schema}')")

    source_id = check_identifier(errors, obj, "source_id")
    chart_id = check_identifier(errors, obj, "chart_id")
    period_start = check_date(errors, obj, "period_start")
    period_end = check_date(errors, obj, "period_end")
    display_year = check_int(errors, obj, "display_year", minimum=1000, maximum=9999)
    methodology_version = check_str(errors, obj, "methodology_version")
    expected_n = check_int(errors, obj, "expected_n", minimum=1, maximum=MAX_EXPECTED_N)
    data_mode = check_enum(errors, obj, "data_mode", DATA_MODES)
    snapshot_key = check_identifier(errors, obj, "snapshot_key", required=False)
    notes = check_str(errors, obj, "notes", required=False, allow_empty=True)

    if period_start is not None and period_end is not None and period_end < period_start:
        errors.add(
            "period_end",
            "date_order",
            f"period_end({period_end.isoformat()}) 가 period_start({period_start.isoformat()}) 보다 앞설 수 없습니다",
        )

    chart_spec: ChartDefinitionSpec | None = None
    if "chart" in obj and obj["chart"] is not None:
        chart_obj = obj["chart"]
        if not isinstance(chart_obj, dict):
            errors.add("chart", "wrong_type", "chart 는 객체여야 합니다")
        else:
            sub = FieldErrors(context)
            check_unknown_fields(sub, chart_obj, CHART_BLOCK_FIELDS)
            name = check_str(sub, chart_obj, "name")
            market = check_str(sub, chart_obj, "market")
            metric = check_str(sub, chart_obj, "metric")
            periodicity = check_enum(sub, chart_obj, "periodicity", PERIODICITIES)
            for item in sub.items:
                errors.add(f"chart.{item['field']}", item["code"], item["message"])
            if not sub and name and market and metric and periodicity:
                chart_spec = ChartDefinitionSpec(name=name, market=market, metric=metric, periodicity=periodicity)

    errors.raise_if_any("batch_config_invalid", "배치 설정에 오류가 있습니다")

    assert source_id and chart_id and period_start and period_end and methodology_version and data_mode
    assert display_year is not None and expected_n is not None
    if snapshot_key is None:
        snapshot_key = f"{chart_id}-{period_start.isoformat()}-{period_end.isoformat()}"

    return BatchConfig(
        source_id=source_id,
        chart_id=chart_id,
        period_start=period_start,
        period_end=period_end,
        display_year=display_year,
        methodology_version=methodology_version,
        expected_n=expected_n,
        data_mode=data_mode,
        snapshot_key=snapshot_key,
        chart=chart_spec,
        notes=notes,
    )


def load_batch_config(path: Path) -> BatchConfig:
    obj = load_json_object(path, f"배치 설정 {path.name}")
    return parse_batch_config(obj, context=f"배치 설정 {path.name}")
