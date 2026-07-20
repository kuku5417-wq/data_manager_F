# Data Manager 데이터 구조 정리

> 스킬 작성용 기초 자료. 코드 기준: app.py / esg_converter.py / tbm_converter.py / out_converter.py / path_config.py

## 1. 저장 경로 (path_config.py)

| 환경 | 판단 기준 | 기준 경로 |
|---|---|---|
| 사내망 (NAS) | `F:\code\secret\.env`의 `NAS_PATH` 경로 접근 가능 | `{NAS_PATH}/` (NAS_PATH = `...\06_Commissioning_TEAM`까지 포함) |
| 사외망 (로컬) | NAS 접근 불가 | `code/data/` |

> 사내망 base(`NAS_PATH`)는 사외망 `F:\code\data`에 1:1 대응한다. 두 환경 모두 base 바로 아래에 `parquet/`, `upload/{esg,ptw,out}/`, `sql/`이 **동일한 이름**으로 위치한다.

하위 디렉토리 (NAS/로컬 동일 구조):
- `parquet/` — 최종 parquet 저장 (`get_parquet_dir()`)
- `upload/esg/` — ESG 엑셀(6개 시트) 업로드 폴더 (`get_upload_dir("esg")`)
- `upload/ptw/` — TBM `ptwlist_YYMMDD.xlsx` 업로드 폴더 (`get_upload_dir("ptw")`)
- `upload/out/` — `outside_*.xlsx` 업로드 폴더 (`get_upload_dir("out")`)
- `upload/<section>/_backup/` — 저장 시 원본 백업, `YYYYMMDD_원본명.xlsx` (`get_backup_dir(section)`)
- `sql/` — SQL 미접속 시 대체 parquet (`get_sql_dir()`)
- `parquet/last_updated.txt` — ESG 저장 후 갱신되는 센티널 (캐시 무효화 트리거)

앱 기동 시 `ensure_upload_dirs()`가 섹션 폴더를 자동 생성하며, 각 탭의 폴더 스캔 입력창은 해당 섹션 업로드 폴더가 기본값으로 채워진다.

## 2. ESG (엑셀 1파일 → parquet 6개)

**입력**: 6개 시트 포함 xlsx 1개. 시트명은 후보 목록으로 탐색 (정확 일치 → 키워드 포함 → 인덱스 폴백).

| key | 시트명 후보 | 변환 함수 |
|---|---|---|
| trial_schedule | 0.시운전일정, 시운전일정 | convert_trial_schedule |
| fuel_usage | 1.사용량, 사용량 | convert_fuel_usage |
| fuel_price | 2.단가, 2.유류단가, 단가, 유류단가 | convert_fuel_price |
| lng_usage | 3.LNG, LNG | convert_lng_usage |
| fuel_plan | 4.연간계획, 연간계획, 시운전유류, 유류계획 | convert_fuel_plan (선택적 시트) |
| pjtmethod | 5.공법, 공법, List, list | convert_pjtmethod |

**공통 처리**: 컬럼명 정규화 — 줄바꿈 제거, `(` 이후 절삭, `/`·공백 제거(`_`), 중복 시 `_2` 부여. `호선` → `PJT` 통일.

**병합 규칙 (`_pjt_merge`)**: PJT 단위 교체. 업로드에 있는 PJT 행은 기존에서 삭제 후 신규로 대체, 없는 PJT는 유지.

### 스키마

**trial_schedule.parquet**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| PJT | str | 호선번호 (예: SN2601) |
| 일정구분 | str | LC~GT+1 / AC / 통합시운전 / ST,DP / 인도준비 |
| 출항, 복귀 | datetime | AM/PM·오전/오후·Excel serial 모두 파싱 |
| 대기 | str | |

**fuel_usage.parquet**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| PJT | str | |
| 일정구분 | str | |
| 공급처 | str | |
| 수급사용 | str | "사용"/"수급" |
| Start, Finish | datetime | |
| HFO, LS_HFO, LS_MGO, RMA10, LDO, 메탄올, LNG, LPG | float | round(3) |

**fuel_price.parquet**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| PJT | str | |
| 단가구분 | str | |
| HFO, LS_HFO, LS_MGO, RMA10, LDO, 메탄올 | float | round(3) |

**lng_usage.parquet**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| PJT | str | LNG선만 |
| 구분, 작업구분, 용도 | str | 작업구분: LNG선적/BOG 소모/LNG하역 |
| 비용, 양, 단가 | numeric | |
| Loading, Unloading | datetime | |

**fuel_plan.parquet** — 컬럼 순서 고정
| 컬럼 | 타입 | 비고 |
|---|---|---|
| PJT | str | 원본 "호선" → PJT 리네임 |
| 인도년 | int | 2000 미만 행 제외 |
| 인도월 | int | "월" 접미사 제거 |
| LNG, HFO, LS_HFO, RMA10, LS_MGO, LDO, 메탄올, LPG | float | MGO → LS_MGO 리네임, NaN → 0 |

특수 처리: 헤더행 자동 탐색(상위 10행에서 PJT/호선 위치), 합계/소계/total/sum 행 제외, NFC 유니코드 정규화. 인도지연 시 동일 PJT가 (계획년월, 실제년월) 2행 존재 가능.

**pjtmethod.parquet**
| 컬럼 | 타입 | 비고 |
|---|---|---|
| PJT | str | |
| 공법 | str | 통합+역+선적+하역+Speed 조합 문자열 |
| 통합 | str | "통합"/"분리" |
| 역 | str | "역"/"순" |
| 선적 | str | M1~M3(통합) / P1~P3(분리) |
| 하역 | str | M/P |
| Speed | str | |
| SG | str/NA | |

제외 컬럼: No, 시리즈, OWNER, TYPE, 선종. 구버전 보정: `부`→`역`, `부두1`→`선적`.

## 3. TBM (ptwlist_YYMMDD.xlsx → ptwlist.parquet)

**입력**: `ptwlist_*.xlsx` 1개. 컬럼은 후보 키워드로 매핑(대소문자 무시), 미매칭은 None 채움.

> **개인정보 최소수집(개인정보보호법 제16조)**: 담당자/작업자 실명(`HNAME`)·HSE담당자(`HSE_MANAGE`)는 소비앱(tbm)·LLM 어디에서도 사용하지 않아 **수집·저장하지 않는다**(2026-07 제거). TBM/RA는 부서(`DEPTNM`/`TEAMNM`) 단위로만 다룬다.

**ptwlist.parquet** (13컬럼. `ptwlist_archive.parquet`도 동일 스키마지만 아래 "아카이브" 참조 — 현재 생산자 없음)
| 표준 컬럼 | Excel 후보 |
|---|---|
| KYULGBN | KYULGBN, 결재구분, 결재상태, 상태 |
| PTW_AGBN | PTW_AGBN, 등급, AGBN |
| TEAMNM | TEAMNM, 팀명, 팀 |
| DEPTNM | DEPTNM, 부서명, 부서 |
| IOWKGBNNM | IOWKGBNNM, 밀폐구분, 밀폐여부 |
| WKGBNNM | WKGBNNM, 작업구분명, 작업구분 |
| STDATE | STDATE, 시작일시, 시작일, 작업시작, START |
| EDDATE | EDDATE, 종료일시, 종료일, 작업종료, END, FINISH |
| PJT | HULLNO, HULL_NO, 호선번호, 호선, PJT, PROJECT |
| AREA_DETAIL | AREA_DETAIL, 작업위치, 위치, 장소, AREA |
| WORK_NM | WORK_NM, 작업명, 작업내용, 작업상세 |
| ACODENM | ACODENM, 작업허가대상, 작업코드명, 허가대상 |
| DATE | (파생) "YY-MM-DD" 문자열 |

**변환 규칙**:
- 날짜 파싱 우선순위: `%y/%m/%d %H:%M` → 기타 포맷 → pandas 자동
- **일별 확장**: STDATE~EDDATE 범위를 하루 1행으로 확장 (DATE 생성). STDATE NaT 행 제거, EDDATE NaT/역전 시 STDATE 하루만
- **병합**: 기존 parquet과 concat 후 `[DEPTNM, PJT, AREA_DETAIL, ACODENM, DATE]` 기준 dedup (keep=last)
- **아카이브: 현재 미구현.** `tbm_converter.ARCHIVE_DAYS = 14` 상수만 있고 어디서도 쓰이지 않는다.
  `_process_raw`는 단일 `ptwlist.parquet`에 전부 누적하며 분리하지 않는다(해당 docstring 참조).
  따라서 `ptwlist_archive.parquet`은 **생산자가 없다** — 카탈로그에 항목은 있으나 갱신되지 않는다.
  (원래 의도는 "DATE가 오늘−14일 미만이면 분리". 구현하려면 `_process_raw` 저장 직전에 추가해야 한다.)

## 4. Out (outside_*.xlsx 복수 → out.parquet + ra.parquet)

> **[개인정보]** out/ra는 사외작업자 위험성평가 목적의 개인정보(`name` 성명, `phone` 연락처, `greeter`/`greeter_actual` 사내 접견자명, `dept`/`company` 소속)를 포함한다.
> - **표시 마스킹 필수**: 화면 출력은 `pii.mask_df_for_display`(→`mask_name`/`mask_phone`) 경유. parquet 원본은 위험성평가 실무상 원문 보관.
> - **최소수집**: `phone`은 비상연락 목적으로만 보관(표시 마스킹). 대시보드·통계 목적엔 불필요하므로 확대 수집 금지.
> - **주의**: `ra_file` 경로 문자열에 개인 실명이 포함될 수 있음(예 `RA_홍길동.pdf`) — 첨부파일명 규칙에서 실명 배제 권장.
> - **전송/백업**: `jsh_out`/`jsh_ra`(MSSQL)는 원문 보유 → 하류 접근통제 대상. `upload/out/_backup/` 원본 엑셀도 원문 → 보존개수 최소·접근통제.

**out.parquet** — 한국어 → 영문 컬럼 매핑
| Excel | parquet | 타입 |
|---|---|---|
| 이름 | name | str **[개인정보]** |
| 연락처 | phone | str **[개인정보]** |
| 회사명 | company | str |
| 방문시작 | visit_start | date (YYYYMMDD 8자리·일반 형식 파싱) |
| 방문종료 | visit_end | date |
| 호선 | project | str |
| 업무내용 | work_content | str |
| 접견자 | greeter | str **[개인정보]** |
| 방문부서 | dept | str |

처리: 방문부서가 OUT_DEPT_FILTER(시운전팀, 시운전, 기장운전, 전장운전, 선장운전, CSU, 친환경실증, 해운, 해운1과, 해운2과, 시운전기술, LNG설비운영) 포함 행만 유지. 복수 파일 concat 후 `[name, company, visit_start, visit_end, work_content]` dedup (keep=last).

**ra.parquet** (out에서 파생, 14컬럼)
| 컬럼 | 비고 |
|---|---|
| name, phone | 그룹 내 중복 제거 후 ", " join |
| company, visit_start, visit_end, project, work_content, greeter, dept | |
| period_start, period_end | 방문기간을 최대 7일 단위 분할 (2일 미만 chunk 제외) |
| is_commissioning | dept가 COMMISSIONING_DEPTS 포함 여부 (bool) |
| ra_done | "N" 기본. 기존 ra.parquet에서 (name, company, period_start) 키로 상태 복원 |
| ra_file | "" 기본. 위와 동일하게 복원 |

파생 규칙: 3년(RA_KEEP_YEARS) 초과 데이터 제외. 그룹 키 = `[company, period_start, period_end, work_content, dept]`.

## 5. SQL 대체 parquet (MySQL 미접속 시)

| 파일 | 원본 | 컬럼 (create_dummy.py 기준) |
|---|---|---|
| pjtlist.parquet | MySQL shipinfo | SHIPNUM, TITLE, TYPEMODEL, SHIPCLASS, DOCK, WORKFINISH(ts), ISTRIAL, ISOUTSIDE, REGOWNER, PROJSEQ, SHIPTYPE |
| milestone.parquet | MySQL pjtevnt | project, PLANWF, PROSWF, PERFWF, PLANLC, PERFLC, PLANDL, PERFDL (모두 timestamp) |
| shipbbs.parquet | MySQL shipbbs | (스키마 미정의 — 코드에 없음) |

## 6. 공통 키·규칙 요약

- **마스터 키**: `PJT` (호선번호, 예: SN2601). SQL 쪽은 `SHIPNUM`/`project`로 표기 상이
- **하위호환**: `호선` 컬럼 발견 시 `PJT`로 리네임
- **병합 전략**: ESG=PJT 단위 교체 / TBM=DATE+위치 dedup / Out=전체 재생성(+ra_done 복원)
- **신호등 diff** (`analyze_diff`): 🟢 added(신규 PJT) / 🟡 changed(교체 PJT) / ⚪ kept(유지 PJT)
- **백업**: 저장 시 원본 Excel을 `upload/<section>/_backup/`에 `YYYYMMDD_파일명`으로 보관 (esg/ptw/out 모두 적용)
