"""`import-mappings <json>` 와 `show-mappings`: 차트 항목 ↔ 녹음(·가사 버전) 매핑 개정 이력.

- 매핑은 (snapshot_id, rank) 별 append-only revision 이다. 삭제·덮어쓰기는 없다.
- base_revision 은 그 항목의 현재 최신 revision(없으면 0)이어야 한다(낙관적 동시성). 하나라도 어긋나면 배치 전체를 거부한다.
- 최신 판정과 내용이 완전히 같은 재입력은 no-op 이다(base_revision 이 최신 revision 이거나 최신 판정의 base_revision 일 때).
  중간에 다른 revision 이 끼어 있으면 no-op 이 아니라 stale 오류다.
- candidate 는 확정이 아니다. 문자열·provider_track_id 가 같아도 프로그램이 confirmed 로 올리지 않는다.
- 매핑은 recording_id 와 lyric_version_id 를 함께 고정한다. 가사 버전은 그 녹음의 것이어야 한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..domain.mappings import MappingSpec, load_mappings_file
from ..errors import InputError
from ..storage.database import open_workspace, utc_now
from .recordings import fetch_lyric_version, fetch_recording

MAPPING_STATE_ORDER: tuple[str, ...] = ("confirmed", "candidate", "unresolved", "unmapped")


def mapping_id_for(snapshot_id: str, rank: int, revision: int) -> str:
    return f"{snapshot_id}.rank{rank}.r{revision}"


def _row_to_mapping(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def latest_mapping(conn: sqlite3.Connection, snapshot_id: str, rank: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM entry_mappings WHERE snapshot_id = ? AND rank = ? ORDER BY revision DESC LIMIT 1",
        (snapshot_id, rank),
    ).fetchone()
    return _row_to_mapping(row) if row is not None else None


def mapping_history(conn: sqlite3.Connection, snapshot_id: str, rank: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM entry_mappings WHERE snapshot_id = ? AND rank = ? ORDER BY revision", (snapshot_id, rank)
    ).fetchall()
    return [_row_to_mapping(r) for r in rows]


# ---------------------------------------------------------------------------
# import-mappings
# ---------------------------------------------------------------------------


def import_mappings(workspace: Path, path: Path) -> dict[str, Any]:
    spec = load_mappings_file(Path(path))
    conn = open_workspace(Path(workspace))
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = _import_in_transaction(conn, spec.mappings)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()
    result["reviewer_id"] = spec.reviewer_id
    result["notice"] = "매핑은 append-only 개정 이력입니다. candidate 는 확정이 아니며 라벨 집계에서는 미확정(U)으로 다룹니다."
    return result


def _import_in_transaction(conn: sqlite3.Connection, mappings: tuple[MappingSpec, ...]) -> dict[str, Any]:
    now = utc_now()
    appended: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for index, item in enumerate(mappings):
        prefix = f"mappings[{index}]"
        entry = conn.execute(
            "SELECT e.snapshot_id, e.rank, e.title, e.artist, s.data_mode FROM chart_entries e JOIN chart_snapshots s USING (snapshot_id) "
            "WHERE e.snapshot_id = ? AND e.rank = ?",
            (item.snapshot_id, item.rank),
        ).fetchone()
        if entry is None:
            raise InputError(
                f"{prefix}: 차트 항목 ({item.snapshot_id}, rank {item.rank}) 이(가) 없습니다",
                code="entry_not_found",
                details=[{"field": prefix, "snapshot_id": item.snapshot_id, "rank": item.rank}],
            )
        latest = latest_mapping(conn, item.snapshot_id, item.rank)
        current_revision = latest["revision"] if latest else 0

        if latest is not None and _same_content(latest, item):
            if item.base_revision in (latest["revision"], latest["base_revision"]):
                unchanged.append({"snapshot_id": item.snapshot_id, "rank": item.rank, "mapping_id": latest["mapping_id"], "revision": latest["revision"]})
                continue
        if item.base_revision != current_revision:
            raise InputError(
                f"{prefix}: ({item.snapshot_id}, rank {item.rank}) 의 base_revision={item.base_revision} 이 현재 최신 revision {current_revision} 과 다릅니다. "
                "최신 판정을 확인한 뒤 base_revision 을 맞춰 다시 제출하세요. 배치 전체를 반영하지 않았습니다",
                code="stale_base_revision",
                details=[
                    {
                        "field": f"{prefix}.base_revision",
                        "snapshot_id": item.snapshot_id,
                        "rank": item.rank,
                        "requested_base_revision": item.base_revision,
                        "current_revision": current_revision,
                        "current_state": latest["state"] if latest else "unmapped",
                        "current_recording_id": latest["recording_id"] if latest else None,
                    }
                ],
            )
        _check_targets(conn, item, entry["data_mode"], prefix)
        revision = current_revision + 1
        mapping_id = mapping_id_for(item.snapshot_id, item.rank, revision)
        conn.execute(
            """
            INSERT INTO entry_mappings (mapping_id, snapshot_id, rank, revision, base_revision, state, recording_id,
                                        lyric_version_id, reviewer_id, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (mapping_id, item.snapshot_id, item.rank, revision, item.base_revision, item.state, item.recording_id,
             item.lyric_version_id, item.reviewer_id, item.reason, now),
        )
        appended.append(
            {
                "snapshot_id": item.snapshot_id,
                "rank": item.rank,
                "mapping_id": mapping_id,
                "revision": revision,
                "state": item.state,
                "recording_id": item.recording_id,
                "lyric_version_id": item.lyric_version_id,
                "previous_state": latest["state"] if latest else "unmapped",
            }
        )
    return {
        "command": "import-mappings",
        "ok": True,
        "appended": appended,
        "unchanged": unchanged,
        "message": f"매핑 revision {len(appended)}건 추가" + (f", 동일 판정 {len(unchanged)}건은 변경 없음" if unchanged else ""),
    }


def _same_content(latest: dict[str, Any], item: MappingSpec) -> bool:
    return all(latest[key] == value for key, value in item.content().items())


def _check_targets(conn: sqlite3.Connection, item: MappingSpec, snapshot_data_mode: str, prefix: str) -> None:
    if item.recording_id is None:
        return
    recording = fetch_recording(conn, item.recording_id)
    if recording is None:
        raise InputError(
            f"{prefix}: 녹음 '{item.recording_id}' 이(가) 등록되어 있지 않습니다. 먼저 import-recordings 로 등록하세요",
            code="recording_not_found",
            details=[{"field": f"{prefix}.recording_id", "recording_id": item.recording_id}],
        )
    if recording["data_mode"] != snapshot_data_mode:
        raise InputError(
            f"{prefix}: 녹음 '{item.recording_id}' 의 data_mode({recording['data_mode']}) 가 스냅샷의 data_mode({snapshot_data_mode}) 와 다릅니다. 가상·실제 자료를 섞지 않습니다",
            code="data_mode_mismatch",
            details=[{"field": f"{prefix}.recording_id", "recording_data_mode": recording["data_mode"], "snapshot_data_mode": snapshot_data_mode}],
        )
    if item.lyric_version_id is None:
        return
    lyric = fetch_lyric_version(conn, item.lyric_version_id)
    if lyric is None:
        raise InputError(
            f"{prefix}: 가사 버전 '{item.lyric_version_id}' 이(가) 등록되어 있지 않습니다",
            code="lyric_version_not_found",
            details=[{"field": f"{prefix}.lyric_version_id", "lyric_version_id": item.lyric_version_id}],
        )
    if lyric["recording_id"] != item.recording_id:
        raise InputError(
            f"{prefix}: 가사 버전 '{item.lyric_version_id}' 은(는) 녹음 '{lyric['recording_id']}' 의 것이며 선택한 녹음 '{item.recording_id}' 과 다릅니다",
            code="lyric_recording_mismatch",
            details=[{"field": f"{prefix}.lyric_version_id", "lyric_recording_id": lyric["recording_id"], "selected_recording_id": item.recording_id}],
        )


# ---------------------------------------------------------------------------
# 조회·진행 상황
# ---------------------------------------------------------------------------


def _describe(conn: sqlite3.Connection, mapping: dict[str, Any] | None) -> dict[str, Any] | None:
    if mapping is None:
        return None
    described = {
        "mapping_id": mapping["mapping_id"],
        "revision": mapping["revision"],
        "base_revision": mapping["base_revision"],
        "state": mapping["state"],
        "recording_id": mapping["recording_id"],
        "lyric_version_id": mapping["lyric_version_id"],
        "reviewer_id": mapping["reviewer_id"],
        "reason": mapping["reason"],
        "created_at": mapping["created_at"],
        "recording": None,
        "lyric_version": None,
    }
    if mapping["recording_id"]:
        rec = fetch_recording(conn, mapping["recording_id"])
        if rec is not None:
            described["recording"] = {k: rec[k] for k in ("title", "artist", "version_kind", "version_label", "release_precision", "release_date", "vocal_type", "data_mode")}
    if mapping["lyric_version_id"]:
        lyr = fetch_lyric_version(conn, mapping["lyric_version_id"])
        if lyr is not None:
            described["lyric_version"] = {k: lyr[k] for k in ("recording_id", "status", "language", "source_id")}
    return described


def entry_states(conn: sqlite3.Connection, snapshot_id: str, *, history: bool = False) -> list[dict[str, Any]]:
    """스냅샷의 각 항목과 현재 매핑 상태(파생 'unmapped' 포함)."""
    entries: list[dict[str, Any]] = []
    rows = conn.execute("SELECT rank, title, artist, provider_track_id FROM chart_entries WHERE snapshot_id = ? ORDER BY rank", (snapshot_id,)).fetchall()
    for row in rows:
        latest = latest_mapping(conn, snapshot_id, row["rank"])
        current = _describe(conn, latest)
        lyric_status = "none"
        if current and current["lyric_version"]:
            lyric_status = current["lyric_version"]["status"]
        entry = {
            "rank": row["rank"],
            "title": row["title"],
            "artist": row["artist"],
            "provider_track_id": row["provider_track_id"],
            "state": latest["state"] if latest else "unmapped",
            "revision": latest["revision"] if latest else 0,
            "recording_id": latest["recording_id"] if latest else None,
            "lyric_version_id": latest["lyric_version_id"] if latest else None,
            "lyric_status": lyric_status,
            "vocal_type": current["recording"]["vocal_type"] if current and current["recording"] else None,
            "current": current,
        }
        if history:
            entry["history"] = [_describe(conn, m) for m in mapping_history(conn, snapshot_id, row["rank"])]
        entries.append(entry)
    return entries


def snapshot_progress(conn: sqlite3.Connection, snapshot_id: str) -> dict[str, Any]:
    entries = entry_states(conn, snapshot_id)
    states = {state: sum(1 for e in entries if e["state"] == state) for state in MAPPING_STATE_ORDER}
    lyric_counts: dict[str, int] = {}
    for e in entries:
        if e["state"] == "confirmed":
            lyric_counts[e["lyric_status"]] = lyric_counts.get(e["lyric_status"], 0) + 1
    return {
        "snapshot_id": snapshot_id,
        "entries": len(entries),
        "states": states,
        "confirmed_lyric_status": lyric_counts,
        "confirmed_recording_ids": sorted({e["recording_id"] for e in entries if e["state"] == "confirmed"}),
        "complete": bool(entries) and states["confirmed"] == len(entries),
    }


def matching_summary(conn: sqlite3.Connection, snapshot_ids: list[str]) -> dict[str, Any]:
    """validate 가 쓰는 매칭 진행 요약. 선택한 스냅샷들에 한정한다."""
    per_snapshot = [snapshot_progress(conn, sid) for sid in snapshot_ids]
    totals = {state: sum(p["states"][state] for p in per_snapshot) for state in MAPPING_STATE_ORDER}
    total_entries = sum(p["entries"] for p in per_snapshot)
    confirmed_sets = [set(p["confirmed_recording_ids"]) for p in per_snapshot]
    unique_confirmed = set().union(*confirmed_sets) if confirmed_sets else set()
    shared = set.intersection(*confirmed_sets) if len(confirmed_sets) >= 2 else set()
    if total_entries == 0:
        status = "no_entries"
    elif totals["confirmed"] == total_entries:
        status = "complete"
    elif totals["confirmed"] + totals["candidate"] + totals["unresolved"] == 0:
        status = "not_started"
    else:
        status = "in_progress"
    return {
        "status": status,
        "scope_snapshot_ids": list(snapshot_ids),
        "entries": total_entries,
        "states": totals,
        "per_snapshot": per_snapshot,
        "unique_confirmed_recordings": len(unique_confirmed),
        "confirmed_recordings_in_all_snapshots": sorted(shared),
        "message": (
            "곡·버전 매칭은 사람이 confirmed 로 확정한 것만 인정합니다. candidate·unresolved·unmapped 항목은 라벨 집계에서 미확정(U)으로 다룹니다. "
            "라벨링·집계는 아직 수행되지 않았습니다"
        ),
    }


def lyrics_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """작업 폴더 전체 가사 버전 카탈로그 합계(매핑 여부와 무관)."""
    counts = {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM lyric_versions GROUP BY status")}
    return {
        "status": "metadata_only",
        "scope": "workspace_catalogue",
        "lyric_versions": sum(counts.values()),
        "by_status": counts,
        "scope_note": "작업 폴더에 등록된 모든 가사 버전의 합계입니다. 매핑되지 않은 녹음의 가사 버전도 포함되며 특정 실험의 확보율이 아닙니다",
        "message": "가사 본문은 저장하지 않습니다. 상태·언어·출처·참조만 기록하며 missing(미확보)은 absent(없음)와 다릅니다",
    }


ENTRY_LYRIC_STATUSES: tuple[str, ...] = ("available", "partial", "translation_only", "missing", "not_applicable", "none", "not_confirmed")


def lyrics_summary_for_snapshots(conn: sqlite3.Connection, snapshot_ids: list[str]) -> dict[str, Any]:
    """선택한 스냅샷 항목의 최신 매핑이 가리키는 가사 버전 상태만 센다.

    포함 규칙: 최신 매핑이 confirmed 이고 lyric_version_id 가 있으면 그 상태, confirmed 이지만 가사 버전 미선택이면 'none',
    candidate/unresolved/unmapped 항목은 'not_confirmed'. 매핑되지 않은 카탈로그 등록은 들어가지 않는다.
    """
    counts = {status: 0 for status in ENTRY_LYRIC_STATUSES}
    lyric_version_ids: set[str] = set()
    total = 0
    for sid in snapshot_ids:
        for entry in entry_states(conn, sid):
            total += 1
            if entry["state"] != "confirmed":
                counts["not_confirmed"] += 1
            else:
                counts[entry["lyric_status"]] += 1
                if entry["lyric_version_id"]:
                    lyric_version_ids.add(entry["lyric_version_id"])
    return {
        "status": "metadata_only",
        "scope": "selected_snapshots_latest_mappings",
        "scope_snapshot_ids": list(snapshot_ids),
        "entries": total,
        "by_status": counts,
        "distinct_lyric_versions": len(lyric_version_ids),
        "inclusion_rule": (
            "항목별 최신 매핑이 confirmed 이고 가사 버전이 있으면 그 상태로, confirmed 이지만 가사 버전 미선택이면 none, "
            "candidate·unresolved·unmapped 는 not_confirmed 로 셉니다. 매핑되지 않은 카탈로그 등록은 포함하지 않습니다"
        ),
        "message": "가사 본문은 저장하지 않습니다. missing(미확보)·none·not_confirmed 는 absent(없음)와 다르며 라벨 집계에서 U 로 다룹니다",
    }


def show_mappings(workspace: Path, *, snapshot_id: str | None = None, history: bool = False) -> dict[str, Any]:
    conn = open_workspace(Path(workspace))
    try:
        conn.execute("BEGIN")
        try:
            if snapshot_id is not None:
                if conn.execute("SELECT 1 FROM chart_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone() is None:
                    raise InputError(f"스냅샷 '{snapshot_id}' 이(가) 없습니다", code="snapshot_not_found")
                snapshot_ids = [snapshot_id]
            else:
                snapshot_ids = [r[0] for r in conn.execute("SELECT snapshot_id FROM chart_snapshots ORDER BY chart_id, period_start, period_end, revision")]
            snapshots = [
                {"snapshot_id": sid, "progress": snapshot_progress(conn, sid), "entries": entry_states(conn, sid, history=history)}
                for sid in snapshot_ids
            ]
            summary = matching_summary(conn, snapshot_ids)
            recordings = [
                {k: r[k] for k in ("recording_id", "title", "artist", "version_kind", "version_label", "original_recording_id", "release_precision", "release_date", "vocal_type", "data_mode")}
                for r in conn.execute("SELECT * FROM recordings ORDER BY recording_id")
            ]
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()
    return {
        "command": "show-mappings",
        "ok": True,
        "scope": {"snapshot_id": snapshot_id, "history": history},
        "matching": summary,
        "snapshots": snapshots,
        "recordings": recordings,
        "notice": "매칭 상태 조회입니다. 라벨·정서 수치는 없습니다.",
    }
