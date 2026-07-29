# Data Manager — 요구사항 정의서 (현재 상태)

> 작성: 2026-06-11 · 역할: **단일 생산자(Producer)**. tbm·esg는 소비.
> 시크릿/설정 단일 소스: `F:\code\secret\.env` + 공용 `app_config.py`(동봉).

---

## 1. 개요

| 항목 | 내용 |
|---|---|
| 목적 | 조선소 시운전팀 데이터의 **단일 생산자** — 엑셀/DB/PDF/API → `parquet` 변환·병합 저장 |
| 역할 | 모든 공유 parquet(`F:\code\data\parquet`)의 **유일한 생산 주체**. tbm/esg는 읽기만 |
| 진입점 | `app.py`(포트 8510) — 모니터링 UI |
| 무인 처리 | 스케줄러 잡(`import/doc/db/date/weather_job`) — 브라우저 없이 생산 |
| 언어/환경 | 한국어 · Streamlit · Python 3.12 (검증 venv `C:\venvs\data_manager`) |
| 패키지 관리 | `uv` (`pyproject.toml` / `uv.lock`) |

## 2. 생산 데이터 & 병합 전략

| 데이터 | 입력 | 병합 |
|---|---|---|
| ESG 6종(trial_schedule/fuel_usage/fuel_price/lng_usage/fuel_plan/pjtmethod) | `upload/esg/*.xlsx` | **PJT 단위 교체** |
| ptwlist | `upload/ptw/ptwlist_*.xlsx` | dedup + LLM 위험키워드 보강 (아카이브 분리는 미구현 — `ptwlist_archive`는 생산자 없음) |
| out, ra | `upload/out/outside_*.xlsx` | **누적 병합**(과거 보존) + ra_done 복원 |
| pjtlist, milestone(**raw wide**), shipbbs | MySQL(shipinfo/pjtevnt/shipbbs) | DB→parquet, `ensure_sn` |
| weather / accident·guide / mapping / date / message | 기상청 API / PDF·LLM / LLM / 정적 / 입력 | 각 생성 |

- 마스터 키 `PJT`(예 `SN2601`). 저장 직전 `sn_util.ensure_sn`로 SN 수렴.
- 저장은 `parquet_io.save_parquet_atomic`(임시파일→rename, 원자적) + `path_config.touch_sentinel()`.

## 3. 무인 파이프라인 (스케줄러 잡)

| 잡 | 주기(권장) | 내용 |
|---|---|---|
| `import_job.py` | 30분 | `upload/{esg,ptw,out}` 신규 변환 → 성공분 `_processed/` 이동(멱등), 실패 `_failed/` |
| `doc_job.py` | 일 1회 | `upload/{accident,guide}` PDF **증분** 파싱(처리분 스킵, LLM 절감) |
| `db_job.py`(사내망) | 일 1회 | pjtlist/milestone/shipbbs 재생성. DB 미연결 시 기존 보존 |
| `date_job.py` | 일 1회 | date.parquet(오늘+7) 갱신 |
| `weather_job.py` | 일 2회 | 기상청 → weather.parquet |
| `ra_job.py` | (선택) | out.parquet → ra.parquet 파생. UI 버튼·import_job과 동일 기능 — 스케줄 등록 여부 미확인 |

- 모든 잡: 헤드리스·원자적·`0/1` 종료·`parquet/_jobstatus/<job>.json` 기록 → UI에서 상태 확인.

## 4. UI (모니터링 + 수동 fallback)

`catalog_view.py` 재디자인 UI: **좌측 카테고리 내비(전체/유류·ESG/안전·작업허가/마스터·SQL/자동수집/문서/메시지) + 통합 카탈로그 + 상세**, 기능 화면 4종(**데이터 변환 / parquet 추가 / 문서 파싱 / 안전메시지**)은 handlers 위임.
- 카탈로그: 전 parquet 행수·컬럼·호선수·최신도·상태 표 + 필터바(호선/기간/최신도/검색), 상세에서 미리보기·컨텍스트 액션(ra 파생, 키워드 재생성, DB 재생성).
- 데이터 변환(수동 fallback): 즉시 변환/생성 버튼 + 임시 업로드(simulate 미리보기) + 운영서버(MSSQL) 전송 expander. 평소엔 잡이 자동 처리.
- 안전메시지: 직접 입력(date/team/content/ref_type/ref_path) — tbm 스크립트 소비 스키마와 일치.

## 5. 핵심 모듈

```
data_manager/
├── app.py                  # 모니터링 UI (8510)
├── app_config.py           # 공용 단일 config(동봉, .env)
├── check_env.py            # 사내망 가동 전 점검(doctor)
├── path_config.py          # 경로(app_config 위임) + 업로드/백업/processed/message + 센티널
├── settings.py             # AppSecrets — app_config 위임
├── parquet_io.py           # save_parquet_atomic
├── sn_util.py              # ensure_sn
├── datetime_util.py        # 컨버터 공통 날짜 파싱 프리미티브
├── esg_converter / tbm_converter / out_converter   # 엑셀→parquet
├── db_connector.py         # DB→parquet 생성기(gen_pjtlist/milestone/shipbbs/gen_all)
├── doc_parser.py / ptw_enrich.py / message_store.py / date_manager.py / api_weather.py
├── job_status.py           # _jobstatus 기록/조회
└── {import,doc,db,date,weather}_job.py   # 무인 잡
```

## 6. 의존성 (`pyproject.toml` / `uv.lock`)

`streamlit`, `pandas`, `pyarrow`, `openpyxl`(코어) · `sqlalchemy`, `pymysql`(DB) ·
`pdfplumber`(PDF) · `openai`(LLM) · `holidays`(date) · `requests`(날씨) ·
**`python-dotenv`**(app_config 필수).

> 로컬 venv 권장(네트워크 드라이브 numpy 오류 회피): `C:\venvs\data_manager`.

## 7. 실행

```powershell
# UI
C:\venvs\data_manager\Scripts\python.exe -m streamlit run app.py --server.port 8510
# 무인 잡(작업 스케줄러)
C:\venvs\data_manager\Scripts\python.exe import_job.py   # 등 각 잡
# 점검
C:\venvs\data_manager\Scripts\python.exe check_env.py
```

## 8. 핵심 규칙

- 마스터 키 `PJT`. milestone은 **raw wide**로 저장(ESG는 wide 직접 읽고, tbm은 read 시 unpivot).
- 컬럼 매핑은 후보 키워드 리스트 방식(새 컬럼 대응 시 후보 추가).
- 저장 후 `touch_sentinel()` 호출(소비 앱 캐시 무효화).
- 날짜 파싱 다형식(Excel serial·AM/PM·오전/오후·`%y/%m/%d %H:%M`) — `datetime_util`.
- 상세 스키마: `DATA_SCHEMA.md` 참조.
