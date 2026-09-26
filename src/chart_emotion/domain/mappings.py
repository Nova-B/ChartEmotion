"""차트 항목 매핑 파일 계약 (`import-mappings`).

파일 구조:
{
  "schema": "chart-emotion.mappings.v1",   // 선택
  "reviewer_id": "reviewer-1",             // 필수. 항목별 reviewer_id 로 덮어쓸 수 있음
  "mappings": [
    {"snapshot_id": "...", "rank": 1, "state": "confirmed", "recording_id": "...",
     "lyric_version_id": "...", "reason": "...", "base_revision": 0}
  ]
}

항목 필수: snapshot_id, rank, state, reason, base_revision
항목 선택: recording_id, lyric_version_id, reviewer_id

상태:
- candidate  : 후보. 녹음 ID 가 있어도 확정이 아니다(문자열·ID 가 일치해도 자동 확정하지 않는다).
- confirmed  : 사람이 확정. recording_id 필수, lyric_version_id 선택(같은 녹음의 것이어야 함).
- unresolved : 판단 보류·철회. recording_id·lyric_version_id 를 적지 않는다.
- (행 없음)  : unmapped. 파생 상태.

base_revision 은 그 항목의 현재 최신 revision(없으면 0)이어야 한다. 다르면 배치 전체가 거부된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import MAPPING_STATES, MAPPING_STATES_WITH_RECORDING, MAPPINGS_SCHEMA
from .jsonfile import load_json_object
from .validation import FieldErrors, check_enum, check_identifier, check_int, check_object_list, check_str, check_unknown_fields

MAPPING_REQUIRED: tuple[str, ...] = ("snapshot_id", "rank", "state", "reason", "base_revision")
MAPPING_OPTIONAL: tuple[str, ...] = ("recording_id", "lyric_version_id", "reviewer_id")
FILE_FIELDS: tuple[str, ...] = ("schema", "reviewer_id", "mappings")
MAX_RANK = 1000


@dataclass(frozen=True)
class MappingSpec:
    snapshot_id: str
    rank: int
    state: str
    recording_id: str | None
    lyric_version_id: str | None
    reviewer_id: str
    reason: str
    base_revision: int

    def content(self) -> dict[str, Any]:
        """재입력(replay) 비교에 쓰는 판정 내용."""
        return {
            "state": self.state,
            "recording_id": self.recording_id,
            "lyric_version_id": self.lyric_version_id,
            "reviewer_id": self.reviewer_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MappingsFile:
    reviewer_id: str
    mappings: tuple[MappingSpec, ...]


def _parse_mapping(errors: FieldErrors, item: dict[str, Any], index: int, default_reviewer: str | None) -> MappingSpec | None:
    prefix = f"mappings[{index}]."
    sub = FieldErrors("mapping")
    check_unknown_fields(sub, item, MAPPING_REQUIRED + MAPPING_OPTIONAL)
    snapshot_id = check_identifier(sub, item, "snapshot_id")
    rank = check_int(sub, item, "rank", minimum=1, maximum=MAX_RANK)
    state = check_enum(sub, item, "state", MAPPING_STATES)
    recording_id = check_identifier(sub, item, "recording_id", required=False)
    lyric_version_id = check_identifier(sub, item, "lyric_version_id", required=False)
    reviewer_id = check_str(sub, item, "reviewer_id", required=False) or default_reviewer
    reason = check_str(sub, item, "reason")
    base_revision = check_int(sub, item, "base_revision", minimum=0)
    if state in MAPPING_STATES_WITH_RECORDING and recording_id is None and not any(e["field"] == "recording_id" for e in sub.items):
        sub.add("recording_id", "missing_field", f"state={state} 이면 recording_id 가 필요합니다")
    if state == "unresolved":
        if recording_id is not None:
            sub.add("recording_id", "state_conflict", "state=unresolved 이면 recording_id 를 적지 않습니다 (이유는 reason 에 남깁니다)")
        if lyric_version_id is not None:
            sub.add("lyric_version_id", "state_conflict", "state=unresolved 이면 lyric_version_id 를 적지 않습니다")
    for err in sub.items:
        errors.add(prefix + err["field"], err["code"], err["message"])
    if sub or reviewer_id is None:
        return None
    assert snapshot_id and rank is not None and state and reason and base_revision is not None
    return MappingSpec(
        snapshot_id=snapshot_id,
        rank=rank,
        state=state,
        recording_id=recording_id,
        lyric_version_id=lyric_version_id,
        reviewer_id=reviewer_id,
        reason=reason,
        base_revision=base_revision,
    )


def parse_mappings_file(obj: dict[str, Any], *, context: str = "매핑 파일") -> MappingsFile:
    errors = FieldErrors(context)
    check_unknown_fields(errors, obj, FILE_FIELDS)
    schema = check_str(errors, obj, "schema", required=False)
    if schema is not None and schema != MAPPINGS_SCHEMA:
        errors.add("schema", "unsupported_schema", f"지원하는 계약은 '{MAPPINGS_SCHEMA}' 뿐입니다 (현재: '{schema}')")
    reviewer_id = check_str(errors, obj, "reviewer_id")
    items = check_object_list(errors, obj, "mappings", min_items=1)

    mappings: list[MappingSpec] = []
    seen: dict[tuple[str, int], int] = {}
    for index, item in enumerate(items or []):
        spec = _parse_mapping(errors, item, index, reviewer_id)
        if spec is None:
            continue
        key = (spec.snapshot_id, spec.rank)
        if key in seen:
            errors.add(
                f"mappings[{index}]",
                "duplicate_entry_in_file",
                f"({spec.snapshot_id}, rank {spec.rank}) 이(가) mappings[{seen[key]}] 과 중복됩니다. 한 배치에 항목당 판정은 하나만 적습니다",
            )
            continue
        seen[key] = index
        mappings.append(spec)
    errors.raise_if_any("mappings_file_invalid", "매핑 파일에 오류가 있습니다")
    assert reviewer_id
    return MappingsFile(reviewer_id=reviewer_id, mappings=tuple(mappings))


def load_mappings_file(path: Path) -> MappingsFile:
    obj = load_json_object(path, f"매핑 파일 {path.name}")
    return parse_mappings_file(obj, context=f"매핑 파일 {path.name}")
