# data_manager (사내망 전용 · data_manager_F)

조선소 시운전팀 데이터 업로드 관리 Streamlit 앱 — Excel 업로드 → parquet 변환·병합 저장. `code_N` 앱군의 **모든 parquet 단일 생산자**이며, 저장 완료된 parquet 을 운영서버 MSSQL(`jsh_*`)에 순차 자동 전송한다(엑셀 → parquet → MSSQL).

> **사내망 전용 고정본**. 사외망 자동감지·로컬경로·프록시 OFF·더미·Upstage/OpenAI 폴백 제거, 사내망(NAS·프록시 ON·SOLA·MySQL/MSSQL) 고정. `NAS_BASE_PATH` 필수.

## 실행

```bash
# 정식 실행 (로컬 venv, 포트 8510)
run.bat

# 또는 uv 직접 실행
uv run streamlit run app.py --server.port 8510
```

최초 1회 `setup_env.bat`(venv 생성 + 의존성 설치), 가동 전 점검은 `python check_env.py`.

## 문서

- **[시스템_개요_사내망.md](시스템_개요_사내망.md) — 시스템 담당자용 종합 개요(스키마·라이브러리·DB·잡·env)**
- [CLAUDE.md](CLAUDE.md) — 파일 구조·핵심 규칙
- [requirements.md](requirements.md) — 시스템 요구사항·무인 파이프라인
- [data_structure.md](data_structure.md) — parquet 스키마·변환 규칙
