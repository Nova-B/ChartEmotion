"""테스트 공용 도우미. 임시 폴더만 쓰고 네트워크·실제 자료를 쓰지 않는다."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
EXAMPLES = ROOT / "examples" / "synthetic"
CHARTS = EXAMPLES / "charts"
BATCHES = EXAMPLES / "batches"
INVALID = EXAMPLES / "invalid"
VERSIONS = EXAMPLES / "versions"
EXPERIMENTS = EXAMPLES / "experiments"

RECORDINGS = EXAMPLES / "recordings"
MAPPINGS = EXAMPLES / "mappings"

TABLES = ("sources", "chart_definitions", "chart_snapshots", "chart_entries", "recordings", "lyric_versions", "entry_mappings")
SYNTHETIC_LYRICS_SOURCE_ID = "synthetic-lyrics"


def run_cli(args: list[str]) -> tuple[int, str, str]:
    """프로세스 안에서 CLI main 을 실행하고 (exit, stdout, stderr) 를 돌려준다."""
    from chart_emotion.cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(args)
        except SystemExit as exc:  # argparse --help / 인자 오류
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def write_text(path: Path, text: str, *, bom: bool = False, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode(encoding)
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)
    return path


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def batch_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source_id": "synthetic-demo",
        "chart_id": "synthetic-annual-top5",
        "snapshot_key": "test-period",
        "period_start": "2015-01-01",
        "period_end": "2015-12-31",
        "display_year": 2015,
        "methodology_version": "synthetic-v1",
        "expected_n": 5,
        "data_mode": "synthetic",
        "chart": {"name": "테스트 차트", "market": "synthetic", "metric": "synthetic_score", "periodicity": "yearly"},
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def experiment_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "experiment_id": "test-exp",
        "revision": 1,
        "data_mode": "synthetic",
        "snapshot_ids": ["demo-period-a-r1", "demo-period-b-r1"],
        "top_n": 5,
        "labelset_version": "v0.1",
        "labels": ["theme.romance", "emotion.anxiety", "function.comfort"],
        "counting_unit": "chart_entry",
        "weighting": "equal",
        "annotation_role": "adopted",
        "coverage_threshold": 0.8,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def recording_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "recording_id": "rec-test-001",
        "title": "테스트곡",
        "artist": "테스트가수",
        "version_kind": "original",
        "release_precision": "day",
        "release_date": "2015-03-14",
        "vocal_type": "lyrical",
        "data_mode": "synthetic",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def lyric_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "lyric_version_id": "lyr-test-001",
        "recording_id": "rec-test-001",
        "status": "available",
        "language": "ko",
        "source_id": SYNTHETIC_LYRICS_SOURCE_ID,
        "reference": "synthetic://lyrics/test-001",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def recordings_file(recordings: list[dict[str, Any]] | None = None, lyric_versions: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"registered_by": "reviewer-1"}
    if recordings is not None:
        base["recordings"] = recordings
    if lyric_versions is not None:
        base["lyric_versions"] = lyric_versions
    base.update(overrides)
    return base


def mapping_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "snapshot_id": "demo-period-a-r1",
        "rank": 1,
        "state": "confirmed",
        "recording_id": "rec-demo-001",
        "lyric_version_id": "lyr-demo-001-ko",
        "reason": "테스트 판정",
        "base_revision": 0,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def mappings_file(mappings: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"reviewer_id": "reviewer-1", "mappings": mappings}
    base.update(overrides)
    return base


VALID_CSV = (
    "rank,title,artist,provider_track_id\n"
    "1,가상곡 가,가상가수 A,demo-001\n"
    "2,가상곡 나,가상가수 B,demo-002\n"
    "3,\"가상곡 다, 그리고 밤\",가상가수 C,demo-003\n"
    "4,가상곡 라,가상가수 D,demo-004\n"
    "5,가상곡 마,가상가수 E,\n"
)


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="chart-emotion-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, *parts: str) -> Path:
        return self.tmp.joinpath(*parts)


class WorkspaceCase(TempDirCase):
    """init 된 작업 폴더를 가진 테스트 베이스."""

    def setUp(self) -> None:
        super().setUp()
        from chart_emotion.application.init_workspace import init_workspace

        self.ws = self.path("ws")
        init_workspace(self.ws)

    def db_path(self) -> Path:
        from chart_emotion.storage.database import database_path

        return database_path(self.ws)

    def table_counts(self) -> dict[str, int]:
        conn = sqlite3.connect(str(self.db_path()))
        try:
            return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
        finally:
            conn.close()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        conn = sqlite3.connect(str(self.db_path()))
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def import_demo(self) -> None:
        from chart_emotion.application.import_chart import import_chart

        import_chart(self.ws, CHARTS / "period_a.csv", BATCHES / "period_a.json")
        import_chart(self.ws, CHARTS / "period_b.csv", BATCHES / "period_b.json")

    def register_lyrics_source(self, *, data_mode: str = "synthetic", source_id: str = SYNTHETIC_LYRICS_SOURCE_ID, **kwargs: Any) -> None:
        from chart_emotion.application.sources import SourceSpec, register_source

        spec = SourceSpec(
            source_id=source_id,
            name="가상 가사 메타데이터 (합성)" if data_mode == "synthetic" else "실제 가사 출처 예시",
            kind=kwargs.pop("kind", "lyrics"),
            data_mode=data_mode,
            status=kwargs.pop("status", "permitted" if data_mode == "synthetic" else "pending"),
            allowed_operations=kwargs.pop("allowed_operations", ("import", "store", "analyze") if data_mode == "synthetic" else ()),
            **kwargs,
        )
        register_source(self.ws, spec)

    def import_demo_recordings(self) -> None:
        """가사 출처 등록 + 예제 녹음·가사 버전 등록."""
        from chart_emotion.application.recordings import import_recordings

        self.register_lyrics_source()
        import_recordings(self.ws, RECORDINGS / "recordings.json")

    def import_demo_mappings(self) -> None:
        from chart_emotion.application.mappings import import_mappings

        import_mappings(self.ws, MAPPINGS / "period_a_initial.json")
        import_mappings(self.ws, MAPPINGS / "period_b_initial.json")
