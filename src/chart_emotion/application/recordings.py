"""`import-recordings <json>`: 녹음·가사 버전을 불변 등록한다.

- 파일 전체를 검증한 뒤 트랜잭션 하나로 반영한다. 어느 항목이든 실패하면 아무것도 남지 않는다.
- 같은 ID + 같은 내용 → unchanged(no-op). 같은 ID + 다른 내용 → recording_immutable / lyric_version_immutable 거부.
  과거 매핑이 가리키는 ID 의 의미가 바뀌지 않도록 수정은 항상 새 ID 로 한다.
- 녹음의 source_id(메타데이터 출처)는 선택이다. 적으면 metadata/chart 종류, data_mode 일치, import+store 허용이 필요하다.
- 가사 버전 status 가 available/partial/translation_only 이면 lyrics 종류 출처와 import+store 허용이 필요하다.
  missing / not_applicable 은 출처 허용이 필요 없다(가사에 접근하지 않았기 때문).
- 연주곡(instrumental) 녹음의 가사 버전은 not_applicable 만, 가창곡(lyrical)은 not_applicable 이외만 허용한다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..domain.contracts import LYRIC_STATUSES_WITH_ACCESS
from ..domain.recordings import LyricVersionSpec, RecordingSpec, load_recordings_file
from ..errors import InputError
from ..storage.database import open_workspace, utc_now
from .sources import check_source_gate, fetch_source

STORE_OPERATIONS: tuple[str, ...] = ("import", "store")


def row_to_recording(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["provider_track_ids"] = json.loads(data["provider_track_ids"])
    return data


def fetch_recording(conn: sqlite3.Connection, recording_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM recordings WHERE recording_id = ?", (recording_id,)).fetchone()
    return row_to_recording(row) if row is not None else None


def fetch_lyric_version(conn: sqlite3.Connection, lyric_version_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM lyric_versions WHERE lyric_version_id = ?", (lyric_version_id,)).fetchone()
    return dict(row) if row is not None else None


def _diff(stored: dict[str, Any], requested: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"field": k, "stored": stored.get(k), "requested": v} for k, v in requested.items() if stored.get(k) != v]


def import_recordings(workspace: Path, path: Path) -> dict[str, Any]:
    spec = load_recordings_file(Path(path))
    conn = open_workspace(Path(workspace))
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = _import_in_transaction(conn, spec.registered_by, spec.recordings, spec.lyric_versions)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()
    result["notice"] = "가사 본문은 저장하지 않습니다. 녹음·가사 버전 ID 는 불변이며 수정은 새 ID 로 합니다."
    return result


def _import_in_transaction(
    conn: sqlite3.Connection, registered_by: str, recordings: tuple[RecordingSpec, ...], lyric_versions: tuple[LyricVersionSpec, ...]
) -> dict[str, Any]:
    now = utc_now()
    registered_rec: list[str] = []
    unchanged_rec: list[str] = []
    for index, rec in enumerate(recordings):
        existing = fetch_recording(conn, rec.recording_id)
        if existing is not None:
            if existing["content_sha256"] == rec.content_sha256():
                unchanged_rec.append(rec.recording_id)
                continue
            raise InputError(
                f"녹음 '{rec.recording_id}' 이(가) 다른 내용으로 이미 등록되어 있습니다. 녹음 등록은 불변이므로 수정본은 새 recording_id 로 등록하세요",
                code="recording_immutable",
                details=[{"field": f"recordings[{index}]", "recording_id": rec.recording_id, "differences": _diff(existing, rec.identity())}],
            )
        _check_recording_refs(conn, rec, index)
        conn.execute(
            """
            INSERT INTO recordings (recording_id, title, artist, version_kind, version_label, original_recording_id,
                                    release_precision, release_date, vocal_type, data_mode, source_id, provider_track_ids,
                                    notes, content_sha256, registered_by, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.recording_id, rec.title, rec.artist, rec.version_kind, rec.version_label, rec.original_recording_id,
                rec.release_precision, rec.release_date, rec.vocal_type, rec.data_mode, rec.source_id,
                json.dumps(list(rec.provider_track_ids), ensure_ascii=False), rec.notes, rec.content_sha256(), registered_by, now,
            ),
        )
        registered_rec.append(rec.recording_id)

    registered_lyr: list[str] = []
    unchanged_lyr: list[str] = []
    for index, lyr in enumerate(lyric_versions):
        existing = fetch_lyric_version(conn, lyr.lyric_version_id)
        if existing is not None:
            if existing["content_sha256"] == lyr.content_sha256():
                unchanged_lyr.append(lyr.lyric_version_id)
                continue
            raise InputError(
                f"가사 버전 '{lyr.lyric_version_id}' 이(가) 다른 내용으로 이미 등록되어 있습니다. 수정본은 새 lyric_version_id 로 등록하세요",
                code="lyric_version_immutable",
                details=[{"field": f"lyric_versions[{index}]", "lyric_version_id": lyr.lyric_version_id, "differences": _diff(existing, lyr.identity())}],
            )
        _check_lyric_refs(conn, lyr, index)
        conn.execute(
            """
            INSERT INTO lyric_versions (lyric_version_id, recording_id, status, language, source_id, reference, notes,
                                        content_sha256, registered_by, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (lyr.lyric_version_id, lyr.recording_id, lyr.status, lyr.language, lyr.source_id, lyr.reference, lyr.notes,
             lyr.content_sha256(), registered_by, now),
        )
        registered_lyr.append(lyr.lyric_version_id)

    return {
        "command": "import-recordings",
        "ok": True,
        "registered_by": registered_by,
        "recordings": {"registered": registered_rec, "unchanged": unchanged_rec},
        "lyric_versions": {"registered": registered_lyr, "unchanged": unchanged_lyr},
        "message": (
            f"녹음 {len(registered_rec)}건·가사 버전 {len(registered_lyr)}건 등록"
            + (f", 동일 내용 {len(unchanged_rec) + len(unchanged_lyr)}건은 변경 없음" if unchanged_rec or unchanged_lyr else "")
        ),
    }


def _check_recording_refs(conn: sqlite3.Connection, rec: RecordingSpec, index: int) -> None:
    prefix = f"recordings[{index}]"
    if rec.original_recording_id is not None:
        original = fetch_recording(conn, rec.original_recording_id)
        if original is None:
            raise InputError(
                f"{prefix}: original_recording_id '{rec.original_recording_id}' 이(가) 등록되어 있지 않습니다 (파일 안에서는 앞쪽에 먼저 적으세요)",
                code="recording_not_found",
                details=[{"field": f"{prefix}.original_recording_id", "recording_id": rec.original_recording_id}],
            )
        if original["data_mode"] != rec.data_mode:
            raise InputError(
                f"{prefix}: 원곡 '{rec.original_recording_id}' 의 data_mode({original['data_mode']}) 와 다릅니다. 가상·실제 자료를 섞지 않습니다",
                code="data_mode_mismatch",
                details=[{"field": f"{prefix}.original_recording_id"}],
            )
    if rec.source_id is not None:
        source = fetch_source(conn, rec.source_id)
        if source is None:
            raise InputError(
                f"{prefix}: 출처 '{rec.source_id}' 이(가) 등록되어 있지 않습니다",
                code="source_not_registered",
                details=[{"field": f"{prefix}.source_id", "source_id": rec.source_id}],
            )
        try:
            check_source_gate(source, data_mode=rec.data_mode, operations=STORE_OPERATIONS, kinds=("metadata", "chart"))
        except InputError as exc:
            raise InputError(f"{prefix}: {exc.message}", code=exc.code, details=[{"field": f"{prefix}.source_id"}, *exc.details]) from None


def _check_lyric_refs(conn: sqlite3.Connection, lyr: LyricVersionSpec, index: int) -> None:
    prefix = f"lyric_versions[{index}]"
    recording = fetch_recording(conn, lyr.recording_id)
    if recording is None:
        raise InputError(
            f"{prefix}: 녹음 '{lyr.recording_id}' 이(가) 등록되어 있지 않습니다",
            code="recording_not_found",
            details=[{"field": f"{prefix}.recording_id", "recording_id": lyr.recording_id}],
        )
    if recording["vocal_type"] == "instrumental" and lyr.status != "not_applicable":
        raise InputError(
            f"{prefix}: 연주곡(instrumental) 녹음 '{lyr.recording_id}' 의 가사 버전은 not_applicable 만 허용됩니다 (현재: {lyr.status})",
            code="lyric_status_conflict",
            details=[{"field": f"{prefix}.status", "vocal_type": recording["vocal_type"]}],
        )
    if recording["vocal_type"] == "lyrical" and lyr.status == "not_applicable":
        raise InputError(
            f"{prefix}: 가창곡(lyrical) 녹음 '{lyr.recording_id}' 에는 not_applicable 을 쓸 수 없습니다. 가사를 못 구했으면 missing 을 쓰세요",
            code="lyric_status_conflict",
            details=[{"field": f"{prefix}.status", "vocal_type": recording["vocal_type"]}],
        )
    if lyr.source_id is not None:
        source = fetch_source(conn, lyr.source_id)
        if source is None:
            raise InputError(
                f"{prefix}: 출처 '{lyr.source_id}' 이(가) 등록되어 있지 않습니다",
                code="source_not_registered",
                details=[{"field": f"{prefix}.source_id", "source_id": lyr.source_id}],
            )
        if source["data_mode"] != recording["data_mode"]:
            raise InputError(
                f"{prefix}: 출처 '{lyr.source_id}' 의 data_mode({source['data_mode']}) 가 녹음의 data_mode({recording['data_mode']}) 와 다릅니다",
                code="data_mode_mismatch",
                details=[{"field": f"{prefix}.source_id"}],
            )
        if lyr.status in LYRIC_STATUSES_WITH_ACCESS:
            try:
                check_source_gate(source, data_mode=recording["data_mode"], operations=STORE_OPERATIONS, kinds=("lyrics",))
            except InputError as exc:
                raise InputError(f"{prefix}: {exc.message}", code=exc.code, details=[{"field": f"{prefix}.source_id"}, *exc.details]) from None
