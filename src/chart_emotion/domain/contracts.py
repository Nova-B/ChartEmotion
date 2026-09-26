"""라벨 ID·판정 값·데이터 모드·출처 상태 등 v0.1 계약 상수.

기획 문서 `02_노래차트_아키텍처와_데이터계약.md`와 `04_노래차트_파일럿_운영계획.md`의 값을
코드로 고정한 것이다. 여기 없는 값은 검증 단계에서 거부된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# 라벨셋 v0.1
# ---------------------------------------------------------------------------

LABELSET_VERSION = "v0.1"
SUPPORTED_LABELSET_VERSIONS: tuple[str, ...] = (LABELSET_VERSION,)


@dataclass(frozen=True)
class LabelDefinition:
    label_id: str
    axis: str
    name_ko: str
    question_ko: str


LABELS: dict[str, LabelDefinition] = {
    "theme.romance": LabelDefinition(
        label_id="theme.romance",
        axis="theme",
        name_ko="연애·관계 주제",
        question_ko="연애·애착·이별 관계가 노래의 주제인가? '사랑'이라는 단어만으로 판정하지 않는다.",
    ),
    "emotion.anxiety": LabelDefinition(
        label_id="emotion.anxiety",
        axis="emotion",
        name_ko="불안 감정",
        question_ko="미래의 위협·불확실성·걱정·통제 상실이 맥락상 드러나는가? 이별의 슬픔만으로 넣지 않는다.",
    ),
    "function.comfort": LabelDefinition(
        label_id="function.comfort",
        axis="function",
        name_ko="위로·자기수용 기능",
        question_ko="고통을 인정하거나 존재의 가치·괜찮음·지지를 전달하는가? 단순히 밝은 가사와 구별한다.",
    ),
}

LABEL_IDS: tuple[str, ...] = tuple(LABELS)

# ---------------------------------------------------------------------------
# 판정 값. 빈값은 '미검토'이며 절대 absent 로 바꾸지 않는다.
# ---------------------------------------------------------------------------

ANNOTATION_VALUES: tuple[str, ...] = ("present", "absent", "uncertain")


class AnnotationState(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNCERTAIN = "uncertain"
    UNREVIEWED = "unreviewed"  # 저장 값이 아니라 '입력 없음'을 뜻하는 상태

    @property
    def is_decided(self) -> bool:
        """P/A 분모에 들어가는 확정 판정인지."""
        return self in (AnnotationState.PRESENT, AnnotationState.ABSENT)


def parse_annotation_value(raw: str | None) -> AnnotationState:
    """검토표 셀 값을 상태로 바꾼다.

    빈 문자열·None·공백은 UNREVIEWED 로 돌려주며 ABSENT 로 바꾸지 않는다.
    허용 값 이외의 문자열은 ValueError 로 거부한다.
    """
    if raw is None:
        return AnnotationState.UNREVIEWED
    text = raw.strip()
    if text == "":
        return AnnotationState.UNREVIEWED
    lowered = text.lower()
    if lowered in ANNOTATION_VALUES:
        return AnnotationState(lowered)
    raise ValueError(
        f"판정 값 '{raw}'은 허용되지 않습니다. 허용 값: {', '.join(ANNOTATION_VALUES)} 또는 빈칸(미검토)"
    )


# ---------------------------------------------------------------------------
# 데이터 모드·실험 설정 열거값
# ---------------------------------------------------------------------------

DATA_MODES: tuple[str, ...] = ("synthetic", "real")
COUNTING_UNITS: tuple[str, ...] = ("chart_entry",)
WEIGHTINGS: tuple[str, ...] = ("equal", "rank_log2")  # rank_log2 는 D08 민감도 분석용 예약값
ANNOTATION_ROLES: tuple[str, ...] = ("adopted", "independent")

# ---------------------------------------------------------------------------
# 출처(source) 등록 계약
# ---------------------------------------------------------------------------

SOURCE_STATUSES: tuple[str, ...] = ("pending", "permitted", "restricted")
SOURCE_OPERATIONS: tuple[str, ...] = ("import", "store", "analyze")
SOURCE_KINDS: tuple[str, ...] = ("chart", "lyrics", "metadata")

# ---------------------------------------------------------------------------
# 차트 정의
# ---------------------------------------------------------------------------

PERIODICITIES: tuple[str, ...] = ("daily", "weekly", "monthly", "yearly", "custom")
SNAPSHOT_STATUSES: tuple[str, ...] = ("complete", "incomplete")

# ---------------------------------------------------------------------------
# 식별자 규칙. 표시 이름과 독립된 ASCII 문자열만 허용한다.
# ---------------------------------------------------------------------------

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def is_valid_identifier(value: object) -> bool:
    return isinstance(value, str) and IDENTIFIER_PATTERN.fullmatch(value) is not None


# ---------------------------------------------------------------------------
# D05: 녹음(recording)·가사 버전·차트 항목 매핑 계약
# ---------------------------------------------------------------------------

VERSION_KINDS: tuple[str, ...] = ("original", "rerecording", "cover", "remix", "live", "translation", "other")
RELEASE_PRECISIONS: tuple[str, ...] = ("unknown", "year", "month", "day")
VOCAL_TYPES: tuple[str, ...] = ("lyrical", "instrumental")

# 가사 '메타데이터' 상태. 본문은 저장하지 않는다.
LYRIC_STATUSES: tuple[str, ...] = ("available", "partial", "translation_only", "missing", "not_applicable")
LYRIC_STATUSES_WITH_ACCESS: tuple[str, ...] = ("available", "partial", "translation_only")  # 출처·언어·참조가 필요한 상태

# 매핑 상태. 행이 없으면 'unmapped'(파생 상태)다. candidate 는 확정이 아니다.
MAPPING_STATES: tuple[str, ...] = ("candidate", "confirmed", "unresolved")
MAPPING_STATES_WITH_RECORDING: tuple[str, ...] = ("candidate", "confirmed")

LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")

# 문서 계약 버전(선택 필드 `schema`). 다른 값은 거부한다.
BATCH_SCHEMA = "chart-emotion.batch.v1"
EXPERIMENT_SCHEMA = "chart-emotion.experiment.v1"
RECORDINGS_SCHEMA = "chart-emotion.recordings.v1"
MAPPINGS_SCHEMA = "chart-emotion.mappings.v1"
