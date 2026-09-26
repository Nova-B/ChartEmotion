"""`import-chart <csv> --batch <json> [--revision N]`.

흐름: 배치 설정 검증 → CSV 전체 검증 → 작업 폴더 열기 → 트랜잭션 하나 안에서
출처 허용 검사 → 차트 정의 생성/대조 → 스냅샷 동일성·개정 판정 → 스냅샷·항목 삽입.
어느 단계에서든 실패하면 롤백되어 출처·차트·스냅샷·항목 어느 것도 남지 않는다.

동일성 규칙:
- 같은 관측 (chart_id, period_start, period_end) 에 대해 content_sha256 과 config_sha256 이 모두 같은
  스냅샷이 있으면 no-op (이미 반영됨).
- 다르면 기존 스냅샷을 덮어쓰지 않는다. `--revision <최신+1>` 을 명시해야 새 불변 스냅샷을 추가한다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..domain.batch import BatchConfig, load_batch_config
from ..errors import InputError
from ..importers.chart_csv import ChartCsvResult, parse_chart_csv
from ..storage.database import open_workspace, utc_now
from .sources import check_source_gate, fetch_source

IMPORT_OPERATIONS: tuple[str, ...] = ("import", "store")


def import_chart(workspace: Path, csv_path: Path, batch_path: Path, *, revision: int | None = None) -> dict[str, Any]:
    batch = load_batch_config(Path(batch_path))
    parsed = parse_chart_csv(Path(csv_path), expected_n=batch.expected_n)
    if revision is not None and (isinstance(revision, bool) or revision < 1):
        raise InputError("--revision 은 1 이상의 정수여야 합니다", code="revision_invalid")

    conn = open_workspace(Path(workspace))
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = _import_in_transaction(conn, batch, parsed, revision)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()
    result["notice"] = _notice(batch.data_mode)
    return result


def _notice(data_mode: str) -> str:
    if data_mode == "synthetic":
        return "가상 자료(synthetic) 배치입니다. 실제 차트가 아니며 연구 결과로 제시하지 않습니다."
    return "실제 자료(real) 배치입니다. 출처의 허용 범위 안에서만 보관·분석하세요."


def _import_in_transaction(
    conn: sqlite3.Connection, batch: BatchConfig, parsed: ChartCsvResult, revision: int | None
) -> dict[str, Any]:
    source = fetch_source(conn, batch.source_id)
    if source is None:
        raise InputError(
            f"출처 '{batch.source_id}' 이(가) 등록되어 있지 않습니다. `chart-emotion register-source` 로 먼저 등록하세요",
            code="source_not_registered",
        )
    check_source_gate(source, data_mode=batch.data_mode, operations=IMPORT_OPERATIONS)

    chart_created = _ensure_chart_definition(conn, batch)

    config_hash = batch.config_sha256()
    existing = conn.execute(
        "SELECT * FROM chart_snapshots WHERE chart_id = ? AND period_start = ? AND period_end = ? ORDER BY revision",
        (batch.chart_id, batch.period_start.isoformat(), batch.period_end.isoformat()),
    ).fetchall()

    for row in existing:
        if row["content_sha256"] == parsed.content_sha256 and row["config_sha256"] == config_hash:
            latest = existing[-1]["revision"]
            return {
                "command": "import-chart",
                "ok": True,
                "status": "already_imported",
                "snapshot_id": row["snapshot_id"],
                "revision": row["revision"],
                "latest_revision": latest,
                "is_latest_revision": row["revision"] == latest,
                "snapshot_status": row["status"],
                "entries_inserted": 0,
                "chart_created": chart_created,
                "message": f"동일한 내용과 설정이 이미 스냅샷 '{row['snapshot_id']}' 로 반영되어 있어 새 행을 만들지 않았습니다",
            }

    if not existing:
        if revision is not None and revision != 1:
            raise InputError(
                f"이 관측 기간의 첫 스냅샷은 revision 1 이어야 합니다 (요청: {revision})",
                code="revision_mismatch",
                details=[{"expected": 1, "requested": revision}],
            )
        new_revision = 1
    else:
        latest_row = existing[-1]
        latest = latest_row["revision"]
        differences = _describe_differences(latest_row, parsed, config_hash, batch)
        if revision is None:
            raise InputError(
                f"같은 관측 기간({batch.chart_id} {batch.period_start.isoformat()}~{batch.period_end.isoformat()})의 "
                f"스냅샷 '{latest_row['snapshot_id']}' (revision {latest}) 와 내용 또는 설정이 다릅니다. "
                f"기존 스냅샷은 덮어쓰지 않습니다. 정정본을 새 스냅샷으로 추가하려면 --revision {latest + 1} 을 지정하세요",
                code="snapshot_revision_required",
                details=[
                    {
                        "existing_snapshot_id": latest_row["snapshot_id"],
                        "latest_revision": latest,
                        "next_revision": latest + 1,
                        "differences": differences,
                    }
                ],
            )
        if revision != latest + 1:
            raise InputError(
                f"--revision 은 최신 revision {latest} 의 다음 값 {latest + 1} 이어야 합니다 (요청: {revision})",
                code="revision_mismatch",
                details=[{"expected": latest + 1, "requested": revision, "latest_snapshot_id": latest_row["snapshot_id"]}],
            )
        new_revision = revision

    snapshot_id = batch.snapshot_id_for(new_revision)
    clash = conn.execute("SELECT chart_id, period_start, period_end FROM chart_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
    if clash is not None:
        raise InputError(
            f"snapshot_id '{snapshot_id}' 이(가) 다른 관측({clash['chart_id']} {clash['period_start']}~{clash['period_end']})에 이미 쓰이고 있습니다. "
            "배치 설정의 snapshot_key 를 바꾸세요",
            code="snapshot_id_conflict",
        )

    status = "complete" if parsed.is_complete else "incomplete"
    now = utc_now()
    conn.execute(
        """
        INSERT INTO chart_snapshots (snapshot_id, chart_id, source_id, period_start, period_end, display_year,
                                     methodology_version, expected_n, data_mode, revision, status, csv_filename,
                                     file_sha256, content_sha256, config_sha256, notes, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            batch.chart_id,
            batch.source_id,
            batch.period_start.isoformat(),
            batch.period_end.isoformat(),
            batch.display_year,
            batch.methodology_version,
            batch.expected_n,
            batch.data_mode,
            new_revision,
            status,
            parsed.filename,
            parsed.file_sha256,
            parsed.content_sha256,
            config_hash,
            batch.notes,
            now,
        ),
    )
    conn.executemany(
        "INSERT INTO chart_entries (snapshot_id, rank, title, artist, provider_track_id) VALUES (?, ?, ?, ?, ?)",
        [(snapshot_id, e.rank, e.title, e.artist, e.provider_track_id) for e in parsed.entries],
    )

    return {
        "command": "import-chart",
        "ok": True,
        "status": "imported",
        "snapshot_id": snapshot_id,
        "revision": new_revision,
        "latest_revision": new_revision,
        "is_latest_revision": True,
        "snapshot_status": status,
        "chart_id": batch.chart_id,
        "chart_created": chart_created,
        "source_id": batch.source_id,
        "data_mode": batch.data_mode,
        "period_start": batch.period_start.isoformat(),
        "period_end": batch.period_end.isoformat(),
        "display_year": batch.display_year,
        "expected_n": batch.expected_n,
        "entries_inserted": len(parsed.entries),
        "missing_ranks": list(parsed.missing_ranks),
        "csv_filename": parsed.filename,
        "csv_had_bom": parsed.had_bom,
        "file_sha256": parsed.file_sha256,
        "content_sha256": parsed.content_sha256,
        "config_sha256": config_hash,
        "config_identity": batch.identity(),
        "message": (
            f"스냅샷 '{snapshot_id}' 에 {len(parsed.entries)}개 항목을 반영했습니다"
            + (f" (누락 순위 {list(parsed.missing_ranks)} → incomplete, 비교 실행 차단)" if parsed.missing_ranks else "")
        ),
    }


def _ensure_chart_definition(conn: sqlite3.Connection, batch: BatchConfig) -> bool:
    row = conn.execute("SELECT * FROM chart_definitions WHERE chart_id = ?", (batch.chart_id,)).fetchone()
    if row is None:
        if batch.chart is None:
            raise InputError(
                f"차트 '{batch.chart_id}' 이(가) 등록되어 있지 않습니다. 첫 가져오기 배치에는 chart 블록"
                "(name, market, metric, periodicity)을 포함하세요",
                code="chart_not_registered",
            )
        conn.execute(
            "INSERT INTO chart_definitions (chart_id, name, market, metric, periodicity, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (batch.chart_id, batch.chart.name, batch.chart.market, batch.chart.metric, batch.chart.periodicity, utc_now()),
        )
        return True
    if batch.chart is not None:
        mismatches = [
            {"field": field, "stored": row[field], "requested": getattr(batch.chart, field)}
            for field in ("market", "metric", "periodicity")
            if row[field] != getattr(batch.chart, field)
        ]
        if mismatches:
            raise InputError(
                f"배치의 chart 블록이 등록된 차트 '{batch.chart_id}' 정의와 다릅니다. 차트 정의는 바꾸지 않습니다. 다른 차트라면 새 chart_id 를 쓰세요",
                code="chart_definition_mismatch",
                details=mismatches,
            )
    return False


def _describe_differences(row: sqlite3.Row, parsed: ChartCsvResult, config_hash: str, batch: BatchConfig) -> dict[str, Any]:
    diff: dict[str, Any] = {
        "content_differs": row["content_sha256"] != parsed.content_sha256,
        "config_differs": row["config_sha256"] != config_hash,
    }
    if diff["config_differs"]:
        stored = {
            "source_id": row["source_id"],
            "chart_id": row["chart_id"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "display_year": row["display_year"],
            "methodology_version": row["methodology_version"],
            "expected_n": row["expected_n"],
            "data_mode": row["data_mode"],
        }
        requested = batch.identity()
        diff["config_fields"] = [
            {"field": key, "stored": stored[key], "requested": requested[key]} for key in stored if stored[key] != requested[key]
        ]
    return json.loads(json.dumps(diff, ensure_ascii=False))
