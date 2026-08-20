# Data Manager (사내망 전용 · data_manager_F)

조선소 시운전팀 데이터 업로드 관리 Streamlit 앱. Excel 업로드 → parquet 변환·병합 저장.

> **사내망 전용 고정본**. 원본 data_manager의 사외망 자동감지·로컬경로·프록시 OFF·더미 폴백·Upstage/OpenAI LLM 폴백을 제거하고 사내망(NAS·프록시 ON·SOLA·MySQL/MSSQL)으로 고정. `IS_INTERNAL=True`, `NAS_BASE_PATH` 미설정 시 기동 거부. 시스템 담당자 개요는 **`시스템_개요_사내망.md`** 참조.

## 실행

```bash
uv run streamlit run app.py --server.port 8510
```

- 패키지 관리: `uv` (pyproject.toml / uv.lock)
- Python 3.12, 주요 의존성: streamlit, pandas, openpyxl, pyarrow

## 파일 구조

| 파일 | 역할 |
|---|---|
| `app.py` | 메인 진입점 — 변환/추가/문서/메시지 화면 렌더 함수 + `catalog_view.render_app` 라우터 |
| `catalog_view.py` | 재디자인 UI — 좌측 카테고리 내비 + 통합 카탈로그 + 상세 화면 (기능 화면은 handlers 위임) |
| `table_actions.py` | 카탈로그 테이블 단위 변환 — `RUN_SINGLE`(딸깍 실행) / `RUN_MENU`(변환 화면 유도) 레지스트리 + `run(key)`. 폴더 스캔 헬퍼(`scan_folder`) 정본 |
| `path_config.py` | NAS(사내망)/로컬(사외망) 경로 자동 전환, 업로드/백업 폴더 관리 |
| `esg_converter.py` | ESG 엑셀 6개 시트 → parquet 6개 (PJT 단위 병합) |
| `tbm_converter.py` | 작업허가서 원본(`PTW_FILE_GLOBS` = `ptwlist*` / `밀폐구역*`, xlsx·xls) → 일별 확장 → ptwlist.parquet. **파일 인식 패턴 정본** — 워처·폴더전체변환·카탈로그 변환이 모두 이 상수를 쓴다 (아카이브 분리는 미구현 — `ARCHIVE_DAYS` 상수만 존재) |
| `out_converter.py` | `outside_*.xlsx` → out.parquet + ra.parquet 파생 |
| `ui_components.py` | 신호등 diff 분석, 카드·컬럼매핑 컴포넌트 |
| `ui_styles.py` | 산업형 디자인 CSS |
| `sn_util.py` | `ensure_sn` — 호선번호 SN 부착 통일 (저장 정본 규칙) |
| `parquet_io.py` | `save_parquet_atomic` — 임시파일→rename 원자적 저장 + 저장 성공 시 MSSQL 자동 전송 훅 |
| `db/` | 운영서버 MSSQL(jsh_*) 전송 — `connection`(엔진) / `tables`(화이트리스트) / `sync`(전체 교체 push) / `auto_sync`(저장된 parquet 순차 자동 push) |
| `settings.py` | `.env` SECTION_KEY → AppSecrets(proxies/ssl/db_url/sola/weather) |
| `llm_client.py` / `api_weather.py` / `doc_parser.py` / `date_manager.py` / `db_connector.py` | tbm 이식 — LLM·날씨·PDF·달력·DB생성 (중앙집중) |
| `ptw_enrich.py` | ptwlist risk_keywords/warning (mapping+미매핑 LLM, 실패내성) |
| `message_store.py` | message.parquet CRUD |
| `weather_job.py` | 무인 날씨수집 (스케줄러 일 2회) |

**통합(중앙집중)**: data_manager가 모든 parquet의 단일 생산자. tbm_system_v6·esg_260605는 `F:\code\data\parquet` 소비. 상세는 `통합_구현완료_체크리스트.md`, `..\tbm_system_v6\데이터통합_결정서.md` 참조.

## 데이터 경로

- **사내망 NAS 전용**: `{NAS_BASE_PATH}\` (`...\06_Commissioning_TEAM`까지 포함한 전체 경로, secret 파일에서 읽음). 로컬 폴백 없음
- `parquet/` 최종 저장, `upload/{esg,ptw,out}/` 섹션별 업로드, `upload/<section>/_backup/` 원본 백업, `sql/` DB 대체 parquet
- **상세 스키마·변환 규칙은 반드시 `DATA_SCHEMA.md`(데이터 스키마 정의서, 정본) 참조** — 테이블 추가·변경 시 이 문서도 함께 갱신하고 제목의 날짜를 바꾼다. **스키마 변경 시 `data_manager` / `data_manager_F` 양쪽 `DATA_SCHEMA.md` 를 동일하게 갱신**할 것(경로·폴백 절만 환경별로 다름)

## 핵심 규칙

- 마스터 키는 `PJT` (호선번호, 예: SN2601). 구버전 `호선` 컬럼은 PJT로 리네임하는 하위호환 유지
- 병합 전략: ESG = PJT 단위 교체 / TBM = `[DEPTNM, PJT, AREA_DETAIL, ACODENM, DATE]` dedup / Out = 전체 재생성 + ra_done 상태 복원
- 컬럼 매핑은 후보 키워드 리스트 방식 (`ESG_SHEET_CANDIDATES`, `COL_CANDIDATES`, `OUT_COL_MAP`) — 새 컬럼 대응 시 후보 추가로 해결
- parquet 저장 후 `pc.touch_sentinel()` 호출 (다운스트림 캐시 무효화)
- **업로드 원본 수명주기**: 변환 성공 원본은 `_backup/` 에 사본을 남기고 `_processed/` 로 **이동**(`pc.move_file`).
  업로드 폴더에는 미처리분만 남아 같은 파일을 다시 읽지 않는다(ptw 는 파일당 Excel COM 을 새로 띄운다).
  실패분은 폴더에 남거나 `_failed/` 로 격리 → 재시도 가능
  - **컨버터의 `backup_dir` 인자에는 반드시 `pc.get_backup_dir(section)`** 을 넘긴다.
    업로드 폴더를 넘기면 사본이 업로드 폴더에 쌓이고, ESG 는 스캔 패턴이 `*.xlsx` 라 그 사본이
    다음 회차에 재변환돼 **회차마다 파일이 불어난다**(2026-07-30 수정. 잔여물 정리는 `_cleanup_uploads.py`)
  - 폴더 전체 변환에서 **변환 전 `prune_uploads` 선삭제를 하지 말 것** — 미처리 파일이 변환 전에 지워진다.
    정리는 변환 후 `_prune_upload(section)`(원본·`_backup`·`_processed` 각 최신 7개)
  - `ptw_watch_job._is_target` 은 **`upload/ptw` 직속 파일만** 대상으로 한다 — 하위 `_processed/` 로의
    이동이 `on_moved` 로 재트리거돼 무한 루프가 되는 것을 막는다
- **테이블 단위 변환**(`table_actions.py`): 카탈로그 목록 행 `↻ 변환`(`?run=키`) / 상세 버튼에서 딸깍 1회 실행. 대상은 **"원본 → 그 테이블 하나"인 변환만**(`RUN_SINGLE` 8종). ESG 6종·out 은 원본 1개에서 parquet 여러 개가 함께 나오므로 제외(`RUN_MENU`) — 테이블별로 쪼개면 DRM Excel COM 재읽기가 테이블 수만큼 곱해지고 산출물 간 갱신 시점이 어긋난다. 대신 변환 화면의 해당 섹션 단독 렌더로 유도(`session_state["cv_sec"]`)
  - `table_actions` 핸들러에서 **`prune_uploads` 를 호출하지 말 것** — 원본을 `unlink()` 한다.
    단 **변환 성공분의 `_processed/` 이동은 허용** — 삭제가 아니고 `_backup/` 사본도 남아 규칙 취지(원본 소실 방지)에 어긋나지 않는다
  - `ra` 는 `regenerate_ra`(out 에서 파생만) 사용. `out_converter.convert_and_save` 는 out+ra 를 함께 써서 out 까지 덮어씀
  - `?run=` 은 실행 직후 쿼리파라미터를 지운다 (새로고침 재실행 방지)
- **MSSQL 자동 전송**: 중앙 parquet 폴더에 `db/tables.py TABLES` 대상 parquet 저장 완료 시 `db/auto_sync.py`가 **저장된 parquet 파일을 다시 읽어** `jsh_*` 테이블 전체 교체 push (순차: 엑셀 → parquet → MSSQL). `.env DB_*` 미설정이면 휴면, 실패해도 parquet 저장에는 영향 없음(best-effort). sql/·백업·사용자 정의 parquet 은 제외
- 날짜 파싱은 다형식 지원 필수 (Excel serial, AM/PM, 오전/오후, `%y/%m/%d %H:%M`)
- **개인정보(PII)**: 생산 단계 **최소수집** — PTW 담당자명(`HNAME`/`HSE_MANAGE`)·shipbbs 작성자(`INSERTBY`)는 미수집, `SELECT *` 유입분은 `db_connector._drop_pii_cols`로 제거. **표시 마스킹** — out/ra/ptwlist 미리보기는 `pii.mask_df_for_display`(→`mask_name`/`mask_phone`) 경유. parquet·MSSQL 원본은 위험성평가 실무상 원문 보관(하류 접근통제 대상). 상세는 `DATA_SCHEMA.md` §6 개인정보 요약 참조

## 주의

- **사내망 고정**: `app_config.py`는 사외망 분기를 제거한 전용 사본(원본 전-repo 동기 원칙에서 의도적 분기). LLM=SOLA 단일, 프록시 항상 활성.
- 시크릿 파일: 상위 폴더 `secret\.env` (저장소 외부, 커밋 금지) — API 키, DB 접속정보, 프록시, `NAS_BASE_PATH` 포함. `path_config.read_secret("KEY")`로 읽을 것
- 시뮬 체크박스 = 저장 없이 미리보기. 저장 로직 수정 시 simulate 분기 유지할 것
- UI 텍스트는 한국어
