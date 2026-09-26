"""chart-emotion CLI.

명령:
  init <workspace>
  register-source --workspace <path> --source-id ... --name ... --kind ... --data-mode ...
  import-chart <csv> --batch <json> --workspace <path> [--revision N]
  import-recordings <json> --workspace <path>
  import-mappings <json> --workspace <path>
  show-mappings --workspace <path> [--snapshot <id>] [--history]
  validate --workspace <path> [--experiment <json>]

종료 코드: 0 성공 / 2 입력·전제조건 오류 / 1 처리 실패.
성공 결과는 stdout 에 UTF-8 JSON, 오류는 stderr 에 UTF-8 JSON 으로 출력한다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .application.import_chart import import_chart
from .application.init_workspace import init_workspace
from .application.mappings import import_mappings, show_mappings
from .application.recordings import import_recordings
from .application.sources import register_source, spec_from_cli
from .application.validate import validate_experiment, validate_workspace
from .domain.contracts import DATA_MODES, SOURCE_KINDS, SOURCE_OPERATIONS, SOURCE_STATUSES
from .errors import ChartEmotionError, InputError

EXIT_OK = 0
EXIT_PROCESSING = 1
EXIT_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chart-emotion",
        description=(
            "차트 정서 관측소 (D01~D05): 차트 CSV 검증·보관, 녹음·가사 버전 등록, 차트 항목 매핑 개정 이력, 실험 설정 점검. "
            "라벨링·집계는 아직 구현되지 않았으며 정서 점수를 계산하지 않습니다."
        ),
    )
    parser.add_argument("--version", action="version", version=f"chart-emotion {__version__}")
    parser.add_argument("--debug", action="store_true", help="예상하지 못한 오류에서 traceback 을 출력")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p_init = sub.add_parser("init", help="작업 폴더와 SQLite 저장소를 만들고 가상 출처를 등록(재실행 시 기존 자료 보존)")
    p_init.add_argument("workspace", type=Path, help="작업 폴더 경로 (없으면 생성)")

    p_src = sub.add_parser("register-source", help="차트·가사·메타데이터 출처와 이용 허용 범위를 등록")
    _add_workspace(p_src)
    p_src.add_argument("--source-id", required=True)
    p_src.add_argument("--name", required=True, help="표시 이름")
    p_src.add_argument("--kind", required=True, choices=SOURCE_KINDS)
    p_src.add_argument("--data-mode", required=True, choices=DATA_MODES, help="synthetic(가상) 또는 real(실제)")
    p_src.add_argument("--status", choices=SOURCE_STATUSES, help="기본값: synthetic=permitted, real=pending")
    p_src.add_argument("--allow", action="append", choices=SOURCE_OPERATIONS, help="허용 작업(반복 지정). permitted 상태에서만 가능")
    p_src.add_argument("--provider")
    p_src.add_argument("--url")
    p_src.add_argument("--checked-on", help="이용 조건 확인일 YYYY-MM-DD (real+permitted 필수)")
    p_src.add_argument("--checked-by")
    p_src.add_argument("--evidence", help="허락 근거: 문서 URL·계약 범위·기록 (real+permitted 필수)")
    p_src.add_argument("--notes")
    p_src.add_argument("--replace", action="store_true", help="같은 source_id 가 다른 내용으로 있으면 덮어쓰기")

    p_imp = sub.add_parser("import-chart", help="차트 CSV 를 배치 설정과 함께 검증하고 스냅샷으로 보관")
    p_imp.add_argument("csv", type=Path, help="rank,title,artist,provider_track_id 열을 가진 UTF-8 CSV")
    p_imp.add_argument("--batch", required=True, type=Path, help="배치 설정 JSON")
    _add_workspace(p_imp)
    p_imp.add_argument("--revision", type=int, help="같은 기간의 정정본을 새 스냅샷으로 추가할 때 지정 (최신 revision + 1)")

    p_rec = sub.add_parser("import-recordings", help="녹음·가사 버전 메타데이터를 불변 등록 (가사 본문 없음)")
    p_rec.add_argument("json", type=Path, help="chart-emotion.recordings.v1 JSON")
    _add_workspace(p_rec)

    p_map = sub.add_parser("import-mappings", help="차트 항목 ↔ 녹음 매핑 판정을 새 revision 으로 추가")
    p_map.add_argument("json", type=Path, help="chart-emotion.mappings.v1 JSON")
    _add_workspace(p_map)

    p_show = sub.add_parser("show-mappings", help="항목별 현재 매핑 상태와 이력을 조회")
    _add_workspace(p_show)
    p_show.add_argument("--snapshot", help="특정 snapshot_id 만 조회")
    p_show.add_argument("--history", action="store_true", help="항목별 전체 revision 이력 포함")

    p_val = sub.add_parser("validate", help="작업 폴더 상태 또는 실험 설정을 검증")
    _add_workspace(p_val)
    p_val.add_argument("--experiment", type=Path, help="실험 설정 JSON. 지정하면 실험 검증, 없으면 작업 폴더 전체 검증")

    return parser


def _add_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, type=Path, help="init 으로 초기화한 작업 폴더")


def _emit(payload: dict[str, Any], *, stream: Any) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    stream.flush()


def _configure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # pragma: no cover - 닫힌 스트림 등
                pass


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "init":
        return init_workspace(args.workspace)
    if args.command == "register-source":
        spec = spec_from_cli(
            source_id=args.source_id,
            name=args.name,
            kind=args.kind,
            data_mode=args.data_mode,
            status=args.status,
            allow=args.allow,
            provider=args.provider,
            url=args.url,
            checked_on=args.checked_on,
            checked_by=args.checked_by,
            evidence=args.evidence,
            notes=args.notes,
        )
        return register_source(args.workspace, spec, replace_existing=args.replace)
    if args.command == "import-chart":
        return import_chart(args.workspace, args.csv, args.batch, revision=args.revision)
    if args.command == "import-recordings":
        return import_recordings(args.workspace, args.json)
    if args.command == "import-mappings":
        return import_mappings(args.workspace, args.json)
    if args.command == "show-mappings":
        return show_mappings(args.workspace, snapshot_id=args.snapshot, history=args.history)
    if args.command == "validate":
        if args.experiment is not None:
            return validate_experiment(args.workspace, args.experiment)
        return validate_workspace(args.workspace)
    raise InputError(f"알 수 없는 명령: {args.command}", code="unknown_command")  # pragma: no cover


def main(argv: Sequence[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except ChartEmotionError as exc:
        _emit({"ok": False, "command": args.command, "error": exc.to_dict()}, stream=sys.stderr)
        return exc.exit_code
    except sqlite3.Error as exc:
        payload: dict[str, Any] = {
            "ok": False,
            "command": args.command,
            "error": {"code": "storage_error", "message": f"SQLite 오류: {exc}", "exit_code": EXIT_PROCESSING, "details": []},
        }
        if args.debug:
            payload["traceback"] = traceback.format_exc()
        _emit(payload, stream=sys.stderr)
        return EXIT_PROCESSING
    except Exception as exc:  # noqa: BLE001 - 마지막 방어선
        payload = {
            "ok": False,
            "command": args.command,
            "error": {"code": "unexpected_error", "message": f"{type(exc).__name__}: {exc}", "exit_code": EXIT_PROCESSING, "details": []},
        }
        if args.debug:
            payload["traceback"] = traceback.format_exc()
        _emit(payload, stream=sys.stderr)
        return EXIT_PROCESSING

    _emit(result, stream=sys.stdout)
    if result.get("ok") is False:
        return EXIT_INPUT  # validate --experiment 에서 오류가 있으면 입력·전제조건 오류로 본다
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
