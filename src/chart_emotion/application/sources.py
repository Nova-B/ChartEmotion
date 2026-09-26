"""출처(source) 등록과 허용 검사.

원칙:
- 가상(synthetic) 출처와 실제(real) 출처를 명시적으로 구분한다.
- 실제 출처의 `permitted` 상태는 확인일과 근거가 있어야만 등록된다. 프로그램이 허락을 지어내지 않는다.
- 허용 작업(import / store / analyze)은 permitted 상태에서만 부여한다.
- synthetic 배치는 synthetic 출처만, real 배치는 real 출처만 쓸 수 있다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.contracts import (
    DATA_MODES,
    SOURCE_KINDS,
    SOURCE_OPERATIONS,
    SOURCE_STATUSES,
    is_valid_identifier,
)
from ..domain.validation import parse_iso_date
from ..errors import InputError
from ..storage.database import open_workspace, utc_now

SYNTHETIC_DEMO_SOURCE_ID = "synthetic-demo"


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    name: str
    kind: str
    data_mode: str
    status: str
    allowed_operations: tuple[str, ...]
    provider: str | None = None
    url: str | None = None
    checked_on: str | None = None
    checked_by: str | None = None
    evidence: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "kind": self.kind,
            "data_mode": self.data_mode,
            "status": self.status,
            "allowed_operations": list(self.allowed_operations),
            "provider": self.provider,
            "url": self.url,
            "checked_on": self.checked_on,
            "checked_by": self.checked_by,
            "evidence": self.evidence,
            "notes": self.notes,
        }


SYNTHETIC_DEMO_SOURCE = SourceSpec(
    source_id=SYNTHETIC_DEMO_SOURCE_ID,
    name="가상 시연 자료 (합성 데이터)",
    kind="chart",
    data_mode="synthetic",
    status="permitted",
    allowed_operations=SOURCE_OPERATIONS,
    provider="chart-emotion 예제",
    url=None,
    checked_on=None,
    checked_by="chart-emotion init",
    evidence="이 저장소의 examples/synthetic 에 포함된, 직접 작성한 가상 자료. 실제 차트·실제 곡·실제 가사가 아니다",
    notes="실제 공급자와 무관한 합성 데이터. 연구 결과로 제시하지 않는다",
)


def validate_source_spec(spec: SourceSpec) -> None:
    details: list[dict[str, Any]] = []

    def add(field: str, code: str, message: str) -> None:
        details.append({"field": field, "code": code, "message": message})

    if not is_valid_identifier(spec.source_id):
        add("source_id", "invalid_identifier", "영문·숫자로 시작하는 영문·숫자·'.'·'_'·'-' 문자열이어야 합니다")
    if not spec.name or not spec.name.strip():
        add("name", "empty_value", "표시 이름이 필요합니다")
    if spec.kind not in SOURCE_KINDS:
        add("kind", "invalid_enum", f"허용 값: {', '.join(SOURCE_KINDS)}")
    if spec.data_mode not in DATA_MODES:
        add("data_mode", "invalid_enum", f"허용 값: {', '.join(DATA_MODES)}")
    if spec.status not in SOURCE_STATUSES:
        add("status", "invalid_enum", f"허용 값: {', '.join(SOURCE_STATUSES)}")
    unknown_ops = [op for op in spec.allowed_operations if op not in SOURCE_OPERATIONS]
    if unknown_ops:
        add("allowed_operations", "invalid_enum", f"허용 작업은 {', '.join(SOURCE_OPERATIONS)} 중에서 골라야 합니다 (현재: {unknown_ops})")
    if len(set(spec.allowed_operations)) != len(spec.allowed_operations):
        add("allowed_operations", "duplicate_items", "허용 작업이 중복됩니다")
    if spec.checked_on is not None and parse_iso_date(spec.checked_on) is None:
        add("checked_on", "invalid_date", "YYYY-MM-DD 형식이어야 합니다")

    if spec.status != "permitted" and spec.allowed_operations:
        add(
            "allowed_operations",
            "grant_requires_permitted",
            f"상태가 '{spec.status}' 인 출처에는 허용 작업을 부여할 수 없습니다. 먼저 이용 조건을 확인하고 permitted 로 등록하세요",
        )
    if spec.data_mode == "real" and spec.status == "permitted":
        if not spec.checked_on:
            add("checked_on", "evidence_required", "실제 자료를 permitted 로 등록하려면 이용 조건 확인일(--checked-on)이 필요합니다")
        if not spec.evidence or not spec.evidence.strip():
            add("evidence", "evidence_required", "실제 자료를 permitted 로 등록하려면 허락 근거(--evidence)가 필요합니다")
        if not spec.allowed_operations:
            add("allowed_operations", "grant_required", "permitted 상태에는 허용 작업을 하나 이상 지정해야 합니다 (--allow)")

    if details:
        raise InputError("출처 등록 값에 오류가 있습니다", code="source_spec_invalid", details=details)


def row_to_source(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["allowed_operations"] = json.loads(data["allowed_operations"])
    return data


def fetch_source(conn: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
    return row_to_source(row) if row is not None else None


def list_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [row_to_source(row) for row in conn.execute("SELECT * FROM sources ORDER BY source_id")]


def _insert_source(conn: sqlite3.Connection, spec: SourceSpec, *, registered_at: str | None = None) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO sources (source_id, name, kind, data_mode, provider, url, status, allowed_operations,
                             checked_on, checked_by, evidence, notes, registered_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            name = excluded.name, kind = excluded.kind, data_mode = excluded.data_mode,
            provider = excluded.provider, url = excluded.url, status = excluded.status,
            allowed_operations = excluded.allowed_operations, checked_on = excluded.checked_on,
            checked_by = excluded.checked_by, evidence = excluded.evidence, notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (
            spec.source_id,
            spec.name,
            spec.kind,
            spec.data_mode,
            spec.provider,
            spec.url,
            spec.status,
            json.dumps(list(spec.allowed_operations)),
            spec.checked_on,
            spec.checked_by,
            spec.evidence,
            spec.notes,
            registered_at or now,
            now,
        ),
    )


def _spec_matches_row(spec: SourceSpec, row: dict[str, Any]) -> bool:
    current = {k: v for k, v in row.items() if k not in ("registered_at", "updated_at")}
    wanted = spec.to_dict()
    return current == wanted


def ensure_synthetic_demo_source(conn: sqlite3.Connection) -> bool:
    """init 이 부르는 가상 출처 시드. 이미 있으면 손대지 않는다. 반환: 새로 넣었는지."""
    if fetch_source(conn, SYNTHETIC_DEMO_SOURCE_ID) is not None:
        return False
    conn.execute("BEGIN IMMEDIATE")
    try:
        _insert_source(conn, SYNTHETIC_DEMO_SOURCE)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return True


def register_source(workspace: Path, spec: SourceSpec, *, replace_existing: bool = False) -> dict[str, Any]:
    """출처를 등록한다. 같은 내용이면 no-op, 다른 내용이면 --replace 없이는 거부."""
    validate_source_spec(spec)
    conn = open_workspace(workspace)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = fetch_source(conn, spec.source_id)
            if existing is not None and _spec_matches_row(spec, existing):
                conn.execute("COMMIT")
                return {"command": "register-source", "status": "unchanged", "source": existing}
            if existing is not None and not replace_existing:
                raise InputError(
                    f"출처 '{spec.source_id}' 이(가) 다른 내용으로 이미 등록되어 있습니다. 덮어쓰려면 --replace 를 지정하세요",
                    code="source_exists",
                    details=[{"existing": existing, "requested": spec.to_dict()}],
                )
            if existing is not None and existing["data_mode"] != spec.data_mode:
                raise InputError(
                    f"출처 '{spec.source_id}' 의 data_mode 를 {existing['data_mode']} 에서 {spec.data_mode} 로 바꿀 수 없습니다. 새 source_id 를 쓰세요",
                    code="source_data_mode_immutable",
                )
            _insert_source(conn, spec, registered_at=existing["registered_at"] if existing else None)
            stored = fetch_source(conn, spec.source_id)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()
    return {"command": "register-source", "status": "updated" if existing else "registered", "source": stored}


def source_issues(source: dict[str, Any]) -> list[dict[str, Any]]:
    """validate 가 보여줄 출처 상태 메모. 오류가 아니라 상태 설명이다."""
    issues: list[dict[str, Any]] = []
    sid = source["source_id"]
    if source["data_mode"] == "real":
        if source["status"] == "pending":
            issues.append({"severity": "info", "code": "source_pending", "source_id": sid, "message": "실제 출처의 이용 조건이 미확인 상태(pending)입니다. 가져오기·분석이 차단됩니다"})
        elif source["status"] == "restricted":
            issues.append({"severity": "info", "code": "source_restricted", "source_id": sid, "message": "실제 출처가 restricted 상태입니다. 가져오기·분석이 차단됩니다"})
        elif source["status"] == "permitted":
            if not source.get("checked_on") or not source.get("evidence"):
                issues.append({"severity": "error", "code": "source_evidence_missing", "source_id": sid, "message": "permitted 실제 출처에 확인일 또는 근거가 없습니다"})
            missing = [op for op in SOURCE_OPERATIONS if op not in source["allowed_operations"]]
            if missing:
                issues.append({"severity": "info", "code": "source_partial_grant", "source_id": sid, "message": f"허용되지 않은 작업: {', '.join(missing)}"})
    return issues


def check_source_gate(
    source: dict[str, Any],
    *,
    data_mode: str,
    operations: tuple[str, ...],
    kinds: tuple[str, ...] = ("chart",),
) -> None:
    """배치·실험·녹음·가사 등록이 이 출처로 해당 작업을 해도 되는지 검사한다. 안 되면 InputError."""
    reasons: list[dict[str, Any]] = []
    sid = source["source_id"]
    if source["kind"] not in kinds:
        reasons.append({"code": "source_kind_mismatch", "message": f"출처 '{sid}' 의 종류는 '{source['kind']}' 이며 여기서는 {', '.join(kinds)} 출처만 쓸 수 있습니다"})
    if data_mode == "synthetic" and source["data_mode"] != "synthetic":
        reasons.append({"code": "synthetic_requires_synthetic_source", "message": f"synthetic 모드에서는 synthetic 출처만 쓸 수 있습니다. '{sid}' 는 {source['data_mode']} 출처입니다"})
    if data_mode == "real" and source["data_mode"] != "real":
        reasons.append({"code": "real_requires_real_source", "message": f"real 모드에서는 real 출처만 쓸 수 있습니다. '{sid}' 는 {source['data_mode']} 출처입니다"})
    if source["status"] != "permitted":
        reasons.append({"code": "source_status_not_permitted", "message": f"출처 '{sid}' 의 상태가 '{source['status']}' 입니다. 이용 조건을 확인하고 permitted 로 등록하기 전에는 진행하지 않습니다"})
    missing_ops = [op for op in operations if op not in source["allowed_operations"]]
    if missing_ops:
        reasons.append({"code": "operation_not_granted", "message": f"출처 '{sid}' 에 허용되지 않은 작업: {', '.join(missing_ops)} (허용: {', '.join(source['allowed_operations']) or '없음'})"})
    if source["data_mode"] == "real" and (not source.get("checked_on") or not source.get("evidence")):
        reasons.append({"code": "source_evidence_missing", "message": f"실제 출처 '{sid}' 에 확인일·근거가 없습니다"})
    if reasons:
        raise InputError(
            f"출처 '{sid}' 로는 요청한 작업({', '.join(operations)})을 진행할 수 없습니다",
            code="source_not_permitted",
            details=reasons,
        )


def spec_from_cli(
    *,
    source_id: str,
    name: str,
    kind: str,
    data_mode: str,
    status: str | None,
    allow: list[str] | None,
    provider: str | None,
    url: str | None,
    checked_on: str | None,
    checked_by: str | None,
    evidence: str | None,
    notes: str | None,
) -> SourceSpec:
    """CLI 인자를 SourceSpec 으로. 기본값: synthetic 은 permitted+전체 작업, real 은 pending+작업 없음."""
    if status is None:
        status = "permitted" if data_mode == "synthetic" else "pending"
    if allow is None:
        ops: tuple[str, ...] = SOURCE_OPERATIONS if (data_mode == "synthetic" and status == "permitted") else ()
    else:
        ops = tuple(allow)
    return SourceSpec(
        source_id=source_id,
        name=name,
        kind=kind,
        data_mode=data_mode,
        status=status,
        allowed_operations=ops,
        provider=provider,
        url=url,
        checked_on=checked_on,
        checked_by=checked_by,
        evidence=evidence,
        notes=notes,
    )
