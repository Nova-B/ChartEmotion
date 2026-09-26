# 기반 단계 구현 상태 (D01~D04)

작성일: 2026-09-26. 구현: Claude Fable 5.1 (사용자 요청, Codex 감독).
기준 문서: `docs/planning/03_노래차트_개발로드맵과_검증.md` 의 실행 백로그.

**모든 실행·검증은 가상(synthetic) 자료로만 수행했다. 실제 차트·가사 분석은 없었다.**

## D01~D04 대응표

| 작업 | 인수 조건(기획) | 구현 위치 | 검증(테스트) | 상태 |
|---|---|---|---|---|
| D01 저장소·패키지·실행 방법·가상 모드 | 키·네트워크 없이 CLI 도움말과 예제 검증 | `pyproject.toml`, `src/chart_emotion/cli.py`, `__main__.py`, `README.md`, `examples/synthetic/` | `tests/test_cli.py` (도움말, `python -m chart_emotion` 무설치 실행, 전체 시연 순서, 종료 코드 0/2) | 완료 |
| D02 라벨 ID·상태·설정 계약 | 잘못된 라벨·기간·N 을 구체적인 오류로 거부 | `domain/contracts.py` (라벨 3개, present/absent/uncertain, 미입력=미검토), `domain/validation.py`, `domain/batch.py`, `domain/experiment.py` | `tests/test_contracts.py` (bool≠int, NaN/범위, 라벨·스냅샷 수·열거값·알 수 없는 필드·schema), `tests/test_batch_config.py` (날짜 형식·순서, 타입, 범위, 식별자, chart 블록) | 완료 |
| D03 SQLite 스키마·마이그레이션·소스 등록 | 빈 DB 생성, 외래키 점검, 지원하지 않는 스키마 버전 거부 | `storage/database.py` (schema v1, 명시적 마이그레이션, 모든 연결 `foreign_keys=ON`, 새 버전 거부·무변경, 미초기화 감지), `application/init_workspace.py`, `application/sources.py` (synthetic/real, pending/permitted/restricted, 작업별 허용, 근거·확인일) | `tests/test_storage.py` (테이블·FK·idempotent init·한글 경로·newer schema 바이트 불변·unknown layout), `tests/test_import_pipeline.py::SourceGateTest` | 완료 |
| D04 차트 CSV 검증·배치 입력 | 동일 입력 재실행 no-op, 잘못된 배치 부분 반영 0건 | `importers/chart_csv.py` (BOM·따옴표·줄바꿈·UTF-8 오류·헤더·셀 수·rank 규칙·누락 순위), `application/import_chart.py` (단일 트랜잭션, content/config/file 해시, no-op, `--revision` 개정), `application/validate.py` | `tests/test_chart_csv.py`, `tests/test_import_pipeline.py` (no-op, BOM 변형 no-op, 개정 충돌·추가, incomplete, 원자성 5종, 동명곡 미병합), `tests/test_validate.py` (합계·누락·superseded·실험 참조·비교 가능성) | 완료 |

테스트 실행 결과(2026-09-26, Codex 검토 반영 후): `python -m unittest discover -s tests -v` → 120 tests, OK.

## Codex 블랙박스 검토 반영 (2026-09-26)

| 발견 | 수정 | 회귀 테스트 |
|---|---|---|
| `coverage_threshold: 10**400` 이 float 변환 OverflowError 로 exit 1 | `check_number` 가 OverflowError 를 `not_finite` 입력 오류로 처리. `check_int` 는 64비트 범위를 넘는 정수를 `out_of_range` 로 거부. JSON 파서의 정수 자릿수 한계 ValueError·RecursionError 도 `json_value_error`(exit 2) 로 변환 | `test_contracts.test_huge_numbers_are_input_errors_not_crashes`, `test_batch_config.test_huge_integer_token_is_input_error`, `test_cli.test_hostile_inputs_exit_two_not_one` |
| JSON 중복 키를 마지막 값으로 조용히 수용 | `object_pairs_hook` 으로 중첩 객체까지 중복 키를 `json_duplicate_key`(exit 2) 로 거부 | `test_batch_config.test_duplicate_json_keys_rejected`, `test_import_pipeline.test_duplicate_key_batch_persists_nothing`, `test_cli.test_hostile_inputs_exit_two_not_one` |
| 5000자리 rank 가 int 변환 ValueError 로 exit 1 | int 변환 전에 자릿수로 범위를 판정해 `csv_rank_out_of_range` 행 오류로 보고(메시지는 20자리까지만 표시) | `test_chart_csv.test_huge_rank_digits_are_row_errors`, `test_import_pipeline.test_huge_rank_csv_persists_nothing` |
| 재검토 잔여: `'0'*5000` 은 자릿수 검사를 통과한 뒤 원본 문자열이 int 변환에 들어가 exit 1 | 앞자리 0 을 뗀 문자열만 변환한다(`'0'*5000` → 0 → 범위 오류, `'0'*5000+'1'` → 1 로 정규화). 원본 무제한 문자열은 변환하지 않음 | `test_chart_csv.test_thousands_of_leading_zeros_never_reach_int_conversion`, `test_import_pipeline.test_all_zero_rank_csv_persists_nothing` |
| 기간 길이 차이 1일까지 허용(6일 vs 7일 주간 통과) | 기획 계약대로 길이가 하루라도 다르면 `comparability_note` 없이는 차단. 윤년 366일 vs 365일 포함 | `test_validate.test_six_vs_seven_day_weekly_periods_blocked_without_note`, `test_leap_year_annual_periods_blocked_without_note`, `test_equal_length_periods_pass_without_note`, `test_incomplete_snapshot_rejected` |
| 요청하지 않은 MIT 라이선스 선언 | `pyproject.toml` 에서 제거하고 `Private :: Do Not Upload` 분류자로 비공개 표시. 라이선스는 사용자 결정 사항 | — |

## 기획 대비 결정 사항

- 차트 정의는 별도 명령 대신 첫 배치의 `chart` 블록으로 등록한다. 같은 트랜잭션 안에서 만들어지므로 배치가 거부되면 차트 정의도 남지 않는다.
- 스냅샷 ID 는 `<snapshot_key>-r<revision>` 이며 기획 문서 예시(`demo-period-a-r1`)와 일치한다.
- 동일성 판정에 원본 파일 해시가 아니라 정규화된 행 내용 해시를 쓴다. BOM·공백만 다른 파일은 같은 입력으로 본다. 원본 파일 해시는 별도로 기록한다.
- 배치 설정 동일성에는 필수 8개 필드만 넣는다. `snapshot_key`, `chart`, `notes` 는 제외한다.
- 실험 설정의 `weighting` 은 기획의 `equal` 외에 D08 민감도 분석용 `rank_log2` 를 예약값으로 허용한다.
- 스키마가 오래된 경우 읽기·쓰기 명령은 거부하고 `init` 만 마이그레이션한다(암묵적 마이그레이션 없음).
- 작업 폴더 전체 `validate` 는 incomplete 스냅샷을 경고로 보고하며 종료 코드 0. 실험 `validate` 는 참조 오류 시 종료 코드 2.
- 기간 길이는 완전히 같아야 한다. 하루 차이(윤년 포함)도 `comparability_note` 가 있을 때만 경고로 통과한다.
- 예제의 세 번째 기간(2020년)은 윤년이므로 2015·2025년과의 비교 실험에는 메모가 필요하다. 기간 길이 규칙의 예시로 그대로 둔다.

## 아직 만들지 않은 것 (D05 이후 — 미착수)

| 작업 | 상태 | 메모 |
|---|---|---|
| D05 곡·가사 버전 매칭 및 개정 | 미착수 | `recordings`, `entry_mappings`, `lyric_versions` 테이블은 이 단계에서 만들지 않았다. 현재 `validate` 의 "문자열 동일 행 수" 는 매칭이 아니다 |
| D06 검토표 왕복·판정 이력 | 미착수 | `annotation_revisions` 미생성. `parse_annotation_value` 계약만 존재 |
| D07 품질·미확보·검토 상태 보고 | 미착수 | P/A/U 집계 없음 |
| D08 집계·동시 출현·가중·조건별 비교 | 미착수 | T01~T04 손계산 검증 없음 |
| D09 보고서·그림 | 미착수 | Markdown/CSV/PNG 없음 |
| D10 run 고정·실패 복구 | 미착수 | `runs`, `experiment_revisions` 미생성 |
| D11 실제 실험 | 대기 | 실제 차트·가사 소스 이용 조건 미확인. 실제 출처는 `pending` 으로만 등록 가능하며 가져오기·분석이 차단된다 |
| D12·D13 화면·모델 분류 | 미착수 | — |

이 문서는 구현 상태 기록이며 기획 문서(`docs/planning/*.md`)를 수정하지 않았다.
