"""doc_job.py — 문서(PDF) 무인 파싱 (스케줄러용 헤드리스).

UI 없이 upload/{accident,guide} 의 PDF를 파싱해 accident/guide.parquet 에 누적한다.
skip_existing=True 로 **이미 parquet에 있는 PDF는 재파싱(재LLM)하지 않고** 신규만 처리 →
매 실행 LLM 비용·중복을 막는다. PDF 원본은 라이브러리로 폴더에 그대로 둔다(이동 안 함).

저장(신규)이 발생하면 last_updated.txt(센티널)를 touch.

실행:
    uv run python doc_job.py
Windows 작업 스케줄러 예시(일 1회):
    프로그램: <venv>\\Scripts\\python.exe   인수: doc_job.py   시작 위치: F:\\code\\data_manager
"""
from __future__ import annotations

import sys
from datetime import datetime


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    import path_config as pc
    import doc_parser
    import job_status

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    err = ""
    before_a = before_g = 0
    try:
        ea = doc_parser.load_accident()
        before_a = 0 if ea is None else len(ea)
        eg = doc_parser.load_guide()
        before_g = 0 if eg is None else len(eg)

        acc = doc_parser.parse_accident_pdfs(skip_existing=True)
        gui = doc_parser.parse_guide_pdfs(skip_existing=True)
        new_a = len(acc) - before_a
        new_g = len(gui) - before_g
        lines.append(f"  accident: +{max(new_a,0)}건 (총 {len(acc)})")
        lines.append(f"  guide:    +{max(new_g,0)}건 (총 {len(gui)})")
        if new_a > 0 or new_g > 0:
            pc.touch_sentinel()
        ok = True
    except Exception as e:
        ok = False
        err = str(e)
        lines.append(f"  실패: {e}")

    for ln in lines:
        print(ln)
    job_status.write_status("doc", ok, "\n".join(lines), err)
    if ok:
        print(f"[{ts}] doc_job OK")
        return 0
    print(f"[{ts}] doc_job FAIL — {err}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
