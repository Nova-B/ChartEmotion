"""차트 CSV 검증·파싱.

계약:
- 헤더는 `rank,title,artist,provider_track_id` 네 열이 모두 있어야 한다(순서는 무관, 중복·추가 열 불가).
- rank: 1..expected_n 의 양의 정수, 파일 안에서 유일.
- title, artist: 공백을 제외하고 비어 있으면 안 된다.
- provider_track_id: 빈값 허용(None 으로 저장).
- 인코딩 UTF-8, BOM 허용. 따옴표 안의 쉼표·줄바꿈은 표준 CSV 규칙을 따른다.
- 완전히 빈 줄은 건너뛴다. 셀 수가 다른 행은 오류다.
- 누락 순위가 있으면 오류가 아니라 `missing_ranks` 로 보고한다(스냅샷은 incomplete 로 저장된다).

전체 행을 검증한 뒤 오류가 하나라도 있으면 InputError 로 거부한다.
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.jsonfile import canonical_json
from ..errors import InputError

EXPECTED_HEADER: tuple[str, ...] = ("rank", "title", "artist", "provider_track_id")
MAX_REPORTED_ROW_ERRORS = 50


@dataclass(frozen=True)
class ChartEntry:
    rank: int
    title: str
    artist: str
    provider_track_id: str | None


@dataclass(frozen=True)
class ChartCsvResult:
    filename: str
    entries: tuple[ChartEntry, ...]
    expected_n: int
    missing_ranks: tuple[int, ...]
    file_sha256: str
    content_sha256: str
    had_bom: bool

    @property
    def is_complete(self) -> bool:
        return not self.missing_ranks


def _row_error(line: int, column: str | None, code: str, message: str) -> dict[str, Any]:
    item: dict[str, Any] = {"line": line, "code": code, "message": message}
    if column is not None:
        item["column"] = column
    return item


def _read_text(path: Path) -> tuple[bytes, str, bool]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise InputError(f"CSV 파일을 찾을 수 없습니다: {path}", code="csv_not_found") from None
    except OSError as exc:
        raise InputError(f"CSV 파일을 읽을 수 없습니다: {path} ({exc.strerror})", code="csv_unreadable") from None
    had_bom = raw.startswith(codecs.BOM_UTF8)
    body = raw[len(codecs.BOM_UTF8) :] if had_bom else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        offset = exc.start + (len(codecs.BOM_UTF8) if had_bom else 0)
        raise InputError(
            f"CSV 파일 {path.name} 은(는) UTF-8 로 해석할 수 없습니다 (바이트 위치 {offset}: {exc.reason})",
            code="csv_invalid_utf8",
            details=[{"file": str(path), "byte_offset": offset, "reason": exc.reason}],
        ) from None
    return raw, text, had_bom


def _validate_header(path: Path, header: list[str]) -> dict[str, int]:
    names = [cell.strip() for cell in header]
    details: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, name in enumerate(names):
        if name in seen:
            details.append(_row_error(1, name, "csv_duplicate_header", f"헤더 열 '{name}' 이(가) 중복됩니다 (열 {seen[name] + 1}, {index + 1})"))
        else:
            seen[name] = index
    missing = [name for name in EXPECTED_HEADER if name not in seen]
    extra = [name for name in seen if name not in EXPECTED_HEADER]
    if missing:
        details.append(_row_error(1, None, "csv_missing_column", f"필수 열이 없습니다: {', '.join(missing)}"))
    if extra:
        details.append(_row_error(1, None, "csv_unexpected_column", f"허용되지 않는 열입니다: {', '.join(repr(e) for e in extra)}"))
    if details:
        raise InputError(
            f"CSV 파일 {path.name} 의 헤더가 계약과 다릅니다. 필요한 헤더: {','.join(EXPECTED_HEADER)}",
            code="csv_header_invalid",
            details=details,
        )
    return seen


def parse_chart_csv(path: Path, *, expected_n: int) -> ChartCsvResult:
    if expected_n < 1:
        raise InputError("expected_n 은 1 이상이어야 합니다", code="batch_config_invalid")

    raw, text, had_bom = _read_text(path)
    if text.strip() == "":
        raise InputError(f"CSV 파일 {path.name} 이(가) 비어 있습니다", code="csv_empty")

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:  # pragma: no cover - strip() 검사로 이미 걸러짐
        raise InputError(f"CSV 파일 {path.name} 이(가) 비어 있습니다", code="csv_empty") from None
    except csv.Error as exc:
        raise InputError(
            f"CSV 파일 {path.name} 의 헤더를 해석할 수 없습니다: {exc}",
            code="csv_malformed",
            details=[_row_error(1, None, "csv_malformed", str(exc))],
        ) from None

    columns = _validate_header(path, header)
    idx_rank = columns["rank"]
    idx_title = columns["title"]
    idx_artist = columns["artist"]
    idx_ptid = columns["provider_track_id"]

    entries: dict[int, ChartEntry] = {}
    first_line_of_rank: dict[int, int] = {}
    errors: list[dict[str, Any]] = []
    data_rows = 0

    try:
        for row in reader:
            line = reader.line_num
            if not row:
                continue  # 완전히 빈 줄
            data_rows += 1
            if len(row) != len(EXPECTED_HEADER):
                errors.append(
                    _row_error(line, None, "csv_cell_count", f"셀이 {len(EXPECTED_HEADER)}개여야 합니다 (현재 {len(row)}개)")
                )
                continue

            rank_text = row[idx_rank].strip()
            title = row[idx_title].strip()
            artist = row[idx_artist].strip()
            ptid = row[idx_ptid].strip() or None

            row_ok = True
            rank: int | None = None
            if not rank_text.isdigit() or not rank_text.isascii():
                errors.append(_row_error(line, "rank", "csv_rank_not_integer", f"rank 는 양의 정수여야 합니다 (현재: '{row[idx_rank]}')"))
                row_ok = False
            elif len(rank_digits := (rank_text.lstrip("0") or "0")) > len(str(expected_n)):
                # 앞자리 0 을 제거한 유효 자릿수로 범위를 먼저 판정한다. 수천 자리 숫자도 처리 실패가 아니라 입력 오류다.
                shown = rank_text if len(rank_text) <= 20 else f"{rank_text[:20]}…({len(rank_text)}자리)"
                errors.append(
                    _row_error(line, "rank", "csv_rank_out_of_range", f"rank 는 1..{expected_n} 범위여야 합니다 (현재: {shown})")
                )
                row_ok = False
            else:
                # 원본 문자열이 아니라 앞자리 0 을 뗀 짧은 문자열만 변환한다('0'*5000 → '0', '0'*5000+'1' → '1').
                rank = int(rank_digits)
                if rank < 1 or rank > expected_n:
                    errors.append(
                        _row_error(line, "rank", "csv_rank_out_of_range", f"rank 는 1..{expected_n} 범위여야 합니다 (현재: {rank})")
                    )
                    row_ok = False
                elif rank in entries:
                    errors.append(
                        _row_error(
                            line,
                            "rank",
                            "csv_duplicate_rank",
                            f"rank {rank} 이(가) {first_line_of_rank[rank]}행에 이미 있습니다. 같은 순위에 다른 곡은 충돌입니다",
                        )
                    )
                    row_ok = False
            if title == "":
                errors.append(_row_error(line, "title", "csv_empty_field", "title 은 비어 있을 수 없습니다"))
                row_ok = False
            if artist == "":
                errors.append(_row_error(line, "artist", "csv_empty_field", "artist 는 비어 있을 수 없습니다"))
                row_ok = False
            if row_ok and rank is not None:
                entries[rank] = ChartEntry(rank=rank, title=title, artist=artist, provider_track_id=ptid)
                first_line_of_rank[rank] = line
    except csv.Error as exc:
        raise InputError(
            f"CSV 파일 {path.name} 의 {reader.line_num}행 근처에서 형식 오류: {exc}",
            code="csv_malformed",
            details=[_row_error(reader.line_num, None, "csv_malformed", str(exc))],
        ) from None

    if data_rows == 0:
        raise InputError(f"CSV 파일 {path.name} 에 헤더만 있고 데이터 행이 없습니다", code="csv_header_only")

    if errors:
        truncated = len(errors) > MAX_REPORTED_ROW_ERRORS
        raise InputError(
            f"CSV 파일 {path.name} 에서 {len(errors)}건의 행 오류가 발견되어 배치 전체를 반영하지 않았습니다",
            code="csv_invalid",
            details=errors[:MAX_REPORTED_ROW_ERRORS] + ([{"code": "truncated", "message": f"이하 {len(errors) - MAX_REPORTED_ROW_ERRORS}건 생략"}] if truncated else []),
        )

    ordered = tuple(entries[rank] for rank in sorted(entries))
    missing = tuple(rank for rank in range(1, expected_n + 1) if rank not in entries)
    canonical = canonical_json([[e.rank, e.title, e.artist, e.provider_track_id] for e in ordered])
    return ChartCsvResult(
        filename=path.name,
        entries=ordered,
        expected_n=expected_n,
        missing_ranks=missing,
        file_sha256=hashlib.sha256(raw).hexdigest(),
        content_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        had_bom=had_bom,
    )
