"""ra_job.py — out.parquet → ra.parquet 파생 (스케줄러용 헤드리스).

UI 없이 현재 out.parquet 에서 ra.parquet 을 파생·저장(원자적)한다.
기존 평가완료 상태(ra_done='Y', ra_file)는 자동 복원한다.
out.parquet 은 외부에서 직접 생성·배치하고, 이 잡은 ra 파생만 담당한다.

핵심 로직은 out_converter.regenerate_ra() 단일 소스를 재사용 — 앱 버튼과 동일 결과.

실행:
    uv run python ra_job.py
    (또는)  C:\\venvs\\data_manager\\Scripts\\python.exe ra_job.py

Windows 작업 스케줄러 등록 예시:
    프로그램:  C:\\venvs\\data_manager\\Scripts\\python.exe
    인수:      ra_job.py
    시작 위치:  F:\\code\\data_manager
    트리거:    out.parquet 갱신 주기에 맞춰(예: 매일 1회) 등록

종료코드: 0=성공, 1=실패 (스케줄러에서 성공/실패 판별용)
"""
from __future__ import annotations

import sys
from datetime import datetime


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    import out_converter
    import path_config as pc
    try:
        import job_status
    except Exception:
        job_status = None

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    res = out_converter.regenerate_ra(pc.get_parquet_dir())

    if res.get("ok"):
        try:
            pc.touch_sentinel()          # 다운스트림 캐시 무효화
        except Exception:
            pass
        if job_status:
            job_status.write_status("ra", True, res.get("msg", ""))
        print(f"[{ts}] ra_job OK — {res.get('msg', '')}")
        return 0

    if job_status:
        job_status.write_status("ra", False, error=res.get("msg", "실패"))
    print(f"[{ts}] ra_job FAIL — {res.get('msg', '실패')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
