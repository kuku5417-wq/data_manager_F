"""db_job.py — DB→parquet 무인 생성 (스케줄러용 헤드리스).

UI 없이 MySQL(shipinfo/pjtevnt/shipbbs)을 끌어와 pjtlist/milestone/shipbbs.parquet 에
원자적 저장한다. 사내망에서 일 1회(예: 07:30) 스케줄러로 등록한다.

소비 앱(tbm·esg)은 더 이상 이 parquet를 생산하지 않으므로(읽기 전용),
신선도는 이 잡 + UI "DB 생성 실행" 버튼이 책임진다. DB 미연결(사외망) 시
기존 parquet를 보존하고 덮어쓰지 않는다(gen_*는 성공 시에만 저장).

실행:
    uv run python db_job.py

Windows 작업 스케줄러 예시(매일 07:30):
    프로그램: <venv>\\Scripts\\python.exe
    인수:     db_job.py
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
    import db_connector
    import path_config as pc
    import job_status

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = db_connector.gen_all()
    summary = "\n".join(f"  {r['name']}: {r['rows']}행 — {r['msg']}" for r in results)
    for r in results:
        print(f"  {r['name']}: {r['rows']}행 — {r['msg']}")

    # gen_*는 성공 시 msg가 "✅"로 시작. 하나라도 저장됐으면 센티널 갱신.
    saved = [r for r in results if r["msg"].startswith("✅")]
    if saved:
        pc.touch_sentinel()  # 소비 앱 캐시 무효화

    if len(saved) == len(results):
        job_status.write_status("db", True, summary)
        print(f"[{ts}] db_job OK — {len(saved)}종 생성")
        return 0
    job_status.write_status("db", bool(saved), summary,
                            "" if saved else "전종 폴백(DB 미연결)")
    print(f"[{ts}] db_job PARTIAL — {len(saved)}/{len(results)}종 생성(나머지 폴백)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
