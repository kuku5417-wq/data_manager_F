"""weather_job.py — 날씨 무인 수집 (스케줄러용 헤드리스).

UI 없이 기상청 예보를 수집해 weather.parquet 에 원자적 저장.
일 2회(06:30, 12:00) 스케줄러(작업 스케줄러/cron)로 등록한다.

실행:
    uv run python weather_job.py

Windows 작업 스케줄러 예시(매일 06:30, 12:00):
    프로그램: <venv>\\Scripts\\python.exe
    인수:     weather_job.py
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
    import api_weather
    import job_status
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df, status = api_weather.collect_all(progress_cb=lambda s, m: print(f"  {m}"))
    if status == "success":
        job_status.write_status("weather", True, f"{len(df)}일치 저장")
        print(f"[{ts}] weather_job OK — {len(df)}일치 저장")
        return 0
    job_status.write_status("weather", False, error=str(status))
    print(f"[{ts}] weather_job FAIL — {status}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
