"""import_job.py — 업로드 폴더 무인 가져오기 (스케줄러용 헤드리스).

UI 없이 upload/{esg,ptw,out} 의 신규 파일을 스캔해 parquet로 변환·병합한다.
처리에 성공한 원본은 upload/<섹션>/_processed/ 로, 실패분은 _failed/ 로 이동해
다음 실행에서 재처리(재LLM·중복저장)되지 않게 한다(멱등).

  - ESG: upload/esg/*.xlsx        → esg_converter (PJT 단위 병합)
  - Out: upload/out/outside_*.xlsx→ out_converter (누적 병합)
  ※ TBM(ptwlist)은 ptw_watch_job(상시 워처)가 실시간 담당 — 여기서 제외.

저장이 발생하면 last_updated.txt(센티널)를 touch → 소비 앱 캐시 무효화.

실행:
    uv run python import_job.py    (또는 C:\\venvs\\data_manager\\Scripts\\python.exe import_job.py)

Windows 작업 스케줄러 예시(혼재 투입이라 잦은 주기 권장, 예: 30분마다):
    프로그램: <venv>\\Scripts\\python.exe   인수: import_job.py   시작 위치: F:\\code\\data_manager
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


def _move(fp: Path, dest_dir: Path) -> None:
    """원본을 dest_dir로 이동 (이름 충돌 시 타임스탬프 접미)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / fp.name
    if target.exists():
        target = dest_dir / f"{fp.stem}_{datetime.now():%Y%m%d%H%M%S}{fp.suffix}"
    try:
        fp.replace(target)          # 동일 볼륨 원자적 이동
    except Exception:
        import shutil
        shutil.move(str(fp), str(target))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    import path_config as pc
    import esg_converter, out_converter   # ptw 는 ptw_watch_job 워처가 담당
    import job_status

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pq = pc.get_parquet_dir()
    lines: list[str] = []
    any_saved = False
    any_fail = False

    def _ok(res: list[dict], name: str | None = None) -> bool:
        for r in res:
            if name and r.get("name") != name:
                continue
            if r.get("ok") and r.get("rows", 0) > 0:
                return True
        return False

    # ── ESG (파일별) ─────────────────────────────────────
    esg_dir = pc.get_upload_dir("esg")
    for fp in sorted(p for p in esg_dir.glob("*.xlsx") if p.is_file()):
        try:
            res = esg_converter.convert_and_save(fp.read_bytes(), fp.name, pq, pc.get_backup_dir("esg"))
            if _ok(res):
                any_saved = True
                _move(fp, pc.get_processed_dir("esg"))
                lines.append(f"  ESG {fp.name}: OK")
            else:
                any_fail = True
                _move(fp, esg_dir / "_failed")
                lines.append(f"  ESG {fp.name}: 처리결과 없음 → _failed")
        except Exception as e:
            any_fail = True
            _move(fp, esg_dir / "_failed")
            lines.append(f"  ESG {fp.name}: 실패({e}) → _failed")

    # ── TBM (ptwlist) ────────────────────────────────────
    # ptw_watch_job(상시 워처)가 upload/ptw 를 실시간 소유(DRM win32 + 최신 7개 유지).
    # 이중 처리/이동 충돌 방지 위해 import_job 에서는 ptw 를 다루지 않는다.

    # ── Out (전체 일괄, 누적 병합) ───────────────────────
    out_dir = pc.get_upload_dir("out")
    # 삭제(최신 7개 유지) 후 변환 — 초과 원본은 이미 out.parquet에 누적됨
    import tbm_converter
    tbm_converter.prune_uploads(out_dir, 7, out_converter.OUT_FILE_GLOBS)
    out_files = sorted({p for g in out_converter.OUT_FILE_GLOBS
                        for p in out_dir.glob(g) if p.is_file()})
    if out_files:
        try:
            pairs = [(fp.name, fp.read_bytes()) for fp in out_files]
            res = out_converter.convert_and_save(pairs, pq, pc.get_backup_dir("out"), pq / "ra.parquet")
            if _ok(res, "out"):
                any_saved = True
                for fp in out_files:
                    _move(fp, pc.get_processed_dir("out"))
                lines.append(f"  Out {len(out_files)}개: OK (누적 병합)")
            else:
                any_fail = True
                for fp in out_files:
                    _move(fp, out_dir / "_failed")
                lines.append(f"  Out {len(out_files)}개: 처리결과 없음 → _failed")
        except Exception as e:
            any_fail = True
            lines.append(f"  Out 일괄 실패({e}) — 원본 보존")

    if any_saved:
        pc.touch_sentinel()

    summary = "\n".join(lines) if lines else "신규 파일 없음"
    for ln in lines:
        print(ln)

    ok = any_saved or not any_fail   # 저장 성공 or (신규 없음 & 실패 없음)
    job_status.write_status("import", ok, summary)
    if any_saved and not any_fail:
        print(f"[{ts}] import_job OK")
        return 0
    if any_fail:
        print(f"[{ts}] import_job PARTIAL — 일부 실패(_failed 확인)")
        return 1
    print(f"[{ts}] import_job IDLE — 신규 파일 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
