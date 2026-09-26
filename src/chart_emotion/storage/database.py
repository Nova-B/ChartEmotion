"""SQLite 작업 폴더 저장소.

- 작업 폴더 안의 `chart_emotion.sqlite3` 하나가 저장소다.
- 모든 연결에서 `PRAGMA foreign_keys = ON` 을 켠다.
- 스키마 버전은 `schema_meta.schema_version` 에 기록하며 마이그레이션은 MIGRATIONS 에 순서대로 적는다.
- 현재 코드보다 새로운(또는 알 수 없는) 스키마 버전은 아무것도 바꾸지 않고 거부한다.
- 읽기·쓰기 명령은 초기화되지 않은 폴더에 DB 를 만들지 않는다. 초기화·마이그레이션은 `init` 만 수행한다.

스키마 v1(D01~D04): sources, chart_definitions, chart_snapshots, chart_entries.
스키마 v2(D05): recordings, lyric_versions, entry_mappings 추가. v1 자료는 그대로 보존된다.
annotation_revisions / experiment_revisions / runs 는 D06 이후 필요한 시점에 새 마이그레이션으로 추가한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..errors import ProcessingError, WorkspaceError

DB_FILENAME = "chart_emotion.sqlite3"
SCHEMA_VERSION = 2

MIGRATIONS: dict[int, str] = {
    1: """
CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE sources (
    source_id          TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    kind               TEXT NOT NULL CHECK (kind IN ('chart', 'lyrics', 'metadata')),
    data_mode          TEXT NOT NULL CHECK (data_mode IN ('synthetic', 'real')),
    provider           TEXT,
    url                TEXT,
    status             TEXT NOT NULL CHECK (status IN ('pending', 'permitted', 'restricted')),
    allowed_operations TEXT NOT NULL,   -- JSON 배열. import / store / analyze 의 부분집합
    checked_on         TEXT,            -- 이용 조건 확인일 (YYYY-MM-DD)
    checked_by         TEXT,
    evidence           TEXT,            -- 허락 근거 (문서 URL·계약 범위·기록)
    notes              TEXT,
    registered_at      TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE chart_definitions (
    chart_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    market      TEXT NOT NULL,
    metric      TEXT NOT NULL,
    periodicity TEXT NOT NULL CHECK (periodicity IN ('daily', 'weekly', 'monthly', 'yearly', 'custom')),
    created_at  TEXT NOT NULL
);

CREATE TABLE chart_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    chart_id            TEXT NOT NULL REFERENCES chart_definitions (chart_id),
    source_id           TEXT NOT NULL REFERENCES sources (source_id),
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    display_year        INTEGER NOT NULL,
    methodology_version TEXT NOT NULL,
    expected_n          INTEGER NOT NULL CHECK (expected_n > 0),
    data_mode           TEXT NOT NULL CHECK (data_mode IN ('synthetic', 'real')),
    revision            INTEGER NOT NULL CHECK (revision > 0),
    status              TEXT NOT NULL CHECK (status IN ('complete', 'incomplete')),
    csv_filename        TEXT NOT NULL,
    file_sha256         TEXT NOT NULL,     -- 원본 CSV 바이트 해시
    content_sha256      TEXT NOT NULL,     -- 정규화한 행 내용 해시 (BOM·공백 차이 무시)
    config_sha256       TEXT NOT NULL,     -- 배치 설정 정규 JSON 해시
    notes               TEXT,
    imported_at         TEXT NOT NULL,
    UNIQUE (chart_id, period_start, period_end, revision)
);

CREATE TABLE chart_entries (
    snapshot_id       TEXT NOT NULL REFERENCES chart_snapshots (snapshot_id),
    rank              INTEGER NOT NULL CHECK (rank > 0),
    title             TEXT NOT NULL,
    artist            TEXT NOT NULL,
    provider_track_id TEXT,
    PRIMARY KEY (snapshot_id, rank)
) WITHOUT ROWID;
""",
    2: """
CREATE TABLE recordings (
    recording_id          TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    artist                TEXT NOT NULL,
    version_kind          TEXT NOT NULL CHECK (version_kind IN ('original', 'rerecording', 'cover', 'remix', 'live', 'translation', 'other')),
    version_label         TEXT,
    original_recording_id TEXT REFERENCES recordings (recording_id),
    release_precision     TEXT NOT NULL CHECK (release_precision IN ('unknown', 'year', 'month', 'day')),
    release_date          TEXT,               -- 정밀도에 맞는 문자열(YYYY / YYYY-MM / YYYY-MM-DD). unknown 이면 NULL
    vocal_type            TEXT NOT NULL CHECK (vocal_type IN ('lyrical', 'instrumental')),
    data_mode             TEXT NOT NULL CHECK (data_mode IN ('synthetic', 'real')),
    source_id             TEXT REFERENCES sources (source_id),   -- 메타데이터 출처(선택). 없으면 수동 입력
    provider_track_ids    TEXT NOT NULL,      -- JSON 배열. 참고용이며 병합 근거가 아님
    notes                 TEXT,
    content_sha256        TEXT NOT NULL,      -- 등록 내용 해시. 같은 ID 로 다른 내용은 거부
    registered_by         TEXT NOT NULL,
    registered_at         TEXT NOT NULL,
    CHECK ((release_precision = 'unknown') = (release_date IS NULL))
);

CREATE TABLE lyric_versions (
    lyric_version_id TEXT PRIMARY KEY,
    recording_id     TEXT NOT NULL REFERENCES recordings (recording_id),
    status           TEXT NOT NULL CHECK (status IN ('available', 'partial', 'translation_only', 'missing', 'not_applicable')),
    language         TEXT,
    source_id        TEXT REFERENCES sources (source_id),
    reference        TEXT,               -- 보관·접근 참조(경로·카탈로그 ID). 가사 본문이 아니다
    notes            TEXT,
    content_sha256   TEXT NOT NULL,
    registered_by    TEXT NOT NULL,
    registered_at    TEXT NOT NULL
);

CREATE TABLE entry_mappings (
    mapping_id       TEXT PRIMARY KEY,   -- '<snapshot_id>.rank<rank>.r<revision>' 불변 ID
    snapshot_id      TEXT NOT NULL,
    rank             INTEGER NOT NULL CHECK (rank > 0),
    revision         INTEGER NOT NULL CHECK (revision > 0),
    base_revision    INTEGER NOT NULL CHECK (base_revision >= 0 AND base_revision < revision),
    state            TEXT NOT NULL CHECK (state IN ('candidate', 'confirmed', 'unresolved')),
    recording_id     TEXT REFERENCES recordings (recording_id),
    lyric_version_id TEXT REFERENCES lyric_versions (lyric_version_id),
    reviewer_id      TEXT NOT NULL,
    reason           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (snapshot_id, rank) REFERENCES chart_entries (snapshot_id, rank),
    UNIQUE (snapshot_id, rank, revision),
    CHECK ((state = 'unresolved') = (recording_id IS NULL)),
    CHECK (recording_id IS NOT NULL OR lyric_version_id IS NULL)
);
""",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database_path(workspace: Path) -> Path:
    return Path(workspace) / DB_FILENAME


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)  # 트랜잭션은 명시적으로 BEGIN/COMMIT
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def read_schema_version(conn: sqlite3.Connection) -> int:
    """0 = 빈 DB. 알 수 없는 배치이면 WorkspaceError."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "schema_meta" not in tables:
        if tables:
            raise WorkspaceError(
                "저장소에 schema_meta 테이블이 없어 chart-emotion 이 만든 DB 로 볼 수 없습니다. 아무것도 변경하지 않았습니다",
                code="schema_unknown_layout",
            )
        return 0
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        raise WorkspaceError("schema_meta 에 schema_version 이 없습니다. 아무것도 변경하지 않았습니다", code="schema_unknown_layout")
    try:
        return int(row[0])
    except (TypeError, ValueError):
        raise WorkspaceError(f"schema_version 값 '{row[0]}' 을 해석할 수 없습니다", code="schema_unknown_layout") from None


def _refuse_if_newer(version: int, path: Path) -> None:
    if version > SCHEMA_VERSION:
        raise WorkspaceError(
            f"저장소 {path} 의 스키마 버전 {version} 은 이 프로그램이 지원하는 버전 {SCHEMA_VERSION} 보다 새롭습니다. "
            "더 새로운 chart-emotion 으로 여세요. 아무것도 변경하지 않았습니다",
            code="schema_too_new",
            details=[{"found": version, "supported": SCHEMA_VERSION}],
        )


def open_workspace(workspace: Path) -> sqlite3.Connection:
    """초기화된 작업 폴더를 연다. DB 를 새로 만들지 않는다."""
    path = database_path(workspace)
    if not Path(workspace).is_dir() or not path.is_file():
        raise WorkspaceError(
            f"작업 폴더가 초기화되지 않았습니다: {Path(workspace)} (먼저 `chart-emotion init <workspace>` 를 실행하세요)",
            code="workspace_not_initialized",
        )
    conn = _connect(path)
    try:
        version = read_schema_version(conn)
        _refuse_if_newer(version, path)
        if version < SCHEMA_VERSION:
            raise WorkspaceError(
                f"저장소 스키마 버전 {version} 은 현재 버전 {SCHEMA_VERSION} 보다 오래되었습니다. "
                "`chart-emotion init <workspace>` 로 명시적으로 마이그레이션하세요",
                code="schema_migration_required",
                details=[{"found": version, "supported": SCHEMA_VERSION}],
            )
    except Exception:
        conn.close()
        raise
    return conn


def initialize_workspace(workspace: Path) -> tuple[sqlite3.Connection, int, bool]:
    """폴더와 DB 를 만들고 필요한 마이그레이션을 적용한다.

    반환: (연결, 이전 스키마 버전, DB 파일을 새로 만들었는지)
    """
    workspace = Path(workspace)
    path = database_path(workspace)
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"작업 폴더를 만들 수 없습니다: {workspace} ({exc.strerror})", code="workspace_unwritable") from None
    created = not path.exists()
    conn = _connect(path)
    try:
        previous = read_schema_version(conn)
        _refuse_if_newer(previous, path)
        for version in range(previous + 1, SCHEMA_VERSION + 1):
            _apply_migration(conn, version)
    except Exception:
        conn.close()
        if created:
            # 새로 만든 빈 파일만 정리한다. 기존 파일은 손대지 않는다.
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return conn, previous, created


def _apply_migration(conn: sqlite3.Connection, version: int) -> None:
    sql = MIGRATIONS.get(version)
    if sql is None:
        raise ProcessingError(f"마이그레이션 {version} 이 정의되어 있지 않습니다", code="migration_missing")
    conn.execute("BEGIN IMMEDIATE")
    try:
        # executescript 는 암묵적으로 COMMIT 하므로 쓰지 않고 문장 단위로 실행한다.
        for statement in _split_statements(sql):
            conn.execute(statement)
        now = utc_now()
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('created_at', ?) ON CONFLICT(key) DO NOTHING",
            (now,),
        )
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('updated_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now,),
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _split_statements(sql: str) -> list[str]:
    """세미콜론으로 끝나는 DDL 문을 나눈다(주석 안의 세미콜론은 쓰지 않는다)."""
    statements: list[str] = []
    for chunk in sql.split(";"):
        cleaned = "\n".join(line for line in chunk.splitlines() if line.strip() and not line.strip().startswith("--"))
        if cleaned.strip():
            statements.append(cleaned)
    return statements


def foreign_keys_enabled(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    """PRAGMA foreign_key_check 위반 목록(정상이면 빈 리스트)."""
    rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    return [f"{row[0]} rowid={row[1]} -> {row[2]}" for row in rows]
