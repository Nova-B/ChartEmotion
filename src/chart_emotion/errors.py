"""사용자에게 보여줄 오류 계층.

- InputError (exit 2): 입력·전제조건 오류. 파일 형식, 설정 값, 미초기화 작업 폴더,
  출처 미허가, 개정 충돌 등 사용자가 고칠 수 있는 문제.
- ProcessingError (exit 1): 처리 실패. 저장소 손상, 예상하지 못한 DB 오류 등.

모든 오류는 `code`(기계용 식별자), `message`(한국어 설명), `details`(행·열·필드 단위 목록)를 가진다.
"""

from __future__ import annotations

from typing import Any


class ChartEmotionError(Exception):
    exit_code = 1
    default_code = "processing_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details: list[dict[str, Any]] = list(details or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "exit_code": self.exit_code,
            "details": self.details,
        }


class InputError(ChartEmotionError):
    """입력 또는 전제조건 오류. 저장소는 변경되지 않는다."""

    exit_code = 2
    default_code = "input_error"


class WorkspaceError(InputError):
    """작업 폴더 상태 오류(미초기화, 지원하지 않는 스키마 버전 등)."""

    default_code = "workspace_error"


class ProcessingError(ChartEmotionError):
    exit_code = 1
    default_code = "processing_error"
