# ChartEmotion — 차트 정서 관측소 (D01~D05, M1)

> **가상 자료(synthetic) 시연용 코드입니다.** 이 저장소의 예제와 실행 결과는 직접 작성한 합성 데이터이며 실제 차트·실제 곡·실제 가사가 아닙니다.
> 현재 구현은 **차트 CSV 검증·보관, 녹음·가사 버전 메타데이터 등록, 차트 항목 매핑 개정 이력, 실험 설정 점검**까지입니다. 가사 본문 저장, 라벨링, 집계, 보고서는 아직 없으며 **어떤 정서 점수도 계산하지 않습니다.**

기획 문서는 `docs/planning/` 에 있습니다(개발 착수 시점의 계획 사본이며 구현 완료의 증거가 아닙니다). 구현 상태는 [`docs/foundation_status.md`](docs/foundation_status.md), D05 매핑 계약은 [`docs/d05_matching_contract.md`](docs/d05_matching_contract.md) 에서 확인합니다.

## 1. 요구 사항

- Windows 11, Python 3.11 이상 (표준 라이브러리만 사용: argparse, sqlite3, csv, json, unittest)
- 외부 패키지 설치·네트워크 연결·API 키 불필요

## 2. 실행 준비 (Windows PowerShell)

패키지를 설치하지 않고 소스에서 바로 실행합니다. 저장소 루트에서:

```powershell
cd C:\Users\admin\Desktop\Dev\ChartEmotion
$env:PYTHONPATH = "$PWD\src"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
python -m chart_emotion --help
```

`PYTHONPATH` 는 현재 PowerShell 창에서만 유효합니다. 새 창을 열면 다시 설정하세요.
출력을 파일로 저장할 때 한글이 깨지면 `$env:PYTHONIOENCODING = "utf-8"` 을 추가로 설정합니다.

선택 사항: `pip install -e .` 로 설치하면 `chart-emotion` 명령을 바로 쓸 수 있습니다. 이 README 의 예시는 설치 없이 동작하는 `python -m chart_emotion` 형식을 사용합니다.

## 3. 시연 순서: init → import 두 번 → validate

아래를 그대로 붙여 넣으면 됩니다(2절의 준비 명령을 먼저 실행한 상태).

```powershell
python -m chart_emotion init .\data\demo-workspace
python -m chart_emotion import-chart .\examples\synthetic\charts\period_a.csv --batch .\examples\synthetic\batches\period_a.json --workspace .\data\demo-workspace
python -m chart_emotion import-chart .\examples\synthetic\charts\period_b.csv --batch .\examples\synthetic\batches\period_b.json --workspace .\data\demo-workspace
python -m chart_emotion validate --workspace .\data\demo-workspace
python -m chart_emotion validate --workspace .\data\demo-workspace --experiment .\examples\synthetic\experiments\demo_comparison.json
```

기대 결과:

- `init`: `data\demo-workspace\chart_emotion.sqlite3` 생성, 스키마 버전 2, 가상 출처 `synthetic-demo` 등록. 다시 실행해도 기존 자료를 보존합니다.
- 두 `import-chart`: 스냅샷 `demo-period-a-r1`, `demo-period-b-r1` 에 각각 5개 항목. 가상곡 8개 중 2곡(`demo-001`, `demo-002`)이 두 기간에 함께 등장합니다.
- `validate`: 항목 합계 10, 누락 순위 없음, 매칭 `not_started`(아직 매핑 없음), 라벨링 `not_analyzed`, 분석 `not_performed`.
- `validate --experiment`: 두 스냅샷이 같은 차트·같은 N·같은 산정 방식·서로 다른 기간임을 확인하고 `ok: true`. 매칭 미완료는 `matching_incomplete` 경고로만 표시됩니다. 정서 수치는 없습니다.

같은 파일을 다시 넣으면 `already_imported` 로 끝나고 행이 늘지 않습니다:

```powershell
python -m chart_emotion import-chart .\examples\synthetic\charts\period_b.csv --batch .\examples\synthetic\batches\period_b.json --workspace .\data\demo-workspace
```

### 오류·정정 시연

```powershell
# 중복 순위 → 종료 코드 2, DB 변경 없음
python -m chart_emotion import-chart .\examples\synthetic\invalid\duplicate_rank.csv --batch .\examples\synthetic\batches\period_c.json --workspace .\data\demo-workspace
# 필수 열 누락 → 종료 코드 2
python -m chart_emotion import-chart .\examples\synthetic\invalid\missing_column.csv --batch .\examples\synthetic\batches\period_c.json --workspace .\data\demo-workspace
# 기간 종료일이 시작일보다 앞섬 → 종료 코드 2, 새 차트 정의도 만들어지지 않음
python -m chart_emotion import-chart .\examples\synthetic\charts\period_a.csv --batch .\examples\synthetic\invalid\invalid_dates.json --workspace .\data\demo-workspace
# 순위 3 누락 배치 → 저장은 되지만 incomplete (비교 차단)
python -m chart_emotion import-chart .\examples\synthetic\charts\period_c_incomplete.csv --batch .\examples\synthetic\batches\period_c.json --workspace .\data\demo-workspace
# 같은 기간의 정정본 → --revision 없이는 거부(종료 코드 2), 기존 스냅샷은 덮어쓰지 않음
python -m chart_emotion import-chart .\examples\synthetic\charts\period_c_corrected.csv --batch .\examples\synthetic\batches\period_c.json --workspace .\data\demo-workspace
# --revision 2 로 새 불변 스냅샷 demo-period-c-r2 추가
python -m chart_emotion import-chart .\examples\synthetic\charts\period_c_corrected.csv --batch .\examples\synthetic\batches\period_c.json --workspace .\data\demo-workspace --revision 2
# 같은 제목의 다른 버전 3행이 각각 별도 항목으로 보관되는지 확인
python -m chart_emotion import-chart .\examples\synthetic\versions\same_title_versions.csv --batch .\examples\synthetic\versions\batch_versions.json --workspace .\data\demo-workspace
python -m chart_emotion validate --workspace .\data\demo-workspace
```

`$LASTEXITCODE` 로 직전 명령의 종료 코드를 확인할 수 있습니다.

### D05 시연: 녹음·가사 버전 등록 → 매핑 → 정정 → 조회

3절의 기본 시연(두 기간 가져오기)을 마친 상태에서 이어서 실행합니다.

```powershell
# 1) 가사 메타데이터 출처 등록 (가상). 가사 본문은 어디에도 저장하지 않습니다
python -m chart_emotion register-source --workspace .\data\demo-workspace --source-id synthetic-lyrics --name "가상 가사 메타데이터 (합성)" --kind lyrics --data-mode synthetic
# 2) 녹음 10건(원곡 8 + 재녹음 1 + 커버 1)과 가사 버전 10건 등록. 다시 실행하면 '변경 없음'
python -m chart_emotion import-recordings .\examples\synthetic\recordings\recordings.json --workspace .\data\demo-workspace
# 3) 기간 A·B 항목 매핑 (confirmed 7, candidate 2, unresolved 1)
python -m chart_emotion import-mappings .\examples\synthetic\mappings\period_a_initial.json --workspace .\data\demo-workspace
python -m chart_emotion import-mappings .\examples\synthetic\mappings\period_b_initial.json --workspace .\data\demo-workspace
# 4) 정정: 후보 확정 2건, 후보 철회 1건 → 각각 revision 2
python -m chart_emotion import-mappings .\examples\synthetic\mappings\corrections.json --workspace .\data\demo-workspace
# 5) 오래된 검토표 → stale_base_revision, 종료 코드 2, 배치 전체 미반영
python -m chart_emotion import-mappings .\examples\synthetic\mappings\stale_conflict.json --workspace .\data\demo-workspace
# 6) 철회했던 항목을 다른 검토자가 재확정 → revision 3 (이전 revision 은 그대로 남음)
python -m chart_emotion import-mappings .\examples\synthetic\mappings\reconfirm.json --workspace .\data\demo-workspace
# 7) 조회: 항목별 현재 상태와 전체 이력
python -m chart_emotion show-mappings --workspace .\data\demo-workspace --snapshot demo-period-b-r1 --history
# 8) 실험 검증에 매칭 진행과 가사 상태가 두 스냅샷의 최신 매핑 범위로 표시됨 (matching.status: complete, lyrics.scope: selected_snapshots_latest_mappings)
python -m chart_emotion validate --workspace .\data\demo-workspace --experiment .\examples\synthetic\experiments\demo_comparison.json
```

오류 예시:

```powershell
# 다른 녹음의 가사 버전을 고름 → lyric_recording_mismatch, 종료 코드 2
python -m chart_emotion import-mappings .\examples\synthetic\invalid\mapping_lyric_mismatch.json --workspace .\data\demo-workspace
# 정밀도와 날짜 불일치, bool 타입, 가사 본문 필드 → recordings_file_invalid, 종료 코드 2
python -m chart_emotion import-recordings .\examples\synthetic\invalid\recordings_invalid.json --workspace .\data\demo-workspace
# 같은 제목 3개 버전(원곡·재녹음·커버)이 각각 다른 녹음으로 확정되는지 확인
python -m chart_emotion import-chart .\examples\synthetic\versions\same_title_versions.csv --batch .\examples\synthetic\versions\batch_versions.json --workspace .\data\demo-workspace
python -m chart_emotion import-mappings .\examples\synthetic\mappings\versions_chart.json --workspace .\data\demo-workspace
python -m chart_emotion show-mappings --workspace .\data\demo-workspace --snapshot demo-versions-r1
```

### 기존 작업 폴더 마이그레이션 (스키마 v1 → v2)

이전 버전(0.1.x)으로 만든 작업 폴더는 스키마 버전 1 입니다. 모든 명령이 `schema_migration_required`(종료 코드 2)로 거부되며 아무것도 바꾸지 않습니다. `init` 을 한 번 더 실행하면 기존 출처·차트·스냅샷·항목을 그대로 둔 채 `recordings`, `lyric_versions`, `entry_mappings` 테이블만 추가됩니다.

```powershell
python -m chart_emotion init .\data\demo-workspace   # schema_version_before: 1 → schema_version: 2, migrated: true
```

프로그램보다 새로운 스키마 버전은 `init` 을 포함한 모든 명령이 거부합니다. 마이그레이션 전에 DB 파일을 복사해 두는 것을 권합니다.

## 4. 명령과 종료 코드

| 명령 | 역할 |
|---|---|
| `init <workspace>` | 폴더·DB 생성, 마이그레이션, 가상 출처 `synthetic-demo` 시드. 재실행 시 자료 보존 |
| `register-source --workspace <path> ...` | 출처와 허용 작업 등록(5절) |
| `import-chart <csv> --batch <json> --workspace <path> [--revision N]` | CSV 전체 검증 후 트랜잭션 하나로 스냅샷 보관 |
| `import-recordings <json> --workspace <path>` | 녹음·가사 버전 메타데이터를 불변 등록. 같은 ID 같은 내용은 no-op, 다른 내용은 거부 |
| `import-mappings <json> --workspace <path>` | 차트 항목 ↔ 녹음(·가사 버전) 판정을 새 revision 으로 추가. `base_revision` 불일치 시 배치 전체 거부 |
| `show-mappings --workspace <path> [--snapshot <id>] [--history]` | 항목별 현재 매핑 상태·진행 요약·전체 이력 조회 |
| `validate --workspace <path> [--experiment <json>]` | 작업 폴더 상태 보고 또는 실험 설정·참조·매칭 진행 검증 |

D05 입력 계약(JSON 필드·상태·동시성 규칙)은 [`docs/d05_matching_contract.md`](docs/d05_matching_contract.md) 에 있습니다.

| 종료 코드 | 의미 |
|---|---|
| 0 | 성공 (no-op 포함) |
| 2 | 입력·전제조건 오류: 파일 형식, 설정 값, 미초기화 폴더, 출처 미허가, 개정 충돌, 실험 참조 오류. **저장소 변경 없음** |
| 1 | 처리 실패: SQLite 오류, 예상하지 못한 예외 (`--debug` 로 traceback 출력) |

성공 결과는 stdout, 오류는 stderr 에 UTF-8 JSON 으로 출력합니다. `validate --experiment` 는 참조 오류가 있으면 보고서를 stdout 에 출력하고 종료 코드 2 를 돌려줍니다. 작업 폴더 전체 `validate` 는 불완전 스냅샷 등을 `issues` 로 나열하되 저장소 자체가 손상되지 않았으면 0 으로 끝납니다.

## 5. 출처(source) 등록과 허용 규칙

`init` 이 만드는 `synthetic-demo` 는 가상 자료 전용 출처입니다. 실제 자료는 반드시 별도로 등록합니다.

```powershell
# 실제 출처를 '미확인(pending)' 으로 등록: 가져오기·분석이 차단됨
python -m chart_emotion register-source --workspace .\data\demo-workspace --source-id example-real-chart --name "실제 차트 예시(미확인)" --kind chart --data-mode real
# 이용 조건을 확인한 뒤에만 permitted 로 갱신 (확인일·근거 필수, 허용 작업은 명시한 것만)
python -m chart_emotion register-source --workspace .\data\demo-workspace --source-id example-real-chart --name "실제 차트 예시" --kind chart --data-mode real --status permitted --allow import --allow store --checked-on 2026-09-26 --checked-by "사용자" --evidence "이용 약관 §x 또는 계약 문서 경로" --replace
```

규칙:

- `data_mode` 는 `synthetic` / `real`, `status` 는 `pending` / `permitted` / `restricted`, 허용 작업은 `import` / `store` / `analyze`.
- 허용 작업은 `permitted` 상태에서만 부여할 수 있습니다. `real` + `permitted` 는 `--checked-on` 과 `--evidence` 가 없으면 등록되지 않습니다. 프로그램이 허락을 지어내지 않습니다.
- synthetic 배치는 synthetic 출처만, real 배치는 real 출처만 사용할 수 있습니다. 가져오기에는 `import`+`store`, 실험 검증에는 `analyze` 허용이 필요합니다.
- 이 단계에는 공급자 사이트 접근·자동 수집 기능이 없습니다.

## 6. 입력 계약

### 6.1 배치 설정 JSON (`--batch`)

| 필드 | 필수 | 타입·규칙 |
|---|---|---|
| `source_id` | 필수 | 등록된 출처 식별자 (영문·숫자로 시작, `. _ -` 허용) |
| `chart_id` | 필수 | 차트 식별자. 처음 쓰는 chart_id 는 `chart` 블록 필요 |
| `period_start` / `period_end` | 필수 | `YYYY-MM-DD`, 실재하는 날짜, `period_end >= period_start` |
| `display_year` | 필수 | 정수(1000~9999). 공급자 표시 연도일 뿐, 기간을 여기서 유도하지 않음 |
| `methodology_version` | 필수 | 문자열. 산정 방식 버전 |
| `expected_n` | 필수 | 정수 1~1000. rank 의 상한이자 완전성 기준 |
| `data_mode` | 필수 | `synthetic` / `real` |
| `schema` | 선택 | 있으면 `chart-emotion.batch.v1` 이어야 함 |
| `snapshot_key` | 선택 | 스냅샷 ID 접두어. 기본값 `<chart_id>-<start>-<end>`. 스냅샷 ID 는 `<snapshot_key>-r<revision>` |
| `chart` | 선택 | `{name, market, metric, periodicity}`. periodicity 는 `daily/weekly/monthly/yearly/custom`. 등록된 차트와 market·metric·periodicity 가 다르면 거부 |
| `notes` | 선택 | 메모 |

`bool` 은 정수로 받지 않으며(`true` ≠ 1) 알 수 없는 필드는 거부합니다. 스냅샷 동일성은 위 필수 8개 필드의 정규 JSON 해시(`config_sha256`)와 행 내용 해시(`content_sha256`)로 판정합니다. 원본 파일 해시(`file_sha256`)도 함께 기록합니다.

### 6.2 차트 CSV

```csv
rank,title,artist,provider_track_id
1,가상곡 가,가상가수 A,demo-001
2,"가상곡 나, 쉼표 포함",가상가수 B,
```

- 헤더 네 열이 모두 있어야 합니다(순서 무관, 중복·추가 열 불가). `provider_track_id` 열 자체는 필수이고 값은 비워도 됩니다.
- UTF-8 (BOM 유무 무관). 다른 인코딩(cp949 등)은 `csv_invalid_utf8` 로 거부합니다.
- 날짜·연도 값은 모두 ASCII 숫자만 받습니다(유니코드·전각 숫자 불가). 연도 범위는 0001~9999 이며 존재하지 않는 날짜(평년 2월 29일 등)는 거부합니다.
- `rank` 는 1..`expected_n` 의 양의 정수, 파일 안에서 유일. 앞자리 0 을 뗀 유효 자릿수가 `expected_n` 보다 많으면(수천 자리 숫자 포함) 행 오류로 보고하며, `0001` 처럼 0 으로 채운 값은 1 로 읽습니다. `title`, `artist` 는 비어 있을 수 없습니다.
- 따옴표 안의 쉼표·줄바꿈·`""` 는 표준 CSV 규칙을 따릅니다. 빈 파일·헤더만 있는 파일·셀 수가 다른 행·닫히지 않은 따옴표는 오류입니다.
- 누락 순위가 있으면 저장은 되지만 스냅샷이 `incomplete` 가 되어 실험 검증에서 거부됩니다.
- 오류가 하나라도 있으면 파일명·행·열·오류 코드를 출력하고 DB 를 바꾸지 않습니다.

### 6.3 실험 설정 JSON (`validate --experiment`)

| 필드 | 필수 | 규칙 |
|---|---|---|
| `experiment_id` | 필수 | 식별자 |
| `revision` | 필수 | 정수 ≥ 1 |
| `data_mode` | 필수 | `synthetic` / `real`. 참조 스냅샷과 같아야 함 |
| `snapshot_ids` | 필수 | 서로 다른 스냅샷 ID **정확히 2개** |
| `top_n` | 필수 | 정수 1~1000. 두 스냅샷의 `expected_n` 과 같아야 함 |
| `labelset_version` | 필수 | `v0.1` |
| `labels` | 필수 | `theme.romance`, `emotion.anxiety`, `function.comfort` 중 1개 이상, 중복 불가 |
| `counting_unit` | 필수 | `chart_entry` |
| `weighting` | 필수 | `equal` (또는 D08 예약값 `rank_log2`) |
| `annotation_role` | 필수 | `adopted` / `independent` |
| `coverage_threshold` | 필수 | 0~1 유한 실수 |
| `schema`, `question`, `comparability_note` | 선택 | `schema` 는 `chart-emotion.experiment.v1`. 산정 방식·기간 길이가 다른 비교는 `comparability_note` 가 있을 때만 경고로 통과 |

참조 검증 항목: 스냅샷 존재, `complete` 상태, 같은 차트, 서로 다른 기간, 기간 길이, 산정 방식, N, data_mode, 출처의 `analyze` 허용, 최신 revision 여부(경고).

기간 길이 규칙: 두 스냅샷의 `period_start`~`period_end` 일수가 **하루라도 다르면** 기본 실행을 차단합니다(`period_length_mismatch`, 종료 코드 2). 6일짜리 주간과 7일짜리 주간, 윤년 366일과 평년 365일도 예외가 아닙니다. 비교 가능성을 검토했다면 실험 설정에 `comparability_note` 를 남기세요. 그러면 오류 대신 경고로 통과하고 메모가 보고에 함께 표시됩니다. 프로그램이 이 예외를 자동으로 만들지 않습니다.

```json
{ "...": "...", "comparability_note": "2020년 차트는 윤년(366일)이며 집계 방식은 동일함을 확인함" }
```

JSON 설정 파일에서 같은 객체 안에 같은 키가 두 번 나오면(`"top_n"` 이 두 번 등) 어느 값이 맞는지 알 수 없으므로 `json_duplicate_key` 로 거부합니다. 처리 범위를 넘는 큰 숫자(예: 400자리 정수)는 처리 실패가 아니라 입력 오류(종료 코드 2)로 보고합니다.

## 7. 저장소 위치와 확인 방법

- 작업 폴더는 `init` 에 준 경로이며 DB 는 그 안의 `chart_emotion.sqlite3` 하나입니다. 이 README 는 `data\demo-workspace` 를 쓰며 `data/` 는 `.gitignore` 로 제외됩니다.
- 테이블(스키마 v2): `schema_meta`, `sources`, `chart_definitions`, `chart_snapshots`, `chart_entries`, `recordings`, `lyric_versions`, `entry_mappings`. 항목의 유일성은 `(snapshot_id, rank)`, 스냅샷의 유일성은 `(chart_id, period_start, period_end, revision)`, 매핑의 유일성은 `(snapshot_id, rank, revision)` 이며 매핑 ID 는 `<snapshot_id>.rank<rank>.r<revision>` 입니다.
- `sqlite3` 명령이 없어도 Python 으로 볼 수 있습니다:

```powershell
python -c "import sqlite3; c = sqlite3.connect(r'data\demo-workspace\chart_emotion.sqlite3'); [print(r) for r in c.execute('SELECT snapshot_id, revision, status, period_start, period_end FROM chart_snapshots')]"
python -c "import sqlite3; c = sqlite3.connect(r'data\demo-workspace\chart_emotion.sqlite3'); [print(r) for r in c.execute('SELECT snapshot_id, rank, title, artist, provider_track_id FROM chart_entries ORDER BY snapshot_id, rank')]"
python -c "import sqlite3; c = sqlite3.connect(r'data\demo-workspace\chart_emotion.sqlite3'); [print(r) for r in c.execute('SELECT mapping_id, state, recording_id, lyric_version_id, reviewer_id FROM entry_mappings ORDER BY snapshot_id, rank, revision')]"
```

DB Browser for SQLite 같은 도구로 열어도 됩니다. 스키마 버전이 프로그램보다 새로우면 모든 명령이 아무것도 바꾸지 않고 거부합니다.

## 8. 테스트

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m unittest discover -s tests -v
```

임시 폴더만 사용하고 네트워크·실제 자료를 쓰지 않습니다. 시나리오: 전체 시연 파이프라인, BOM·한글·따옴표 CSV, 빈·헤더만·깨진 CSV, 스키마 버전 거부, v1→v2 마이그레이션(자료 보존·마이그레이션 전 거부), 출처 게이트, 동일 입력 no-op, 개정 충돌·새 revision, 누락 순위 incomplete, 잘못된 배치의 테이블 간 원자성, 동명곡 미병합, 녹음·가사 버전 불변 등록과 발매일 정밀도, 매핑 stale/재입력/이력 불변, 다른 녹음의 가사 버전 거부, 가상·실제 혼합 거부, 매칭 진행 범위, 실험 설정 타입·라벨·참조 오류, CLI 종료 코드.

## 9. 폴더 구조

```text
ChartEmotion/
  pyproject.toml            # 패키지 메타데이터, chart-emotion 콘솔 진입점
  README.md
  docs/planning/            # 개발 착수 시점의 기획 문서 사본
  docs/foundation_status.md # D01~D05 구현·테스트 대응표, D06 이후 미구현
  docs/d05_matching_contract.md # 녹음·가사 버전·매핑 JSON 계약과 상태 규칙
  src/chart_emotion/
    cli.py                  # argparse CLI, 종료 코드
    errors.py
    domain/                 # 라벨·판정값·배치·실험·녹음·매핑 계약, 순수 검증
    importers/chart_csv.py  # CSV 검증·해시
    storage/database.py     # SQLite 스키마 v1·v2, 마이그레이션, 외래키
    application/            # init, 출처 등록·게이트, 차트·녹음·매핑 가져오기, 조회, validate
  examples/synthetic/       # 가상 자료 예제 (README 참고)
  tests/                    # unittest
  data/                     # 로컬 작업 폴더 (git 제외)
```

## 10. 현재 한계

- D06 이후(검토표 왕복, 라벨 상태, P/A/U 집계, 보고서, run 고정)는 구현되지 않았습니다. `annotation_revisions`, `experiment_revisions`, `runs` 테이블은 필요한 시점에 새 마이그레이션으로 추가합니다.
- 매핑은 사람이 JSON 으로 판정한 것만 기록합니다. 후보 자동 제안·문자열 유사도 매칭 기능은 없으며, `candidate` 상태도 사람이 적은 것입니다.
- 가사는 상태·언어·출처·참조 메타데이터만 다룹니다. 본문 저장·확보·전송 기능은 없습니다.
- 실험 설정 검증은 구조·스냅샷 참조·매칭 진행만 확인하며 실험을 저장하지 않습니다.
- 단일 순위 차트만 지원하며 공동 순위는 받지 않습니다.
- 라이선스는 아직 정하지 않았습니다. `pyproject.toml` 은 비공개(`Private :: Do Not Upload`)로 표시되어 있으며 배포용이 아닙니다.
- 실제 차트·가사 자료의 이용 조건은 확인되지 않았습니다. 이 프로그램은 어떤 실제 분석도 수행한 적이 없습니다.
