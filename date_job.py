"""date_job.py — date.parquet 무인 생성 (스케줄러용 헤드리스).

UI 없이 정적 달력 테이블(기본 2025-01-01 ~ 오늘+7일)을 재생성해 date.parquet 에
원자적 저장한다. 범위가 '오늘+7'이라 매일 갱신해야 미래 7일이 따라온다.
자정 직후(예: 00:30) 일 1회 스케줄러로 등록한다.

실행:
    uv run python date_job.py

Windows 작업 스케줄러 예시(매일 00:30):
    프로그램: <venv>\\Scripts\\python.exe
    인수:     date_job.py
    시작 위치: F:\\code\\data_manager
"""
from __future__ import annotations

import sys
from datetime import datetime


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import date_manager
    import path_config as pc
    import job_status

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        df = date_manager.generate_date_table()
        date_manager.save_date_parquet(df)
        pc.touch_sentinel()  # 소비 앱 캐시 무효화
    except Exception as e:
        job_status.write_status("date", False, error=str(e))
        print(f"[{ts}] date_job FAIL — {e}")
        return 1
    job_status.write_status("date", True, f"{len(df)}일치 저장")
    print(f"[{ts}] date_job OK — {len(df)}일치 저장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
