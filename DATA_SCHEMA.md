# data_manager (사내망 전용 · data_manager_F) 데이터 스키마 정의서 (2026-07-29)

> **목적** — `data_manager_F` 가 생산하는 중앙 parquet 의 **정본 스키마 정의서**.
> tbm / tbm_mssql / esg / esg_mssql / costplan / log(SSIMS) / OCR_N 등 **소비앱을 새로 만들거나 수정할 때 이 문서를 참조**한다.
> **기준일: 2026-07-29** — 이 날짜의 생산자 소스코드와 실파일을 대조해 작성.
>
> **사외망본 `data_manager` 와 스키마는 완전히 동일하다.** 생산자 모듈(`esg_converter`/`tbm_converter`/`out_converter`/`ptw_enrich`/`message_store`/`date_manager`/`doc_parser`/`parquet_io`/`sn_util`/`db/`)이 두 repo 에서 바이트 단위로 같고, 차이는 **경로(§1)와 DB 폴백(§4.4)** 뿐이다.
>
> **갱신 규칙** — 테이블이 추가·변경되면 이 문서를 함께 갱신하고 제목의 날짜를 바꾼다. **`data_manager` / `data_manager_F` 양쪽 `DATA_SCHEMA.md` 를 동일하게 갱신**한다(경로·폴백 절 제외). 문서와 코드가 어긋나면 **코드(생산자 모듈)가 정본**이다.

---

## 0. 사용 규칙 (소비앱 개발자용)

1. **parquet 은 data_manager 만 생산한다.** 소비앱은 **읽기 전용**. 어떤 앱도 중앙 `parquet/` 에 쓰지 않는다.
   - 예외: `ra.parquet` 의 상태 컬럼(`ra_done`/`ra_file`/`excluded`/`exclude_reason`/`greeter_actual`/`manual`)은 소비앱(tbm)이 기록하는 값이며, data_manager 가 재생성 시 **복원·보존**한다(§4.2).
2. **경로 하드코딩 금지.** `path_config.get_parquet_dir()` 또는 `app_config.PARQUET_PATH` 경유(§1).
3. **호선번호는 `SN2601` 형식이 정본.** `sn_util.ensure_sn` / `normalize_sn` 경유하고, 표시단에서 `f"SN{...}"` 재부착 금지.
4. **컬럼 유무를 가정하지 말 것.** 실파일에는 구버전 잔여 컬럼이 남아 있을 수 있다(§7). 항상 `if col in df.columns` 방어.
5. **dtype 을 가정하지 말 것.** 문자열은 pandas/pyarrow 버전에 따라 `string`/`large_string` 이 섞이고, 숫자는 값에 따라 `int64`/`double` 이 바뀐다. 소비 시 `astype(str)` / `pd.to_numeric` 으로 명시 캐스팅한다.
6. **캐시 무효화는 센티널로.** `parquet/last_updated.txt` 의 mtime/내용 변화를 캐시 키로 쓴다(§1.2).
7. **개인정보 표시는 반드시 마스킹.** `[PII]` 표기 컬럼은 화면 출력 전 `pii.mask_name` / `pii.mask_phone` / `pii.mask_df_for_display` 경유(§6).

---

## 1. 저장 위치

### 1.1 경로 규약 — **사내망 NAS 전용**

| 환경 | base 경로 |
|---|---|
| 사내망 (유일) | `{NAS_BASE_PATH}` — `.env` 에서 읽음 (`...\06_Commissioning_TEAM` 까지 포함한 전체 경로) |

- **단일 소스 = `app_config.BASE_PATH` (= `NAS_BASE_PATH`).** `path_config` 는 `app_config` 를 필수 임포트한다(폴백 없음).
- `data_manager_F` 는 **사외망 로컬 경로 폴백이 없다.** `NAS_BASE_PATH` 미설정·접근 불가면 **기동을 거부**한다.
- 사외망본 `data_manager` 는 같은 자리에서 `.env` 의 `DATA_PATH`(로컬 폴더)로 폴백한다 — **하위 폴더 이름과 스키마는 동일**하므로 이 문서의 나머지 내용은 두 환경에서 그대로 유효하다.

| 하위 폴더 | 접근 함수 | 용도 |
|---|---|---|
| `parquet/` | `pc.get_parquet_dir()` / `app_config.PARQUET_PATH` | **중앙 parquet (본 문서의 대상)** |
| `upload/{esg,ptw,out,accident,guide,message}/` | `pc.get_upload_dir(section)` | 섹션별 원본 업로드 |
| `upload/<section>/_backup/` | `pc.get_backup_dir(section)` | 원본 백업 (`YYYYMMDD_원본명`) |
| `upload/<section>/_processed/` | `pc.get_processed_dir(section)` | 무인 잡 처리완료 원본 이동 (재처리 방지) |
| `sql/` | `pc.get_sql_dir()` | DB 미접속 시 대체 parquet |

### 1.2 parquet/ 내 비(非)테이블 산출물

| 경로 | 형식 | 내용 |
|---|---|---|
| `parquet/last_updated.txt` | 텍스트 | 저장 시각 ISO 문자열. `pc.touch_sentinel()` 이 갱신. **소비앱 캐시 무효화 트리거** |
| `parquet/_jobstatus/<job>.json` | JSON | 무인 잡 실행 상태 `{job, ok, ts, summary, error}` (`job_status.py`). job = `import`/`doc`/`weather`/`db`/`date` |

### 1.3 저장 방식 (전 테이블 공통)

- `parquet_io.save_parquet_atomic(df, path)` — 임시파일 기록 → `os.replace` 원자적 교체. **소비앱이 반쯤 쓰인 파일을 읽는 일이 없다.**
- 저장 성공 시 `db/auto_sync.py` 가 **저장된 parquet 을 다시 읽어** 운영 MSSQL `jsh_*` 테이블로 전체 교체 push(§5). `.env DB_*` 미설정이면 휴면, 실패해도 parquet 저장에는 영향 없음(best-effort).

---

## 2. 산출물 일람 (20개 parquet)

| # | 파일 | 그룹 | 생산자 모듈 | 원본 | 갱신 트리거 | MSSQL |
|---|---|---|---|---|---|---|
| 1 | `trial_schedule.parquet` | ESG | `esg_converter` | ESG xlsx `0.시운전일정` | 수동 업로드 | `jsh_trial_schedule` |
| 2 | `fuel_usage.parquet` | ESG | `esg_converter` | ESG xlsx `1.사용량` | 수동 업로드 | `jsh_fuel_usage` |
| 3 | `fuel_price.parquet` | ESG | `esg_converter` | ESG xlsx `2.단가` | 수동 업로드 | `jsh_fuel_price` |
| 4 | `lng_usage.parquet` | ESG | `esg_converter` | ESG xlsx `3.LNG` | 수동 업로드 | `jsh_lng_usage` |
| 5 | `fuel_plan.parquet` | ESG | `esg_converter` | ESG xlsx `4.연간계획` | 수동 업로드 | `jsh_fuel_plan` |
| 6 | `pjtmethod.parquet` | ESG | `esg_converter` | ESG xlsx `5.공법` | 수동 업로드 | `jsh_pjtmethod` |
| 7 | `ptwlist.parquet` | TBM | `tbm_converter` + `ptw_enrich` | `upload/ptw/ptwlist_*.xlsx` | 업로드 / 워처(`ptw_watch_job`) | `jsh_ptwlist` |
| 8 | `mapping.parquet` | TBM | `ptw_enrich` | LLM 생성 캐시 | ptwlist 변환 시 자동 누적 | `jsh_mapping` |
| 9 | `out.parquet` | 사외작업자 | `out_converter` | `upload/out/outside_*.xlsx` | 수동 업로드 | `jsh_out` |
| 10 | `ra.parquet` | 사외작업자 | `out_converter` | `out.parquet` 파생 | out 변환 / `regenerate_ra` | `jsh_ra` |
| 11 | `pjtlist.parquet` | 운영DB 미러 | `db_connector.gen_pjtlist` | MySQL `shipinfo` | DB 잡(`db_job`) | `jsh_pjtlist` |
| 12 | `milestone.parquet` | 운영DB 미러 | `db_connector.gen_milestone` | MySQL `pjtevnt` | DB 잡 | `jsh_milestone` |
| 13 | `shipbbs.parquet` | 운영DB 미러 | `db_connector.gen_shipbbs` | MySQL `shipbbs` | DB 잡 | `jsh_shipbbs` |
| 14 | `date.parquet` | 참조 | `date_manager` | `holidays` 라이브러리 | 날짜 잡(`date_job`) | `jsh_date` |
| 15 | `weather.parquet` | 참조 | `api_weather` | 기상청 단기예보 API | 날씨 잡(`weather_job`, 일 2회) | `jsh_weather` |
| 16 | `message.parquet` | 참조 | `message_store` | 앱 내 직접 입력 | UI 저장 | `jsh_message` |
| 17 | `accident.parquet` | 문서 | `doc_parser` | `upload/accident/*.pdf` + LLM | 문서 잡(`doc_job`) | — |
| 18 | `guide.parquet` | 문서 | `doc_parser` | `upload/guide/*.pdf` + LLM | 문서 잡 | — |
| 19 | `ptwlist_archive.parquet` | ⚠️ 미생산 | **없음** | — | — | — |

> **19번 `ptwlist_archive.parquet`** — 파일은 존재하지만 **현재 생산자가 없다.** `tbm_converter.ARCHIVE_DAYS = 14` 상수만 남아 있고 어디서도 사용되지 않으며, `_process_raw` 는 전량을 `ptwlist.parquet` 에 누적한다. **소비앱은 이 파일을 참조하지 말 것**(갱신되지 않는 과거 스냅샷).
>
> `accident` / `guide` 는 `db/tables.py TABLES` 화이트리스트에 없어 MSSQL 로 push 되지 않는다.

---

## 3. 공통 규약

| 항목 | 규칙 |
|---|---|
| 마스터 키 | **`PJT`** (호선번호, `SN2601` 형식). 운영DB 미러 계열은 `SHIPNUM`(pjtlist) / `project`(milestone, shipbbs) 로 표기가 다름. `out`/`ra` 는 `project` |
| 하위호환 | 원본에 `호선` 컬럼이 있으면 `PJT` 로 리네임 |
| 날짜 파싱 | Excel serial · `AM/PM` · `오전/오후` · `%y/%m/%d %H:%M` 등 다형식 지원 (`esg_converter._parse_datetime_col`, `tbm_converter._parse_dt_col`) |
| 문자열 dtype | `string` / `large_string` 혼재 — 의미 차이 없음. **소비 시 구분하지 말 것** |
| 숫자 dtype | 값이 모두 정수면 `int64`, 아니면 `double` 로 저장됨. **소비 시 `pd.to_numeric(...).astype(float)` 권장** |
| 전량 결측 컬럼 | 값이 하나도 없으면 arrow `null` 타입으로 저장된다(예: 현 `trial_schedule.대기`). `astype` 전에 `notna()` 확인 |
| 컬럼 순서 | **보장되지 않음**(`fuel_plan` 만 코드에서 고정). 위치 인덱싱 금지, 이름으로 접근 |
| 병합 전략 | ESG=PJT 단위 교체 / TBM=키 dedup 누적 / out=키 dedup 누적 / ra=재생성+상태복원 / DB미러·date=전체 교체 / weather=예보일 기준 upsert |

---

## 4. 테이블별 스키마

### 4.1 ESG 계열 — `esg_converter.py`

**입력**: 6개 시트를 포함한 xlsx 1개. 시트명은 후보 목록으로 탐색(정확 일치 → 키워드 포함 → 인덱스 폴백).

| key | 시트명 후보 | 변환 함수 |
|---|---|---|
| trial_schedule | 0.시운전일정, 시운전일정 | `convert_trial_schedule` |
| fuel_usage | 1.사용량, 사용량 | `convert_fuel_usage` |
| fuel_price | 2.단가, 2.유류단가, 단가, 유류단가 | `convert_fuel_price` |
| lng_usage | 3.LNG, LNG | `convert_lng_usage` |
| fuel_plan | 4.연간계획, 연간계획, 시운전유류, 유류계획 | `convert_fuel_plan` (선택적 시트) |
| pjtmethod | 5.공법, 공법, List, list | `convert_pjtmethod` |

**공통 컬럼명 정규화**: 줄바꿈 제거 → `(` 이후 절삭 → `/`·공백을 `_` 로 → 중복 시 `_2` 접미. `호선` → `PJT`.

**병합 규칙 (`_pjt_merge`)**: **PJT 단위 교체.** 업로드에 포함된 PJT 의 기존 행을 삭제하고 신규로 대체, 업로드에 없는 PJT 는 유지.

#### `trial_schedule.parquet` — 시운전 항차 일정

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `PJT` | str | 호선번호 (`SN2601`) |
| `일정구분` | str | `LC~GT+1` / `AC` / `통합시운전` / `ST,DP` / `인도준비` 등 |
| `출항` | datetime | 항차 출항 일시 |
| `복귀` | datetime | 항차 복귀 일시 |
| `대기` | str | 대기 구분. **현 실파일은 전량 결측(`null` 타입)** |

#### `fuel_usage.parquet` — 항차별 유종 사용/수급량

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `PJT` | str | 호선번호 |
| `일정구분` | str | `trial_schedule.일정구분` 과 동일 체계 |
| `공급처` | str | 공급 업체 |
| `수급사용` | str | `"사용"` / `"수급"` |
| `Start` | datetime | 구간 시작 |
| `Finish` | datetime | 구간 종료 |
| `HFO` `LS_HFO` `LS_MGO` `RMA10` `LDO` `메탄올` `LNG` `LPG` | numeric | 유종별 수량. `round(3)` |

#### `fuel_price.parquet` — 유종 단가

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `PJT` | str | 호선번호 |
| `단가구분` | str | 단가 구분 |
| `HFO` `LS_HFO` `LS_MGO` `RMA10` `LDO` `메탄올` | numeric | 유종별 단가. `round(3)` — 정수만 있으면 `int64` 로 저장됨 |

#### `lng_usage.parquet` — LNG 선적/하역/BOG (LNG선만)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `PJT` | str | 호선번호 |
| `구분` | str | |
| `작업구분` | str | `LNG선적` / `BOG 소모` / `LNG하역` |
| `용도` | str | |
| `양` | numeric | 수량 |
| `단가` | numeric | 단가 |
| `비용` | numeric | 금액 |
| `Loading` | datetime | 선적 일시 |
| `Unloading` | datetime | 하역 일시 |

#### `fuel_plan.parquet` — 연간 유류 계획 (**컬럼 순서 고정**)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `PJT` | str | 원본 `호선` → 리네임 |
| `인도년` | int | 2000 미만 행은 제외 |
| `인도월` | int | `"월"` 접미 제거 |
| `LNG` `HFO` `LS_HFO` `RMA10` `LS_MGO` `LDO` `메탄올` `LPG` | float | 원본 `MGO` → `LS_MGO` 리네임. 결측 → `0` |

특수 처리: 헤더행 자동 탐색(상위 10행에서 `PJT`/`호선` 위치), 합계/소계/total/sum 행 제외, NFC 유니코드 정규화.
**주의**: 인도 지연 시 동일 PJT 가 (계획 년월, 실제 년월) **2행** 존재할 수 있다.

#### `pjtmethod.parquet` — 호선별 시운전 공법

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `PJT` | str | 호선번호 |
| `공법` | str | 통합+역+선적+하역+Speed 조합 문자열 |
| `통합` | str | `"통합"` / `"분리"` |
| `역` | str | `"역"` / `"순"` |
| `선적` | str | 통합 `M1~M3` / 분리 `P1~P3` |
| `하역` | str | `M` / `P` |
| `Speed` | str | |
| `SG` | str | 결측 가능 |

제외 컬럼: `No`, `시리즈`, `OWNER`, `TYPE`, `선종`. 구버전 보정: `부`→`역`, `부두1`→`선적`.

---

### 4.2 TBM / 작업허가(PTW) 계열

#### `ptwlist.parquet` — 작업허가서 일별 전개 목록

**생산자**: `tbm_converter._process_raw` → `ptw_enrich.enrich_ptwlist`
**입력**: `upload/ptw/ptwlist_YYMMDD.xlsx` (DRM 걸린 파일은 Excel COM 으로 읽고 실패 시 `read_excel` 폴백)

**변환 파이프라인**: 컬럼 매핑 → **팀 필터(`TEAMNM == "시운전팀"` 만 유지)** → `PJT` `ensure_sn` → 날짜 파싱 → **일별 확장** → dedup 누적 → 위험요소 보강

- **일별 확장**: `STDATE`~`EDDATE` 범위를 하루 1행으로 전개해 `DATE` 생성. `STDATE` 결측 행은 제거, `EDDATE` 결측/역전 시 `STDATE` 하루만.
- **병합**: 기존 parquet 과 concat 후 `[DEPTNM, PJT, AREA_DETAIL, ACODENM, DATE]` 기준 dedup (`keep=last`) — **누적**되며 과거 데이터는 지워지지 않는다.
- **위험요소 보강**: `ACODENM` 을 키로 `mapping.parquet` 조회 → 미매핑은 LLM 생성. LLM 실패해도 저장은 진행(해당 행 빈 값), 빈 결과는 캐시하지 않아 다음 회차에 재시도.

| 컬럼 | 타입 | Excel 후보 컬럼 | 설명 |
|---|---|---|---|
| `KYULGBN` | str | KYULGBN, 결재구분, 결재상태, 상태 | 결재 상태 |
| `PTW_AGBN` | str | PTW_AGBN, 등급, AGBN | 허가 등급 |
| `TEAMNM` | str | TEAMNM, 팀명, 팀 | 팀명 (저장 시점에 `시운전팀` 만 남음) |
| `DEPTNM` | str | DEPTNM, 부서명, 부서 | 부서명 — TBM/RA 의 조직 단위 |
| `IOWKGBNNM` | str | IOWKGBNNM, 밀폐구분, 밀폐여부 | 밀폐공간 구분 |
| `WKGBNNM` | str | WKGBNNM, 작업구분명, 작업구분 | 작업 구분 |
| `STDATE` | datetime | STDATE, 시작일시, 시작일, 작업시작, START | 작업 시작 |
| `EDDATE` | datetime | EDDATE, 종료일시, 종료일, 작업종료, END, FINISH | 작업 종료 |
| `PJT` | str | HULLNO, HULL_NO, 호선번호, 호선, PJT, PROJECT | 호선번호 (`ensure_sn`) |
| `AREA_DETAIL` | str | AREA_DETAIL, 작업위치, 위치, 장소, AREA | 작업 위치 |
| `WORK_NM` | str | WORK_NM, 작업명, 작업내용, 작업상세 | 작업 내용 — **자유입력. 실명 기재 금지**(LLM 전송 대상) |
| `ACODENM` | str | ACODENM, 작업허가대상, 작업코드명, 허가대상 | 작업허가 대상 — **`mapping` 조인 키** |
| `DATE` | str | (파생) | `"YY-MM-DD"` 문자열. 일별 확장 산물 |
| `risk_keywords` | str | (파생) | 위험 키워드 `", "` 구분. 미매핑·LLM 실패 시 결측 |
| `warning` | str | (파생) | 현장 경고문 1~2문장. 위와 동일 |

> **개인정보 최소수집** — 담당자 실명(`HNAME`)·HSE담당자(`HSE_MANAGE`)는 소비앱·LLM 어디에서도 쓰지 않아 **수집·저장하지 않는다**(2026-07 `COL_CANDIDATES` 에서 제거). TBM/RA 는 부서(`DEPTNM`/`TEAMNM`) 단위로만 다룬다.
> ⚠️ **단, 현 실파일에는 두 컬럼이 전량 `null` 로 잔존**한다 — 누적 병합(`concat`)이라 과거 컬럼이 사라지지 않기 때문. 신규 데이터에는 값이 채워지지 않는다. 소비앱은 **두 컬럼을 참조하지 말 것**(§7).

#### `mapping.parquet` — 작업유형별 위험요소 캐시

**생산자**: `ptw_enrich._merge_mapping`. 쓰기 직전 재읽기-병합으로 lost-update 를 막고 계속 누적된다. dedup 키 `work` (`keep=last`).

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `work` | str | 작업유형 = `ptwlist.ACODENM` **(조인 키, 유일)** |
| `keyword` | str | 위험 키워드 `", "` 구분 |
| `warning` | str | 현장 작업자용 경고 1~2문장 |

---

### 4.3 사외작업자 / 위험성평가 계열 — `out_converter.py`

> **[개인정보]** 이 두 테이블은 사외작업자 위험성평가 목적의 개인정보를 포함한다.
> - **표시 마스킹 필수**: 화면 출력은 `pii.mask_df_for_display`(→`mask_name`/`mask_phone`) 경유. parquet 원본은 위험성평가 실무상 원문 보관 → **하류 접근통제 대상**.
> - **최소수집**: `phone` 은 비상연락 목적으로만 보관. 대시보드·통계 목적의 확대 수집 금지.
> - **주의**: `ra_file` 경로 문자열에 실명이 포함될 수 있음(예 `RA_홍길동.pdf`) — 첨부파일명에서 실명 배제 권장.
> - `jsh_out`/`jsh_ra`(MSSQL) 및 `upload/out/_backup/` 원본 엑셀도 원문 보유 → 보존 최소·접근통제.

#### `out.parquet` — 사외작업자 방문 신청

**입력**: `upload/out/outside_*.xlsx` (복수 파일 concat)
**필터**: `방문부서` 가 `OUT_DEPT_FILTER`(시운전팀, 시운전, 기장운전, 전장운전, 선장운전, CSU, 친환경실증, 해운, 해운1과, 해운2과, 시운전기술, LNG설비운영) 중 하나를 **포함**하는 행만 유지.
**병합**: 기존 `out.parquet` 포함 concat 후 `[name, company, visit_start, visit_end, work_content]` dedup (`keep=last`) — 폴더에 최신 파일만 있어도 과거가 보존된다.

| Excel 컬럼 | parquet 컬럼 | 타입 | 설명 |
|---|---|---|---|
| 이름 | `name` | str | **[PII]** 성명 |
| 연락처 | `phone` | str | **[PII]** 연락처 |
| 회사명 | `company` | str | 소속 업체 |
| 방문시작 | `visit_start` | date | `YYYYMMDD` 8자리 및 일반 형식 파싱 |
| 방문종료 | `visit_end` | date | |
| 호선 / PJT | `project` | str | 호선번호 |
| 업무내용 | `work_content` | str | **자유입력 — 실명 기재 금지** |
| 접견자 | `greeter` | str | **[PII]** 사내 접견자명 |
| 방문부서 | `dept` | str | 방문 대상 부서 |

#### `ra.parquet` — 위험성평가 대상 (out 파생, 18컬럼)

**파생 규칙**: 방문기간을 **최대 7일(`RA_MAX_DAYS`) 단위로 분할**, 2일 미만 chunk 제외. **3년(`RA_KEEP_YEARS`) 초과 데이터 제외.**
**그룹 키**: `[company, period_start, period_end, work_content, dept]` — 같은 그룹의 `name`/`phone` 은 중복 제거 후 `", "` join.
**상태 복원**: 재생성 시 기존 `ra.parquet` 에서 카드키 `[company, period_start, period_end, work_content, dept]` 로 `RA_STATE_COLS` 를 복원. `manual == "Y"` 행 중 재생성 결과에 없는 카드키는 **그대로 보존**(신청 없는 수기 항목이 사라지지 않게).

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `name` | str | **[PII]** 그룹 내 성명을 `", "` join |
| `phone` | str | **[PII]** 위와 동일 |
| `company` | str | 업체명 **(그룹 키)** |
| `visit_start` `visit_end` | datetime | 원 방문기간 (※ `out` 은 `date`, `ra` 는 `timestamp` — 타입이 다름) |
| `project` | str | 호선번호 |
| `work_content` | str | 업무내용 **(그룹 키)** |
| `greeter` | str | **[PII]** 신청서상 접견자 |
| `dept` | str | 방문부서 **(그룹 키)** |
| `period_start` `period_end` | datetime | 7일 단위 분할된 평가 기간 **(그룹 키)** |
| `is_commissioning` | bool | `dept` 가 `COMMISSIONING_DEPTS` 포함 여부 |
| `ra_done` | str | 평가 완료 `"Y"`/`"N"` (기본 `"N"`). **소비앱 기록 → 재생성 시 복원** |
| `ra_file` | str | 평가서 파일 경로 (기본 `""`). **소비앱 기록 → 복원** / **[PII 주의]** |
| `excluded` | str | 제외 여부 `"Y"`/`"N"` (기본 `"N"`). **소비앱 기록 → 복원** |
| `exclude_reason` | str | 제외 사유 (기본 `""`). **소비앱 기록 → 복원** |
| `greeter_actual` | str | **[PII]** 실제 접견자 (기본 `""`). **소비앱 기록 → 복원** |
| `manual` | str | 수기 추가 행 표식 `"Y"`/`"N"` (기본 `"N"`). **`"Y"` 행은 재생성 시 보존** |

---

### 4.4 운영 DB 미러 계열 — `db_connector.py`

- MySQL 접속 실패 시 폴백: **기존 parquet → 빈 DataFrame** (사내망 전용본은 `data2` 더미 폴백을 제거했다. `_fallback()` 의 `dummy_name` 인자는 호출부 호환용으로 남아 있으나 사용되지 않는다). 폴백 시에는 **저장하지 않는다**(기존 파일 보존) — 사내 DB 장애에도 소비앱은 직전 parquet 으로 계속 동작한다.
- `SELECT *` 유입분은 `_drop_pii_cols` 로 인명/연락처류 컬럼 제거(최소수집). `milestone` 의 wide 이벤트 컬럼은 보존.
- 호선 컬럼은 `ensure_sn` 적용. `오전/오후` 한국어 datetime 은 셀 단위로 보정.

#### `pjtlist.parquet` — 호선 마스터 (MySQL `shipinfo`)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `SHIPNUM` | str | **호선번호 (`ensure_sn` 적용) — 이 테이블의 키** |
| `TITLE` | str | 호선명 |
| `TYPEMODEL` | str | 선형/모델 |
| `SHIPCLASS` | str | 선급 |
| `DOCK` | str | 도크 |
| `WORKFINISH` | datetime | 공사 완료일 |
| `ISTRIAL` | str | 시운전 대상 여부 |
| `ISOUTSIDE` | str | 사외 여부 |
| `REGOWNER` | str | 선주 |
| `PROJSEQ` | str | `"n/총수"` 형식 시리즈 순번 |
| `SHIPTYPE` | str | 선종 |
| `SEQNO` | int | 시리즈 내 순번 |
| `MAINHULLNUM` | str | 대표 호선 그룹 키 |

> `SELECT *` 기반이므로 운영 DB 컬럼이 추가되면 **여기 없는 컬럼이 함께 들어올 수 있다.** 소비앱은 필요한 컬럼만 선택해 사용할 것.

#### `milestone.parquet` — 호선 마일스톤 (MySQL `pjtevnt`, **raw wide — unpivot 하지 않음**)

`project` 1컬럼 + **15개 이벤트 × 3계열(PLAN/PROS/PERF) = 45컬럼**, 총 **46컬럼**. 세 계열 모두 동일한 15개 이벤트로 대칭이다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `project` | str | 호선번호 (`PJT` → `project` 리네임 + `ensure_sn`) **키** |
| `PLAN<EVT>` | datetime | 계획일 |
| `PROS<EVT>` | datetime | 예정일(전망) |
| `PERF<EVT>` | datetime | 실적일 |

`<EVT>` 목록 (15종): `LC` 기공 · `SP` · `BT` · `GT` · `CMR` · `COLDFROM`/`COLDTO` · `IE` · `MT` · `GASFROM`/`GASTO` · `STFROM`/`STTO` · `WF` 진수 · `DL` 인도

> `SELECT *` 기반이므로 운영 DB 에 이벤트가 추가되면 컬럼이 늘어난다. **컬럼 존재 여부를 확인하고 접근할 것.**
> ⚠️ ESG 는 wide 형태를 직접 소비하고, tbm 은 읽는 쪽에서 자체 unpivot 한다. **data_manager 는 unpivot 하지 않는다.**

#### `shipbbs.parquet` — 호선 게시/특이사항 (MySQL `shipbbs`)

`SELECT PJT, KIND, REMARK, INSERTDATE` 로 **필요 컬럼만 조회**(작성자 `INSERTBY` 미수집).

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `project` | str | 호선번호 (`ensure_sn`) |
| `kind` | str | 구분 |
| `remark` | str | 내용 |
| `insertdate` | str | 등록일시 (**문자열로 저장됨** — 소비 시 `pd.to_datetime` 필요) |

---

### 4.5 참조 / 부가 계열

#### `date.parquet` — 날짜 마스터 (`date_manager`)

기본 범위 `2025-01-01 ~ 오늘+7일`. 한국 공휴일은 `holidays.KR` 기준. **전체 재생성 교체.**

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `date` | date | **키** |
| `year` `month` `day` | int | |
| `weekday` | int | 0=월 … 6=일 |
| `weekday_name_kor` | str | `월`~`일` 한 글자 |
| `quarter` | int | 1~4 |
| `week_of_year` | int | ISO 주차 |
| `is_weekend` | bool | 토·일 |
| `is_holiday` | bool | 법정공휴일 |
| `holiday_name` | str | 공휴일명 (없으면 `""`) |
| `is_company_holiday` | bool | 회사 지정 휴일 |
| `is_business_day` | bool | `not (주말 or 공휴일 or 회사휴일)` |

#### `weather.parquet` — 기상청 단기예보 일 단위 요약 (`api_weather`)

**병합**: 동일 `forecast_date` 행을 제거한 뒤 신규를 append 하는 **upsert**, `forecast_date` 정렬.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `forecast_date` | date | 예보 대상일 **키** |
| `tmp_min` `tmp_max` `tmp_avg` | float | 최저/최고/평균 기온(℃) |
| `pop_max` | float | 최대 강수확률(%) |
| `pty_code` / `pty_label` | str | 강수형태 코드 / 한글 라벨 |
| `sky_code` / `sky_label` | str | 하늘상태 코드 / 한글 라벨 |
| `wsd_avg` `wsd_max` | float | 평균/최대 풍속(m/s) |
| `wav_height` | float | 파고(m) |
| `vec_deg` / `vec_dir` | float / str | 풍향 각도 / 방위 문자열 |
| `fetched_at` | datetime | 수집 시각 |
| `keyword` | str | 룰기반 위험 키워드 `", "` 구분 (동파위험/온열질환/미끄럼주의/강풍/수중작업금지). 해당 없으면 결측 |
| `warning` | str | 룰기반 경고문 `" / "` 구분. 해당 없으면 결측 |

#### `message.parquet` — 팀 안전메시지 (`message_store`)

`COLUMNS = ["date","team","content","ref_type","ref_path"]` 로 **항상 정규화 후 저장**. 구 스키마(`id`/`author`/`message`/`active`)는 로드 시 자동 매핑(`message`→`content`, `team="전체"`, `ref_type="없음"`, `ref_path=""`).

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `date` | date | 게시일 |
| `team` | str | 대상 팀 (기본 `"전체"`) |
| `content` | str | 메시지 본문 — **자유입력. 실명 기재 금지**(TBM 스크립트로 LLM 전송) |
| `ref_type` | str | 첨부 종류 (기본 `"없음"`) |
| `ref_path` | str | 첨부 경로 (`upload/message/`) |

#### `accident.parquet` — 사고사례 (`doc_parser.parse_accident_pdfs`)

**입력** `upload/accident/*.pdf` → 텍스트 추출 → LLM 이 사고 건별로 분리 추출. LLM 실패 시 파일당 1행 폴백(누락 방지). `skip_existing=True` 면 `pdf_filename` 기준 신규만 처리 후 append.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | str | `"<파일stem>#<seq>"` |
| `seq` | int | 문서 내 사고 순번 |
| `date` | str | 파싱 실행일 `YYYY-MM-DD` (**사고 발생일이 아님**) |
| `summary` | str | 사고 개요 1문장 |
| `cause` | str | 사고 원인 |
| `result` | str | 사고 결과/피해 |
| `countermeasure` | str | 재발방지 대책 |
| `accident_type` | str | 사고유형 (추락/화재/감전 등) |
| `keywords` | str | 위험 키워드 `", "` 구분 |
| `text` | str | PDF 전문 |
| `source` | str | 원본 PDF 파일명 |
| `pdf_filename` | str | 원본 PDF 파일명 (**증분 처리 키**) |

⚠️ 현 실파일은 구버전 산출물이라 `seq`/`text` 가 없다(§7).

#### `guide.parquet` — 안전가이드 (`doc_parser.parse_guide_pdfs`)

**입력** `upload/guide/*.pdf` → LLM 이 **표준ID 단위로 분리** 추출. 정렬 `[standard_id, pdf_filename, seq]`.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | str | `standard_id` 또는 `"<파일stem>#<seq>"` |
| `standard_id` | str | 표준/규격 번호 (예: `SG-001`). 없으면 `""` |
| `seq` | int | 문서 내 표준 순번 |
| `title` | str | 표준 제목 (LLM 실패 시 파일 stem) |
| `text` | str | PDF 전문 |
| `keywords` | str | 작업유형·위험요인 키워드 5~10개 |
| `pdf_filename` | str | 원본 PDF 파일명 (**증분 처리 키**) |
| `link` | str | 원본 PDF 전체 경로 |

⚠️ 현 실파일은 구버전 산출물이라 `standard_id`/`seq` 가 없다(§7).

---

## 5. MSSQL 매핑 (`db/tables.py`)

**규칙: MSSQL 테이블명 = `jsh_` + parquet stem (전부 소문자).** 화이트리스트 외 키는 `table_of()` 가 거부한다.

```
ptwlist, out, ra, mapping, message,
trial_schedule, fuel_usage, fuel_price, lng_usage, fuel_plan, pjtmethod,
weather, date, milestone, pjtlist, shipbbs
```

- 총 16개. `accident` / `guide` / `ptwlist_archive` 는 대상 아님.
- 전송 방식: **전체 교체(push)**. `sql/`·백업·사용자 정의 parquet 은 제외.
- `*_mssql` 변형 앱은 `jsh_*` 우선 읽기 + parquet 폴백이므로 **컬럼 구성은 본 문서와 동일**하다.

---

## 6. 개인정보(PII) 요약

| 테이블 | 컬럼 | 처리 |
|---|---|---|
| `out` | `name`, `phone`, `greeter` | 원문 저장, **표시 마스킹 필수** |
| `ra` | `name`, `phone`, `greeter`, `greeter_actual`, (`ra_file` 경로) | 원문 저장, **표시 마스킹 필수** |
| `ptwlist` | `HNAME`, `HSE_MANAGE` | **미수집** — 현 실파일 잔여 컬럼은 전량 결측, 참조 금지 |
| `shipbbs` | `INSERTBY` | **미수집** — `SELECT` 목록에서 제외 |
| 전 테이블 | 자유입력란(`WORK_NM`, `work_content`, `content`) · 첨부파일명 | **실명 기재 금지** (LLM 전송·경로 노출 방지) |

`db_connector._drop_pii_cols` 가 `SELECT *` 유입분에서 다음 토큰이 포함된 컬럼을 제거한다:
`hname, 담당자, 작성자, 신청자, 작업자, 성명, 이름, insertby, phone, 연락처, 휴대, 핸드폰, 전화, mobile, tel, hse_manage, email, 메일, 주민, 생년, 주소`

---

## 7. 코드 ↔ 실파일 불일치 (2026-07-29 기준)

소비앱은 **코드(생산자) 정의를 기준**으로 개발하되, 아래는 실파일에 남아 있으므로 방어 코드를 둔다.

> ⚠️ 아래 표는 **사외망 로컬 `data/parquet/` 스냅샷**을 대조한 결과다. **사내망 NAS 의 실파일은 별도 확인이 필요**하다(생성 시점이 달라 잔여 컬럼 구성이 다를 수 있음). 방어 코드 원칙(§0-4)은 두 환경 모두 동일하게 적용한다.

| 테이블 | 내용 | 소비앱 대응 |
|---|---|---|
| `ptwlist` | 실파일에 `HNAME`, `HSE_MANAGE` 가 전량 `null` 로 잔존 (누적 병합 특성상 자동 제거되지 않음) | **참조하지 말 것** |
| `ptwlist` | `PTW_AGBN`, `WKGBNNM` 이 현 실파일에서 전량 결측(`null` 타입) — 원본 엑셀에 해당 컬럼이 없었던 경우 | `notna()` 확인 후 사용 |
| `trial_schedule` | `대기` 전량 결측(`null` 타입) | 동일 |
| `accident` | 실파일에 `seq`, `text` 없음 (구버전 산출물). 재파싱하면 생성됨 | `if col in df.columns` |
| `guide` | 실파일에 `standard_id`, `seq` 없음 (구버전 산출물). 재파싱하면 생성됨 | 동일 |
| `ptwlist_archive` | **생산자 없음** — 갱신되지 않는 과거 스냅샷 | **참조 금지** |
| `out` ↔ `ra` | 같은 의미의 `visit_start`/`visit_end` 가 `out`=date, `ra`=timestamp 로 타입이 다름 | 비교 전 타입 통일 |

---

## 8. 새 테이블 추가 시 체크리스트

1. 생산자 모듈에서 `parquet_io.save_parquet_atomic(df, pc.get_parquet_dir() / "<name>.parquet")` 로 저장한다.
2. 저장 후 `pc.touch_sentinel()` 호출 (다운스트림 캐시 무효화).
3. MSSQL 전송이 필요하면 `db/tables.py` 의 `TABLES` 목록에 stem 을 추가한다 (테이블명은 `jsh_<stem>` 자동).
4. 무인 잡이면 종료 시 `job_status.write_status(job, ok, summary, error)` 를 기록한다.
5. 원본이 파일 업로드면 `path_config.UPLOAD_SECTIONS` 에 섹션을 추가하고 `_backup`/`_processed` 규약을 따른다.
6. 호선 컬럼은 반드시 `sn_util.ensure_sn` 을 통과시킨다.
7. 개인정보 컬럼은 **수집 자체를 재검토**하고, 불가피하면 §6 표에 등재 + 표시 마스킹 경로를 마련한다.
8. **본 문서 §2 일람표와 §4 스키마에 항목을 추가하고, 제목의 날짜를 갱신한다.**
