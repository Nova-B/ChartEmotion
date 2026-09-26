"""JSON 설정 검증용 작은 도우미. 타입을 엄격하게 본다(bool 은 정수가 아니다)."""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Iterable

from ..errors import InputError

# ASCII 숫자만 허용한다. `\d` 는 유니코드 숫자(예: 아라비아-인도 숫자)도 받으므로 쓰지 않는다.
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_MISSING = object()


class FieldErrors:
    """필드 단위 오류를 모아 한 번에 InputError 로 올린다."""

    def __init__(self, context: str) -> None:
        self.context = context
        self.items: list[dict[str, Any]] = []

    def add(self, field: str, code: str, message: str) -> None:
        self.items.append({"field": field, "code": code, "message": message})

    def __bool__(self) -> bool:
        return bool(self.items)

    def raise_if_any(self, code: str, message: str) -> None:
        if self.items:
            raise InputError(f"{self.context}: {message}", code=code, details=self.items)


def require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InputError(
            f"{context}: 최상위 값은 JSON 객체여야 합니다 (현재: {type(value).__name__})",
            code="json_not_object",
        )
    return value


def check_unknown_fields(errors: FieldErrors, obj: dict[str, Any], allowed: Iterable[str]) -> None:
    allowed_set = set(allowed)
    for key in obj:
        if key not in allowed_set:
            errors.add(key, "unknown_field", f"알 수 없는 필드입니다. 허용 필드: {', '.join(sorted(allowed_set))}")


def check_str(
    errors: FieldErrors,
    obj: dict[str, Any],
    field: str,
    *,
    required: bool = True,
    allow_empty: bool = False,
) -> str | None:
    value = obj.get(field, _MISSING)
    if value is _MISSING or value is None:
        if required:
            errors.add(field, "missing_field", "필수 필드가 없습니다")
        return None
    if not isinstance(value, str):
        errors.add(field, "wrong_type", f"문자열이어야 합니다 (현재: {type(value).__name__})")
        return None
    if not allow_empty and value.strip() == "":
        errors.add(field, "empty_value", "빈 문자열은 허용되지 않습니다")
        return None
    return value


def check_identifier(errors: FieldErrors, obj: dict[str, Any], field: str, *, required: bool = True) -> str | None:
    from .contracts import IDENTIFIER_PATTERN

    value = check_str(errors, obj, field, required=required)
    if value is None:
        return None
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        errors.add(
            field,
            "invalid_identifier",
            "식별자는 영문·숫자로 시작하고 영문·숫자·'.'·'_'·'-' 만 포함하는 100자 이하 문자열이어야 합니다",
        )
        return None
    return value


def check_int(
    errors: FieldErrors,
    obj: dict[str, Any],
    field: str,
    *,
    required: bool = True,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    value = obj.get(field, _MISSING)
    if value is _MISSING or value is None:
        if required:
            errors.add(field, "missing_field", "필수 필드가 없습니다")
        return None
    # bool 은 int 의 하위 타입이지만 정수로 받지 않는다.
    if isinstance(value, bool) or not isinstance(value, int):
        errors.add(field, "wrong_type", f"정수여야 합니다 (현재: {type(value).__name__})")
        return None
    if abs(value) >= 2**63:
        errors.add(field, "out_of_range", "정수가 너무 큽니다 (64비트 범위를 넘는 값은 받지 않습니다)")
        return None
    if minimum is not None and value < minimum:
        errors.add(field, "out_of_range", f"{minimum} 이상이어야 합니다 (현재: {value})")
        return None
    if maximum is not None and value > maximum:
        errors.add(field, "out_of_range", f"{maximum} 이하여야 합니다 (현재: {value})")
        return None
    return value


def check_number(
    errors: FieldErrors,
    obj: dict[str, Any],
    field: str,
    *,
    required: bool = True,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    value = obj.get(field, _MISSING)
    if value is _MISSING or value is None:
        if required:
            errors.add(field, "missing_field", "필수 필드가 없습니다")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.add(field, "wrong_type", f"숫자여야 합니다 (현재: {type(value).__name__})")
        return None
    try:
        number = float(value)
    except OverflowError:
        # 매우 큰 정수 토큰(예: 10**400)은 float 로 바꿀 수 없다. 처리 실패가 아니라 입력 오류다.
        errors.add(field, "not_finite", "숫자가 너무 커서 처리할 수 없습니다 (유한한 범위의 값을 쓰세요)")
        return None
    if not math.isfinite(number):
        errors.add(field, "not_finite", "유한한 숫자여야 합니다 (NaN·무한대 불가)")
        return None
    if minimum is not None and number < minimum:
        errors.add(field, "out_of_range", f"{minimum} 이상이어야 합니다 (현재: {value})")
        return None
    if maximum is not None and number > maximum:
        errors.add(field, "out_of_range", f"{maximum} 이하여야 합니다 (현재: {value})")
        return None
    return number


def check_enum(
    errors: FieldErrors,
    obj: dict[str, Any],
    field: str,
    allowed: Iterable[str],
    *,
    required: bool = True,
) -> str | None:
    allowed_tuple = tuple(allowed)
    value = check_str(errors, obj, field, required=required)
    if value is None:
        return None
    if value not in allowed_tuple:
        errors.add(field, "invalid_enum", f"허용 값: {', '.join(allowed_tuple)} (현재: '{value}')")
        return None
    return value


def parse_iso_date(text: str) -> date | None:
    """ASCII YYYY-MM-DD 형식만 허용. 연도 0000 이나 달력상 존재하지 않는 날짜(평년 2월 29일 등)는 None."""
    if DATE_PATTERN.fullmatch(text) is None:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.year >= 1 else None


def check_date(errors: FieldErrors, obj: dict[str, Any], field: str, *, required: bool = True) -> date | None:
    value = check_str(errors, obj, field, required=required)
    if value is None:
        return None
    parsed = parse_iso_date(value)
    if parsed is None:
        errors.add(field, "invalid_date", f"YYYY-MM-DD 형식의 실재하는 날짜여야 합니다 (현재: '{value}')")
        return None
    return parsed


def check_object_list(
    errors: FieldErrors,
    obj: dict[str, Any],
    field: str,
    *,
    required: bool = True,
    min_items: int = 0,
) -> list[dict[str, Any]] | None:
    """객체 배열 필드. 항목이 객체가 아니면 인덱스와 함께 오류를 남긴다."""
    value = obj.get(field, _MISSING)
    if value is _MISSING or value is None:
        if required:
            errors.add(field, "missing_field", "필수 필드가 없습니다")
        return None
    if not isinstance(value, list):
        errors.add(field, "wrong_type", f"객체 배열이어야 합니다 (현재: {type(value).__name__})")
        return None
    ok = True
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.add(f"{field}[{index}]", "wrong_type", f"객체여야 합니다 (현재: {type(item).__name__})")
            ok = False
    if not ok:
        return None
    if len(value) < min_items:
        errors.add(field, "too_few_items", f"항목이 {min_items}개 이상이어야 합니다 (현재: {len(value)})")
        return None
    return value


def check_str_list(
    errors: FieldErrors,
    obj: dict[str, Any],
    field: str,
    *,
    required: bool = True,
    min_items: int | None = None,
    max_items: int | None = None,
    unique: bool = True,
) -> list[str] | None:
    value = obj.get(field, _MISSING)
    if value is _MISSING or value is None:
        if required:
            errors.add(field, "missing_field", "필수 필드가 없습니다")
        return None
    if not isinstance(value, list):
        errors.add(field, "wrong_type", f"문자열 배열이어야 합니다 (현재: {type(value).__name__})")
        return None
    items: list[str] = []
    ok = True
    for index, item in enumerate(value):
        if not isinstance(item, str) or item.strip() == "":
            errors.add(f"{field}[{index}]", "wrong_type", "비어 있지 않은 문자열이어야 합니다")
            ok = False
        else:
            items.append(item)
    if not ok:
        return None
    if min_items is not None and len(items) < min_items:
        errors.add(field, "too_few_items", f"항목이 {min_items}개 이상이어야 합니다 (현재: {len(items)})")
        return None
    if max_items is not None and len(items) > max_items:
        errors.add(field, "too_many_items", f"항목이 {max_items}개 이하여야 합니다 (현재: {len(items)})")
        return None
    if unique and len(set(items)) != len(items):
        duplicates = sorted({item for item in items if items.count(item) > 1})
        errors.add(field, "duplicate_items", f"중복 항목이 있습니다: {', '.join(duplicates)}")
        return None
    return items
