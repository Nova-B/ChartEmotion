"""JSON 설정 파일 읽기. 파일·문법·중복 키·정수 한계 오류를 InputError 로 바꾼다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import InputError
from .validation import require_object


class _DuplicateKey(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook: 같은 객체 안에서 키가 반복되면 거부한다(중첩 객체 포함)."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def parse_json_object_text(text: str, context: str, *, file: str | None = None) -> dict[str, Any]:
    location = {"file": file} if file else {}
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKey as exc:
        raise InputError(
            f"{context}: JSON 키 '{exc.key}' 이(가) 같은 객체 안에서 반복됩니다. 어떤 값이 맞는지 알 수 없어 거부합니다",
            code="json_duplicate_key",
            details=[{**location, "key": exc.key}],
        ) from None
    except json.JSONDecodeError as exc:
        raise InputError(
            f"{context}: JSON 문법 오류 ({exc.msg}, {exc.lineno}행 {exc.colno}열)",
            code="json_syntax_error",
            details=[{**location, "line": exc.lineno, "column": exc.colno, "message": exc.msg}],
        ) from None
    except (ValueError, RecursionError) as exc:
        # 예: 자릿수 한계를 넘는 정수 토큰(int 변환 한계), 지나치게 깊은 중첩
        raise InputError(
            f"{context}: JSON 값을 처리할 수 없습니다 ({exc})",
            code="json_value_error",
            details=[{**location, "message": str(exc)}],
        ) from None
    return require_object(value, context)


def load_json_object(path: Path, context: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise InputError(f"{context}: 파일을 찾을 수 없습니다: {path}", code="json_not_found") from None
    except OSError as exc:
        raise InputError(f"{context}: 파일을 읽을 수 없습니다: {path} ({exc.strerror})", code="json_unreadable") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError(
            f"{context}: UTF-8 로 해석할 수 없습니다 (바이트 위치 {exc.start})",
            code="json_invalid_utf8",
            details=[{"file": str(path), "byte_offset": exc.start, "reason": exc.reason}],
        ) from None
    return parse_json_object_text(text, context, file=str(path))


def canonical_json(value: Any) -> str:
    """해시용 정규 JSON: 키 정렬, 공백 없음, 비ASCII 유지."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
