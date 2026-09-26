"""녹음(recording)·가사 버전 등록 파일 계약 (`import-recordings`).

파일 구조:
{
  "schema": "chart-emotion.recordings.v1",      // 선택
  "registered_by": "reviewer-1",                 // 필수. 등록자
  "recordings": [ {...}, ... ],                  // 선택(둘 중 하나는 있어야 함)
  "lyric_versions": [ {...}, ... ]               // 선택
}

recording 항목 필수: recording_id, title, artist, version_kind, release_precision, vocal_type, data_mode
recording 항목 선택: version_label, original_recording_id, release_date, source_id, provider_track_ids, notes
lyric_version 항목 필수: lyric_version_id, recording_id, status
lyric_version 항목 선택: language, source_id, reference, notes

원칙:
- ID 는 제목·아티스트·provider_track_id 와 무관한 독립 문자열이다. 문자열 유사성으로 병합하지 않는다.
- 발매일은 정밀도(unknown/year/month/day)와 함께 적는다. 연도만 알면 '1월 1일'을 지어내지 않는다.
- 가사 본문 필드는 없다. 상태·언어·출처·참조만 기록한다.
- 같은 ID 로 같은 내용을 다시 넣으면 no-op, 다른 내용은 거부한다(새 ID 를 쓴다).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    DATA_MODES,
    LANGUAGE_PATTERN,
    LYRIC_STATUSES,
    LYRIC_STATUSES_WITH_ACCESS,
    RECORDINGS_SCHEMA,
    RELEASE_PRECISIONS,
    VERSION_KINDS,
    VOCAL_TYPES,
)
from .jsonfile import canonical_json, load_json_object
from .validation import (
    FieldErrors,
    check_enum,
    check_identifier,
    check_object_list,
    check_str,
    check_str_list,
    check_unknown_fields,
    parse_iso_date,
)

RECORDING_REQUIRED: tuple[str, ...] = ("recording_id", "title", "artist", "version_kind", "release_precision", "vocal_type", "data_mode")
RECORDING_OPTIONAL: tuple[str, ...] = ("version_label", "original_recording_id", "release_date", "source_id", "provider_track_ids", "notes")
LYRIC_REQUIRED: tuple[str, ...] = ("lyric_version_id", "recording_id", "status")
LYRIC_OPTIONAL: tuple[str, ...] = ("language", "source_id", "reference", "notes")
FILE_FIELDS: tuple[str, ...] = ("schema", "registered_by", "recordings", "lyric_versions")

# ASCII 숫자만. 연도는 0001..9999 (그레고리력 범위). 연·월만 알 때 나머지 요소를 지어내지 않고 형식만 검사한다.
YEAR_RE = re.compile(r"^(?!0000)[0-9]{4}$")
MONTH_RE = re.compile(r"^(?!0000)[0-9]{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class RecordingSpec:
    recording_id: str
    title: str
    artist: str
    version_kind: str
    version_label: str | None
    original_recording_id: str | None
    release_precision: str
    release_date: str | None
    vocal_type: str
    data_mode: str
    source_id: str | None
    provider_track_ids: tuple[str, ...]
    notes: str | None

    def identity(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "title": self.title,
            "artist": self.artist,
            "version_kind": self.version_kind,
            "version_label": self.version_label,
            "original_recording_id": self.original_recording_id,
            "release_precision": self.release_precision,
            "release_date": self.release_date,
            "vocal_type": self.vocal_type,
            "data_mode": self.data_mode,
            "source_id": self.source_id,
            "provider_track_ids": list(self.provider_track_ids),
            "notes": self.notes,
        }

    def content_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.identity()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LyricVersionSpec:
    lyric_version_id: str
    recording_id: str
    status: str
    language: str | None
    source_id: str | None
    reference: str | None
    notes: str | None

    def identity(self) -> dict[str, Any]:
        return {
            "lyric_version_id": self.lyric_version_id,
            "recording_id": self.recording_id,
            "status": self.status,
            "language": self.language,
            "source_id": self.source_id,
            "reference": self.reference,
            "notes": self.notes,
        }

    def content_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.identity()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RecordingsFile:
    registered_by: str
    recordings: tuple[RecordingSpec, ...]
    lyric_versions: tuple[LyricVersionSpec, ...]


def _check_release(errors: FieldErrors, item: dict[str, Any], prefix: str) -> tuple[str | None, str | None]:
    precision = check_enum(errors, item, "release_precision", RELEASE_PRECISIONS)
    raw = item.get("release_date")
    if raw is not None and not isinstance(raw, str):
        errors.add(f"{prefix}release_date", "wrong_type", f"문자열이어야 합니다 (현재: {type(raw).__name__})")
        return precision, None
    if precision is None:
        return None, None
    if precision == "unknown":
        if raw is not None:
            errors.add(f"{prefix}release_date", "precision_conflict", "release_precision 이 unknown 이면 release_date 를 적지 않습니다 (날짜를 지어내지 않음)")
            return precision, None
        return precision, None
    if raw is None:
        errors.add(f"{prefix}release_date", "missing_field", f"release_precision={precision} 이면 release_date 가 필요합니다")
        return precision, None
    valid = (
        (precision == "year" and YEAR_RE.fullmatch(raw) is not None)
        or (precision == "month" and MONTH_RE.fullmatch(raw) is not None)
        or (precision == "day" and parse_iso_date(raw) is not None)
    )
    if not valid:
        expected = {"year": "YYYY", "month": "YYYY-MM", "day": "YYYY-MM-DD"}[precision]
        errors.add(
            f"{prefix}release_date",
            "invalid_date",
            f"release_precision={precision} 이면 ASCII 숫자 {expected} 형식(연도 0001~9999)의 실재하는 값이어야 합니다 (현재: '{raw}')",
        )
        return precision, None
    return precision, raw


def _parse_recording(errors: FieldErrors, item: dict[str, Any], index: int) -> RecordingSpec | None:
    prefix = f"recordings[{index}]."
    sub = FieldErrors("recording")
    check_unknown_fields(sub, item, RECORDING_REQUIRED + RECORDING_OPTIONAL)
    recording_id = check_identifier(sub, item, "recording_id")
    title = check_str(sub, item, "title")
    artist = check_str(sub, item, "artist")
    version_kind = check_enum(sub, item, "version_kind", VERSION_KINDS)
    version_label = check_str(sub, item, "version_label", required=False)
    original_recording_id = check_identifier(sub, item, "original_recording_id", required=False)
    release_precision, release_date = _check_release(sub, item, "")
    vocal_type = check_enum(sub, item, "vocal_type", VOCAL_TYPES)
    data_mode = check_enum(sub, item, "data_mode", DATA_MODES)
    source_id = check_identifier(sub, item, "source_id", required=False)
    provider_track_ids = check_str_list(sub, item, "provider_track_ids", required=False, unique=True)
    notes = check_str(sub, item, "notes", required=False, allow_empty=True)
    if recording_id and original_recording_id == recording_id:
        sub.add("original_recording_id", "self_reference", "자기 자신을 원곡으로 가리킬 수 없습니다")
    if version_kind == "original" and original_recording_id:
        sub.add("original_recording_id", "version_conflict", "version_kind=original 인 녹음에는 original_recording_id 를 적지 않습니다")
    for err in sub.items:
        errors.add(prefix + err["field"], err["code"], err["message"])
    if sub:
        return None
    assert recording_id and title and artist and version_kind and release_precision and vocal_type and data_mode
    return RecordingSpec(
        recording_id=recording_id,
        title=title,
        artist=artist,
        version_kind=version_kind,
        version_label=version_label,
        original_recording_id=original_recording_id,
        release_precision=release_precision,
        release_date=release_date,
        vocal_type=vocal_type,
        data_mode=data_mode,
        source_id=source_id,
        provider_track_ids=tuple(provider_track_ids or ()),
        notes=notes,
    )


def _parse_lyric_version(errors: FieldErrors, item: dict[str, Any], index: int) -> LyricVersionSpec | None:
    prefix = f"lyric_versions[{index}]."
    sub = FieldErrors("lyric_version")
    check_unknown_fields(sub, item, LYRIC_REQUIRED + LYRIC_OPTIONAL)
    lyric_version_id = check_identifier(sub, item, "lyric_version_id")
    recording_id = check_identifier(sub, item, "recording_id")
    status = check_enum(sub, item, "status", LYRIC_STATUSES)
    language = check_str(sub, item, "language", required=False)
    source_id = check_identifier(sub, item, "source_id", required=False)
    reference = check_str(sub, item, "reference", required=False)
    notes = check_str(sub, item, "notes", required=False, allow_empty=True)
    if language is not None and LANGUAGE_PATTERN.fullmatch(language) is None:
        sub.add("language", "invalid_language", "언어 태그는 'ko', 'en', 'ja', 'zh-Hant' 처럼 소문자 2~3자 코드로 시작해야 합니다")
        language = None
    if status in LYRIC_STATUSES_WITH_ACCESS:
        if language is None and not any(e["field"] == "language" for e in sub.items):
            sub.add("language", "missing_field", f"status={status} 이면 language 가 필요합니다")
        if source_id is None and not any(e["field"] == "source_id" for e in sub.items):
            sub.add("source_id", "missing_field", f"status={status} 이면 가사 출처 source_id 가 필요합니다")
    elif status is not None:
        if reference is not None:
            sub.add("reference", "status_conflict", f"status={status} 이면 접근 참조(reference)를 적지 않습니다")
        if status == "not_applicable" and language is not None:
            sub.add("language", "status_conflict", "not_applicable(연주곡 등) 에는 language 를 적지 않습니다")
    for err in sub.items:
        errors.add(prefix + err["field"], err["code"], err["message"])
    if sub:
        return None
    assert lyric_version_id and recording_id and status
    return LyricVersionSpec(
        lyric_version_id=lyric_version_id,
        recording_id=recording_id,
        status=status,
        language=language,
        source_id=source_id,
        reference=reference,
        notes=notes,
    )


def parse_recordings_file(obj: dict[str, Any], *, context: str = "녹음 등록 파일") -> RecordingsFile:
    errors = FieldErrors(context)
    check_unknown_fields(errors, obj, FILE_FIELDS)
    schema = check_str(errors, obj, "schema", required=False)
    if schema is not None and schema != RECORDINGS_SCHEMA:
        errors.add("schema", "unsupported_schema", f"지원하는 계약은 '{RECORDINGS_SCHEMA}' 뿐입니다 (현재: '{schema}')")
    registered_by = check_str(errors, obj, "registered_by")
    recording_items = check_object_list(errors, obj, "recordings", required=False)
    lyric_items = check_object_list(errors, obj, "lyric_versions", required=False)
    if "recordings" not in obj and "lyric_versions" not in obj:
        errors.add("recordings", "missing_field", "recordings 또는 lyric_versions 중 하나는 있어야 합니다")

    recordings: list[RecordingSpec] = []
    seen_rec: set[str] = set()
    for index, item in enumerate(recording_items or []):
        spec = _parse_recording(errors, item, index)
        if spec is None:
            continue
        if spec.recording_id in seen_rec:
            errors.add(f"recordings[{index}].recording_id", "duplicate_in_file", f"recording_id '{spec.recording_id}' 이(가) 파일 안에서 반복됩니다")
            continue
        seen_rec.add(spec.recording_id)
        recordings.append(spec)

    lyric_versions: list[LyricVersionSpec] = []
    seen_lyr: set[str] = set()
    for index, item in enumerate(lyric_items or []):
        spec = _parse_lyric_version(errors, item, index)
        if spec is None:
            continue
        if spec.lyric_version_id in seen_lyr:
            errors.add(f"lyric_versions[{index}].lyric_version_id", "duplicate_in_file", f"lyric_version_id '{spec.lyric_version_id}' 이(가) 파일 안에서 반복됩니다")
            continue
        seen_lyr.add(spec.lyric_version_id)
        lyric_versions.append(spec)

    if not errors and not recordings and not lyric_versions:
        errors.add("recordings", "too_few_items", "등록할 녹음 또는 가사 버전이 없습니다")
    errors.raise_if_any("recordings_file_invalid", "녹음 등록 파일에 오류가 있습니다")
    assert registered_by
    return RecordingsFile(registered_by=registered_by, recordings=tuple(recordings), lyric_versions=tuple(lyric_versions))


def load_recordings_file(path: Path) -> RecordingsFile:
    obj = load_json_object(path, f"녹음 등록 파일 {path.name}")
    return parse_recordings_file(obj, context=f"녹음 등록 파일 {path.name}")
