# 가상 자료 예제 (synthetic)

**이 폴더의 모든 곡명·가수명·순위는 직접 작성한 합성 데이터입니다.** 실제 차트, 실제 곡, 실제 가사와 무관하며 연구 결과로 제시하지 않습니다.

## 기본 시연 자료 — 가상곡 8개, 기간 2개 × Top 5

| 파일 | 내용 |
|---|---|
| `charts/period_a.csv` + `batches/period_a.json` | 표시 연도 2015, 집계 기간 2015-01-01~2015-12-31, 순위 1~5. `demo-001`~`demo-004` 와 provider_track_id 가 없는 `가상곡 마`. 제목에 따옴표로 감싼 쉼표 포함. BOM 없음 |
| `charts/period_b.csv` + `batches/period_b.json` | 표시 연도 2025, 집계 기간 2025-01-01~2025-12-31, 순위 1~5. `demo-006`, `demo-007`, `demo-008` 과 두 기간 공통곡 `demo-001`, `demo-002`. 두 줄 제목과 `""` 이스케이프 포함. **UTF-8 BOM 있음** |
| `experiments/demo_comparison.json` | 위 두 스냅샷(`demo-period-a-r1`, `demo-period-b-r1`)을 참조하는 실험 설정 |

계수: 기간 A 5곡 + 기간 B 5곡 = 항목 10개, 공통곡 2개, 고유 가상곡 8개.

## 누락·정정 시연 — 세 번째 기간

| 파일 | 내용 |
|---|---|
| `charts/period_c_incomplete.csv` + `batches/period_c.json` | 표시 연도 2020, 순위 3 누락 → `incomplete` 스냅샷 `demo-period-c-r1` |
| `charts/period_c_corrected.csv` | 같은 기간의 정정본. `--revision 2` 를 붙여야 `demo-period-c-r2` 로 추가됨 |

이 기간의 곡은 기본 시연의 8곡을 재사용하므로 고유곡 수는 늘지 않습니다.

2020년은 윤년(366일)이므로 `demo-period-c-r2` 를 2015년·2025년(365일) 스냅샷과 비교하는 실험은 `comparability_note` 없이는 `period_length_mismatch` 로 차단됩니다. 이는 의도된 동작이며 기간 길이 규칙의 예시로 남겨 둡니다.

## 잘못된 입력

| 파일 | 기대 결과 |
|---|---|
| `invalid/duplicate_rank.csv` | rank 2 중복 → `csv_invalid` / `csv_duplicate_rank`, 종료 코드 2 |
| `invalid/missing_column.csv` | `artist` 열 없음 → `csv_header_invalid` / `csv_missing_column` |
| `invalid/invalid_dates.json` | `period_end` 가 `period_start` 보다 앞섬 → `batch_config_invalid` / `date_order`. 새 chart_id 를 담고 있지만 차트 정의가 생성되지 않아야 함 |
| `invalid/wrong_types.json` | `display_year` 문자열, `expected_n` bool, `data_mode` 미허용 값, 존재하지 않는 날짜 2018-02-30 |
| `experiments/invalid_unknown_label.json` | `revision` bool, 같은 스냅샷 2개, 미정의 라벨 `emotion.despair`, `coverage_threshold` 1.5 |

## 같은 제목·다른 버전

| 파일 | 내용 |
|---|---|
| `versions/same_title_versions.csv` + `versions/batch_versions.json` | 별도 차트 `synthetic-versions-top3` (주간, Top 3). 제목 `가상곡 가` 3행이 각각 원곡·재녹음·다른 가수 커버로 provider_track_id 만 다름. 세 행이 개별 항목으로 보관되고 문자열만으로 병합되지 않아야 함 |
| `mappings/versions_chart.json` | 위 3행을 `rec-demo-001`(원곡), `rec-demo-001-rerecord-2025`(재녹음), `rec-demo-009-cover`(커버)로 각각 확정 |

이 차트는 기본 시연의 8곡 계수와 별개입니다.

## D05 — 녹음·가사 버전·매핑 (모두 가상)

먼저 `register-source --source-id synthetic-lyrics --kind lyrics --data-mode synthetic` 로 가사 메타데이터 출처를 등록해야 `recordings.json` 이 들어갑니다(가사 본문은 어디에도 없습니다).

| 파일 | 내용 |
|---|---|
| `recordings/recordings.json` | 녹음 10건: 기본 8곡 `rec-demo-001`~`008` + 재녹음 `rec-demo-001-rerecord-2025` + 커버 `rec-demo-009-cover`. 발매일 정밀도 day/month/year/unknown 을 모두 포함하고 `rec-demo-007` 은 연주곡(instrumental). 가사 버전 10건: available 4, partial 1, translation_only 1, missing 3, not_applicable 1 |
| `mappings/period_a_initial.json` | 기간 A: confirmed 3, candidate 1(rank 2), unresolved 1(rank 5) |
| `mappings/period_b_initial.json` | 기간 B: confirmed 4(rank 2 는 A 와 같은 `rec-demo-001`), candidate 1(rank 4) |
| `mappings/corrections.json` | A rank 2 후보→확정, A rank 5 보류→확정, B rank 4 후보→철회(unresolved). 모두 `base_revision: 1` → revision 2 |
| `mappings/stale_conflict.json` | `base_revision: 1` 기준의 오래된 검토표. corrections 이후 실행하면 `stale_base_revision` 으로 배치 전체 거부(첫 항목도 미반영) |
| `mappings/reconfirm.json` | B rank 4 를 reviewer-2 가 `base_revision: 2` 로 재확정 → revision 3. 이전 revision 은 그대로 남음 |
| `invalid/recordings_invalid.json` | 정밀도-날짜 불일치, unknown 인데 날짜 있음, bool 타입, 존재하지 않는 날짜, 가사 본문 필드 → `recordings_file_invalid` |
| `invalid/mapping_lyric_mismatch.json` | `rec-demo-001` 에 `rec-demo-002` 의 가사 버전을 붙임 → `lyric_recording_mismatch` |

순서대로 실행한 뒤 `validate --experiment experiments/demo_comparison.json` 을 보면 매칭 `complete`, 두 기간 공통 확정 녹음 `rec-demo-001`, `rec-demo-002`, 고유 확정 녹음 8 이 나옵니다.
