"""`validate [--experiment <json>]`.

작업 폴더 전체 검증: 스키마·외래키, 출처 상태, 차트, 스냅샷별 항목 수·누락 순위·개정 상태, 합계.
실험 검증: 설정 구조 검증 후 참조 스냅샷의 존재·완전성·비교 가능성(같은 차트, 같은 N, 같은 산정 방식,
같은 기간 길이, 서로 다른 기간)을 점검한다.

이 단계는 곡 매칭·가사·라벨을 다루지 않으며 어떤 정서 수치도 계산하지 않는다.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from .. import __version__
from ..domain.contracts import LABELS, LABELSET_VERSION
from ..domain.experiment import ExperimentConfig, load_experiment_config
from ..errors import InputError
from ..storage.database import SCHEMA_VERSION, database_path, foreign_keys_enabled, integrity_check, open_workspace
from .sources import check_source_gate, fetch_source, list_sources, source_issues

NOT_IMPLEMENTED_SECTIONS: dict[str, Any] = {
    "matching": {
        "status": "not_implemented",
        "message": "곡·버전 매칭(D05)은 아직 구현되지 않았습니다. 제목·아티스트 문자열이 같아도 같은 곡으로 병합하지 않습니다",
    },
    "lyrics": {"status": "not_implemented", "message": "가사 확보(D05~)는 구현되지 않았습니다. 가사를 저장하지 않습니다"},
    "labeling": {
        "status": "not_analyzed",
        "labelset_version": LABELSET_VERSION,
        "labels": list(LABELS),
        "message": "라벨 판정(D06~)은 수행되지 않았습니다. 모든 항목은 미검토 상태이며 '없음'으로 간주하지 않습니다",
    },
    "analysis": {
        "status": "not_performed",
        "message": "집계·비교·정서 수치(D08~)는 계산하지 않았습니다. 이 출력에는 어떤 심리 점수도 없습니다",
    },
}


def _mode_notice(modes: set[str]) -> str:
    if not modes:
        return "저장된 스냅샷이 없습니다."
    if modes == {"synthetic"}:
        return "가상 자료(synthetic)만 저장되어 있습니다. 실제 차트 분석 결과가 아닙니다."
    if modes == {"real"}:
        return "실제 자료(real) 스냅샷이 저장되어 있습니다. 출처 허용 범위를 확인하세요."
    return "가상 자료와 실제 자료가 함께 저장되어 있습니다. 실험에서 두 모드를 섞지 마세요."


def _load_snapshots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM chart_snapshots ORDER BY chart_id, period_start, period_end, revision"
    ).fetchall()
    latest: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (row["chart_id"], row["period_start"], row["period_end"])
        latest[key] = max(latest.get(key, 0), row["revision"])
    result: list[dict[str, Any]] = []
    for row in rows:
        ranks = [r[0] for r in conn.execute("SELECT rank FROM chart_entries WHERE snapshot_id = ? ORDER BY rank", (row["snapshot_id"],))]
        present = set(ranks)
        missing = [rank for rank in range(1, row["expected_n"] + 1) if rank not in present]
        distinct = conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT title, artist, provider_track_id FROM chart_entries WHERE snapshot_id = ?)",
            (row["snapshot_id"],),
        ).fetchone()[0]
        key = (row["chart_id"], row["period_start"], row["period_end"])
        result.append(
            {
                "snapshot_id": row["snapshot_id"],
                "chart_id": row["chart_id"],
                "source_id": row["source_id"],
                "data_mode": row["data_mode"],
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "display_year": row["display_year"],
                "methodology_version": row["methodology_version"],
                "expected_n": row["expected_n"],
                "revision": row["revision"],
                "is_latest_revision": row["revision"] == latest[key],
                "status": row["status"],
                "entry_count": len(ranks),
                "missing_ranks": missing,
                "distinct_title_artist_track_strings": distinct,
                "csv_filename": row["csv_filename"],
                "file_sha256": row["file_sha256"],
                "content_sha256": row["content_sha256"],
                "config_sha256": row["config_sha256"],
                "imported_at": row["imported_at"],
            }
        )
    return result


def validate_workspace(workspace: Path) -> dict[str, Any]:
    workspace = Path(workspace)
    conn = open_workspace(workspace)
    try:
        conn.execute("BEGIN")  # 읽기 트랜잭션 안에서 일관된 상태를 본다
        try:
            fk_on = foreign_keys_enabled(conn)
            fk_violations = integrity_check(conn)
            sources = list_sources(conn)
            charts = [dict(row) for row in conn.execute("SELECT * FROM chart_definitions ORDER BY chart_id")]
            snapshots = _load_snapshots(conn)
            entries_all = conn.execute("SELECT COUNT(*) FROM chart_entries").fetchone()[0]
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()

    issues: list[dict[str, Any]] = []
    if not fk_on:
        issues.append({"severity": "error", "code": "foreign_keys_off", "message": "외래키 검사가 꺼져 있습니다"})
    for violation in fk_violations:
        issues.append({"severity": "error", "code": "foreign_key_violation", "message": violation})
    for source in sources:
        issues.extend(source_issues(source))
    source_ids = {s["source_id"] for s in sources}
    latest_entry_total = 0
    for snap in snapshots:
        if snap["status"] == "incomplete" or snap["missing_ranks"]:
            issues.append(
                {
                    "severity": "warning",
                    "code": "snapshot_incomplete",
                    "snapshot_id": snap["snapshot_id"],
                    "missing_ranks": snap["missing_ranks"],
                    "message": f"스냅샷 '{snap['snapshot_id']}' 에 누락 순위 {snap['missing_ranks']} 가 있어 비교 실행이 차단됩니다",
                }
            )
        if snap["status"] == "complete" and snap["missing_ranks"]:
            issues.append({"severity": "error", "code": "snapshot_status_inconsistent", "snapshot_id": snap["snapshot_id"], "message": "complete 로 저장된 스냅샷에 누락 순위가 있습니다"})
        if not snap["is_latest_revision"]:
            issues.append({"severity": "info", "code": "snapshot_superseded", "snapshot_id": snap["snapshot_id"], "message": f"스냅샷 '{snap['snapshot_id']}' 보다 새로운 revision 이 있습니다. 보존되지만 기본 비교 대상은 최신 revision 입니다"})
        else:
            latest_entry_total += snap["entry_count"]
        if snap["source_id"] not in source_ids:
            issues.append({"severity": "error", "code": "snapshot_source_missing", "snapshot_id": snap["snapshot_id"], "message": "스냅샷의 출처가 등록 목록에 없습니다"})

    modes = {snap["data_mode"] for snap in snapshots}
    has_error = any(issue["severity"] == "error" for issue in issues)
    return {
        "command": "validate",
        "scope": "workspace",
        "ok": not has_error,
        "app_version": __version__,
        "workspace": str(workspace.resolve()),
        "database": str(database_path(workspace).resolve()),
        "schema_version": SCHEMA_VERSION,
        "foreign_keys_enabled": fk_on,
        "data_mode_notice": _mode_notice(modes),
        "sources": sources,
        "charts": charts,
        "snapshots": snapshots,
        "totals": {
            "sources": len(sources),
            "charts": len(charts),
            "snapshots": len(snapshots),
            "snapshots_latest_revision": sum(1 for s in snapshots if s["is_latest_revision"]),
            "entries_all_revisions": entries_all,
            "entries_latest_revisions": latest_entry_total,
            "entries_note": "항목은 (snapshot_id, rank) 기본키로 세므로 같은 파일을 다시 넣어도 중복되지 않습니다",
        },
        "issues": issues,
        **NOT_IMPLEMENTED_SECTIONS,
    }


def _snapshot_summary(conn: sqlite3.Connection, snapshot_id: str) -> dict[str, Any] | None:
    for snap in _load_snapshots(conn):
        if snap["snapshot_id"] == snapshot_id:
            return snap
    return None


def validate_experiment(workspace: Path, experiment_path: Path) -> dict[str, Any]:
    workspace = Path(workspace)
    config = load_experiment_config(Path(experiment_path))
    conn = open_workspace(workspace)
    try:
        conn.execute("BEGIN")
        try:
            report = _validate_experiment_with_db(conn, config)
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()
    return {
        "command": "validate",
        "scope": "experiment",
        "ok": report["ok"],
        "app_version": __version__,
        "workspace": str(workspace.resolve()),
        "experiment_file": str(Path(experiment_path)),
        "data_mode_notice": (
            "가상 자료(synthetic) 실험 설정입니다. 실제 차트 분석이 아닙니다."
            if config.data_mode == "synthetic"
            else "실제 자료(real) 실험 설정입니다."
        ),
        "experiment": config.to_dict(),
        **report,
        **NOT_IMPLEMENTED_SECTIONS,
    }


def _validate_experiment_with_db(conn: sqlite3.Connection, config: ExperimentConfig) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []

    for sid in config.snapshot_ids:
        snap = _snapshot_summary(conn, sid)
        if snap is None:
            errors.append({"code": "snapshot_not_found", "snapshot_id": sid, "message": f"스냅샷 '{sid}' 이(가) 작업 폴더에 없습니다"})
            continue
        snapshots.append(snap)
        if snap["status"] != "complete" or snap["missing_ranks"]:
            errors.append(
                {
                    "code": "snapshot_incomplete",
                    "snapshot_id": sid,
                    "missing_ranks": snap["missing_ranks"],
                    "message": f"스냅샷 '{sid}' 은(는) 누락 순위 {snap['missing_ranks']} 가 있는 incomplete 상태라 비교에 쓸 수 없습니다. 정정 배치를 새 revision 으로 넣으세요",
                }
            )
        if snap["expected_n"] != config.top_n:
            errors.append({"code": "top_n_mismatch", "snapshot_id": sid, "message": f"실험 top_n={config.top_n} 과 스냅샷 expected_n={snap['expected_n']} 이 다릅니다"})
        elif snap["entry_count"] != config.top_n:
            errors.append({"code": "entry_count_mismatch", "snapshot_id": sid, "message": f"스냅샷 항목 수 {snap['entry_count']} 이 top_n={config.top_n} 과 다릅니다"})
        if snap["data_mode"] != config.data_mode:
            errors.append({"code": "data_mode_mismatch", "snapshot_id": sid, "message": f"실험 data_mode={config.data_mode} 과 스냅샷 data_mode={snap['data_mode']} 이 다릅니다. 가상·실제 자료를 섞지 않습니다"})
        if not snap["is_latest_revision"]:
            warnings.append({"code": "snapshot_superseded", "snapshot_id": sid, "message": f"스냅샷 '{sid}' 보다 새로운 revision 이 있습니다. 의도한 참조인지 확인하세요"})
        source = fetch_source(conn, snap["source_id"])
        if source is None:
            errors.append({"code": "source_not_registered", "snapshot_id": sid, "message": f"스냅샷 출처 '{snap['source_id']}' 이(가) 없습니다"})
        else:
            try:
                check_source_gate(source, data_mode=snap["data_mode"], operations=("analyze",))
            except InputError as exc:
                errors.append({"code": "source_not_permitted", "snapshot_id": sid, "source_id": snap["source_id"], "message": exc.message, "reasons": exc.details})

    comparability: dict[str, Any] = {}
    if len(snapshots) == 2:
        a, b = snapshots
        comparability["same_chart"] = a["chart_id"] == b["chart_id"]
        if not comparability["same_chart"]:
            errors.append({"code": "chart_mismatch", "message": f"두 스냅샷의 차트가 다릅니다 ({a['chart_id']} vs {b['chart_id']}). 같은 차트만 비교합니다"})
        else:
            row = conn.execute("SELECT periodicity FROM chart_definitions WHERE chart_id = ?", (a["chart_id"],)).fetchone()
            comparability["periodicity"] = row["periodicity"] if row else None

        same_period = (a["period_start"], a["period_end"]) == (b["period_start"], b["period_end"])
        comparability["distinct_periods"] = not same_period
        if same_period:
            errors.append({"code": "same_period", "message": "두 스냅샷이 같은 관측 기간입니다. 서로 다른 기간을 비교해야 합니다"})
        else:
            a_start, a_end = date.fromisoformat(a["period_start"]), date.fromisoformat(a["period_end"])
            b_start, b_end = date.fromisoformat(b["period_start"]), date.fromisoformat(b["period_end"])
            if a_start <= b_end and b_start <= a_end:
                warnings.append({"code": "periods_overlap", "message": "두 관측 기간이 겹칩니다. 의도한 비교인지 확인하세요"})
            len_a = (a_end - a_start).days + 1
            len_b = (b_end - b_start).days + 1
            comparability["period_length_days"] = [len_a, len_b]
            # 기획 계약: 기간 길이가 조금이라도 다르면(윤년 366일 vs 365일 포함) 기본 실행을 차단한다.
            if len_a != len_b:
                _blocked_unless_note(config, errors, warnings, "period_length_mismatch", f"두 기간 길이가 다릅니다 ({len_a}일 vs {len_b}일)")

        comparability["same_methodology"] = a["methodology_version"] == b["methodology_version"]
        if not comparability["same_methodology"]:
            _blocked_unless_note(
                config, errors, warnings, "methodology_mismatch",
                f"산정 방식 버전이 다릅니다 ({a['methodology_version']} vs {b['methodology_version']})",
            )
        comparability["same_top_n"] = a["expected_n"] == b["expected_n"] == config.top_n
        comparability["same_source"] = a["source_id"] == b["source_id"]
        if not comparability["same_source"]:
            warnings.append({"code": "different_sources", "message": "두 스냅샷의 출처가 다릅니다. 비교 가능성 검토 메모를 남기세요"})

        comparability["identical_string_rows_across_snapshots"] = conn.execute(
            """
            SELECT COUNT(*) FROM chart_entries x JOIN chart_entries y
              ON x.title = y.title AND x.artist = y.artist AND x.provider_track_id IS y.provider_track_id
            WHERE x.snapshot_id = ? AND y.snapshot_id = ?
            """,
            (a["snapshot_id"], b["snapshot_id"]),
        ).fetchone()[0]
        comparability["identical_string_rows_note"] = (
            "제목·아티스트·provider_track_id 문자열이 완전히 같은 행의 수입니다. 확정된 곡 매칭이 아니며 D05 에서 사람이 확정합니다"
        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "snapshots": snapshots,
        "comparability": comparability,
        "message": (
            "실험 설정과 스냅샷 참조가 비교 가능 조건을 충족합니다. 매칭·라벨링·집계는 아직 수행되지 않았습니다"
            if not errors
            else f"실험 설정에 {len(errors)}건의 오류가 있어 비교를 진행할 수 없습니다"
        ),
    }


def _blocked_unless_note(
    config: ExperimentConfig, errors: list[dict[str, Any]], warnings: list[dict[str, Any]], code: str, message: str
) -> None:
    if config.comparability_note:
        warnings.append({"code": code, "message": f"{message}. comparability_note 가 있어 진행을 허용하지만 보고서에 표시해야 합니다", "comparability_note": config.comparability_note})
    else:
        errors.append({"code": code, "message": f"{message}. 기본 실행을 차단합니다. 비교 가능성을 검토했다면 실험 설정에 comparability_note 를 남기세요"})
