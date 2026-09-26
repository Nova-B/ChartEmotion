# D05 계약: 녹음·가사 버전 등록과 차트 항목 매핑

작성일: 2026-09-26. 구현: Claude Fable 5.1. 기획 문서 `02_노래차트_아키텍처와_데이터계약.md` 3절(데이터 관계)과
`03_노래차트_개발로드맵과_검증.md` D05("동명곡 미병합, 같은 곡 중복 기간 매핑 가능")를 실행 가능한 계약으로 구체화한 것이다.

**가사 본문은 어떤 명령·테이블에도 저장하지 않는다. 라벨·집계·정서 수치는 이 단계에 없다.**

## 1. 개념

| 개념 | 정의 | 식별자 |
|---|---|---|
| 차트 항목 | 스냅샷의 한 순위 행(원표기 제목·아티스트·provider_track_id) | `(snapshot_id, rank)` |
| 녹음(recording) | 특정 버전의 한 곡. 원곡·재녹음·커버·리믹스·라이브·번역은 서로 다른 녹음 | `recording_id` |
| 가사 버전 | 녹음 하나에 속하는 가사 메타데이터(상태·언어·출처·참조) | `lyric_version_id` |
| 매핑 | 차트 항목이 어느 녹음(·가사 버전)인지에 대한 사람의 판정. append-only 개정 | `mapping_id` = `<snapshot_id>.rank<rank>.r<revision>` |

모든 ID 는 제목·아티스트·provider_track_id 와 무관한 독립 문자열(`^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$`)이다.
프로그램은 문자열이나 provider ID 가 같다는 이유로 어떤 것도 병합·자동 확정하지 않는다.

## 2. `import-recordings` — `chart-emotion.recordings.v1`

```json
{
  "schema": "chart-emotion.recordings.v1",
  "registered_by": "reviewer-1",
  "recordings": [
    {
      "recording_id": "rec-demo-001", "title": "가상곡 가", "artist": "가상가수 A",
      "version_kind": "original", "version_label": null, "original_recording_id": null,
      "release_precision": "day", "release_date": "2015-03-14",
      "vocal_type": "lyrical", "data_mode": "synthetic",
      "source_id": null, "provider_track_ids": ["demo-001"], "notes": "가상"
    }
  ],
  "lyric_versions": [
    {"lyric_version_id": "lyr-demo-001-ko", "recording_id": "rec-demo-001", "status": "available",
     "language": "ko", "source_id": "synthetic-lyrics", "reference": "synthetic://lyrics/demo-001", "notes": null}
  ]
}
```

### 2.1 녹음 필드

| 필드 | 필수 | 규칙 |
|---|---|---|
| `recording_id` | 필수 | 식별자. 불변 |
| `title`, `artist` | 필수 | 표시용 문자열. 매칭 근거로 쓰지 않음 |
| `version_kind` | 필수 | `original` / `rerecording` / `cover` / `remix` / `live` / `translation` / `other` |
| `version_label` | 선택 | 자유 텍스트(예: "2025 재녹음") |
| `original_recording_id` | 선택 | 원곡 녹음 ID. 이미 등록되어 있거나 같은 파일 앞쪽에 있어야 함. `original` 에는 적지 않음. 자기 참조 불가. data_mode 일치 |
| `release_precision` | 필수 | `unknown` / `year` / `month` / `day` |
| `release_date` | 조건부 | `unknown` 이면 없어야 함. `year`=`YYYY`, `month`=`YYYY-MM`, `day`=`YYYY-MM-DD`. **ASCII 숫자만**(유니코드·전각 숫자, 공백, 부호 불가), 연도 `0001`~`9999`, 월 `01`~`12`, `day` 는 실재하는 날짜(윤년 2월 29일만 허용). 연·월만 알 때 나머지 요소를 지어내지 않고 형식만 검사함 |
| `vocal_type` | 필수 | `lyrical`(가창) / `instrumental`(연주곡·비가창) |
| `data_mode` | 필수 | `synthetic` / `real`. 스냅샷·가사 출처와 섞이지 않음 |
| `source_id` | 선택 | 메타데이터 출처. 적으면 `metadata` 또는 `chart` 종류, data_mode 일치, `permitted` + `import`·`store` 허용 필요 |
| `provider_track_ids` | 선택 | 참고용 문자열 배열(중복 불가). 병합 근거가 아님 |
| `notes` | 선택 | 메모 |

### 2.2 가사 버전 필드

| 필드 | 필수 | 규칙 |
|---|---|---|
| `lyric_version_id` | 필수 | 식별자. 불변 |
| `recording_id` | 필수 | 등록된(또는 같은 파일의) 녹음 |
| `status` | 필수 | `available` / `partial` / `translation_only` / `missing` / `not_applicable` |
| `language` | 조건부 | `available`·`partial`·`translation_only` 는 필수(`ko`, `en`, `zh-Hant` 형식). `not_applicable` 에는 적지 않음 |
| `source_id` | 조건부 | `available`·`partial`·`translation_only` 는 필수이며 `lyrics` 종류, data_mode 일치, `permitted` + `import`·`store` 허용 필요. `missing` 은 선택(적으면 존재·data_mode 만 확인, 허용 불필요) |
| `reference` | 조건부 | 보관·접근 참조(경로·카탈로그 ID). `missing`·`not_applicable` 에는 적지 않음 |
| `notes` | 선택 | 메모 |

가창/연주 정합성: `instrumental` 녹음의 가사 버전은 `not_applicable` 만, `lyrical` 녹음은 `not_applicable` 이외만 허용한다.
`missing`(미확보)은 `absent`(가사에 해당 내용 없음)와 다른 뜻이며 D06 라벨 집계에서 U 로 다룬다.

### 2.3 불변성과 재입력

- 같은 ID + 같은 내용(모든 필드 동일) → `unchanged`, 아무것도 바꾸지 않는다.
- 같은 ID + 다른 내용(메모만 달라도) → `recording_immutable` / `lyric_version_immutable`, 종료 코드 2. 수정본은 새 ID 로 등록한다.
  과거 매핑 revision 이 가리키는 ID 의 의미가 나중에 바뀌지 않게 하기 위한 규칙이다.
- 파일 전체가 트랜잭션 하나로 반영된다. 어느 항목이든 실패하면 녹음·가사 버전 모두 남지 않는다.

## 3. `import-mappings` — `chart-emotion.mappings.v1`

```json
{
  "schema": "chart-emotion.mappings.v1",
  "reviewer_id": "reviewer-1",
  "mappings": [
    {"snapshot_id": "demo-period-a-r1", "rank": 1, "state": "confirmed",
     "recording_id": "rec-demo-001", "lyric_version_id": "lyr-demo-001-ko",
     "reason": "provider_track_id 와 표기 확인", "base_revision": 0},
    {"snapshot_id": "demo-period-a-r1", "rank": 2, "state": "candidate",
     "recording_id": "rec-demo-002", "reason": "문자열 일치, 검토 전", "base_revision": 0},
    {"snapshot_id": "demo-period-a-r1", "rank": 5, "state": "unresolved",
     "reason": "provider_track_id 없음", "base_revision": 0}
  ]
}
```

| 필드 | 필수 | 규칙 |
|---|---|---|
| `reviewer_id` (파일) | 필수 | 기본 검토자. 항목별 `reviewer_id` 로 덮어쓸 수 있음 |
| `snapshot_id`, `rank` | 필수 | 존재하는 차트 항목. 한 파일에 같은 항목은 한 번만 |
| `state` | 필수 | `candidate` / `confirmed` / `unresolved` |
| `recording_id` | 조건부 | `candidate`·`confirmed` 필수, `unresolved` 금지. 등록된 녹음이며 스냅샷과 data_mode 일치 |
| `lyric_version_id` | 선택 | `recording_id` 가 있을 때만. 그 녹음의 가사 버전이어야 함(`lyric_recording_mismatch`) |
| `reason` | 필수 | 판정 이유 |
| `base_revision` | 필수 | 그 항목의 현재 최신 revision(없으면 0) |

### 3.1 상태 의미

| 상태 | 뜻 | D06 라벨 집계에서 |
|---|---|---|
| `unmapped` (행 없음) | 아직 판정 없음 | U |
| `candidate` | 후보. 문자열·ID 가 일치해도 확정이 아님 | U |
| `confirmed` | 사람이 확정한 녹음(·가사 버전) | 가사 버전 상태에 따라 P/A/U/N·A |
| `unresolved` | 판단 보류·철회. 이유를 `reason` 에 남김 | U |

같은 녹음이 여러 스냅샷의 항목에 `confirmed` 될 수 있다(두 기간 공통곡). 같은 제목의 원곡·재녹음·커버는 서로 다른 `recording_id` 로 각각 확정된다.

### 3.2 개정과 동시성

- 판정은 항상 새 revision 으로 추가된다(`revision` = 최신 + 1, `base_revision` = 제출 시점의 최신). 기존 행은 수정·삭제하지 않는다.
- `base_revision` 이 현재 최신과 다르면 `stale_base_revision`(종료 코드 2)이며 **배치 전체**를 반영하지 않는다. 오류에 현재 revision·상태·녹음 ID 가 실린다.
- 재입력(replay): 항목의 최신 판정과 내용(state·recording·lyric_version·reviewer·reason)이 완전히 같고 `base_revision` 이 최신 revision 이거나 최신 판정의 `base_revision` 이면 `unchanged`(no-op). 중간에 다른 revision 이 끼어 있으면 no-op 이 아니라 stale 오류다.
- 정정(다른 녹음으로), 철회(`unresolved`), 재확정(`confirmed`)은 모두 새 revision 이며 `show-mappings --history` 로 전체 이력을 본다.

## 4. `show-mappings` 와 `validate` 의 매칭 절

- `show-mappings [--snapshot ID] [--history]`: 항목별 현재 상태(`unmapped` 포함)·녹음 요약·가사 상태, 스냅샷별 진행 요약, 등록된 녹음 목록.
- `validate`(작업 폴더): `matching` 절은 **최신 revision 스냅샷**의 항목만 집계한다. `lyrics` 절은 `scope: workspace_catalogue` 로 표시된 **작업 폴더 전체** 가사 버전 카탈로그 합계이며 매핑되지 않은 등록도 포함한다(실험의 확보율이 아니다). 매핑·녹음·스냅샷 간 data_mode 불일치나 가사 버전-녹음 불일치가 저장소에 있으면 `error` 이슈로 표시한다.
- `validate --experiment`: `matching` 절은 **실험이 참조한 두 스냅샷**으로 범위를 한정하고 항목별 상태·녹음·가사 상태·가창 여부를 `entries_by_snapshot` 에 나열한다. `confirmed_recordings_in_all_snapshots` 는 두 기간에 모두 확정된 녹음이다. 미확정 항목이 있으면 `matching_incomplete` 경고를 내지만 검증 오류는 아니다.
- `validate --experiment` 의 `lyrics` 절은 `scope: selected_snapshots_latest_mappings` 이며 **항목 단위**로 센다. 포함 규칙: 항목의 최신 매핑이 `confirmed` 이고 가사 버전이 있으면 그 상태(`available`/`partial`/`translation_only`/`missing`/`not_applicable`), `confirmed` 이지만 가사 버전 미선택이면 `none`, `candidate`·`unresolved`·`unmapped` 는 `not_confirmed`. 같은 가사 버전이 두 기간에 쓰이면 항목마다 세되 `distinct_lyric_versions` 로 고유 수를 따로 준다. 어떤 항목에도 매핑되지 않은 카탈로그 등록은 이 절에 나타나지 않는다.

## 5. 기획 스케치와 다른 점

- 기획 표의 `recordings.lyric_version_id(선택)` 대신 가사 버전이 `recording_id` 를 가리키고, 매핑이 `recording_id` 와 `lyric_version_id` 를 함께 고정한다. 한 녹음에 여러 가사 버전(원어·번역·부분)이 있을 수 있고, 과거 run 이 어떤 가사 버전을 썼는지 매핑 revision 에서 바로 알 수 있게 하기 위해서다.
- `entry_mappings` 의 `status` 는 `candidate / confirmed / unresolved` 세 값으로 고정했고 `unmapped` 는 파생 상태다.
- 발매일은 `release_date` 문자열 + `release_precision` 으로 저장한다. DB CHECK 제약이 `unknown ⇔ NULL` 을 강제한다.
- 매핑 ID 는 사용자가 정하지 않고 `<snapshot_id>.rank<rank>.r<revision>` 으로 생성한다.
- 후보 자동 제안·문자열 유사도 계산은 구현하지 않았다. 기획의 "문자열 유사도만으로 자동 확정하지 않음"을 지키는 가장 단순한 방식으로, 후보도 사람이 적는다.
