"""ptw_watch_job.py — upload/ptw 폴더 감시 무인 ptwlist 생성 (상시 실행).

watchdog로 upload/ptw 의 ptwlist_*.xlsx 변경(생성/수정)을 감지해 즉시 변환한다.
DRM 파일은 tbm_converter.read_drm_excel(win32 Excel)로 열고, 실패 시 read_excel 폴백.
변환 성공 시: ptwlist.parquet 갱신 → touch_sentinel() → upload 최신 7개 유지 → job_status 기록.

기동 시 다운타임 중 변경분(파일 mtime > parquet mtime)을 1회 재조정한다.
watchdog 미설치 시 폴링 폴백으로 동작.

실행(작업 스케줄러, 로그온 시 1회 상시 실행 권장):
    프로그램: <venv>\\Scripts\\python.exe   인수: ptw_watch_job.py   시작 위치: F:\\code\\data_manager
"""
from __future__ import annotations

import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

KEEP_UPLOADS = 7      # upload/ptw 에 유지할 최신 원본 파일 수
DEBOUNCE_SEC = 2.0    # 이벤트 후 파일 쓰기 완료 대기(연속 이벤트 합치기)
POLL_SEC     = 10.0   # watchdog 미설치 시 폴링 주기


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _is_target(path: Path, ptw_dir: Path | None = None) -> bool:
    """감시 대상 판정 — upload/ptw **직속**의 ptwlist_*.xlsx 만.

    ptw_dir 을 주면 부모 폴더까지 확인한다. 변환 성공분을 하위 _processed/ 로 옮기면
    watchdog 의 on_moved 가 dest_path(=_processed/ptwlist_*.xlsx)로 발화하는데,
    이름만 보면 대상으로 오인해 '변환 → 이동 → 다시 이벤트' 루프에 빠진다.
    """
    if not (path.suffix.lower() == ".xlsx" and path.name.lower().startswith("ptwlist_")):
        return False
    if ptw_dir is not None and path.parent.resolve() != Path(ptw_dir).resolve():
        return False
    return True


def _wait_stable(path: Path, tries: int = 12, interval: float = 0.5) -> bool:
    """파일 크기가 안정될 때까지 대기(쓰기 중 열기 방지). 안정되면 True.
    stat 실패(DRM/네트워크)가 반복되면 win32 리더에 위임하기 위해 진행(True)."""
    last = -1
    stat_fail = 0
    for _ in range(tries):
        try:
            sz = path.stat().st_size
        except OSError:
            stat_fail += 1
            if stat_fail >= 3:          # 계속 stat 불가 → 크기 안정 판단 포기, 읽기 시도 허용
                return True
            time.sleep(interval)
            continue
        if sz == last and sz > 0:
            return True
        last = sz
        time.sleep(interval)
    return True


def process_file(path: Path) -> None:
    """단일 ptwlist 파일 변환 + 센티널 + 원본 _processed 이동 + 상태 기록."""
    import path_config as pc
    import tbm_converter
    import job_status

    ptw_dir = pc.get_upload_dir("ptw")
    if not path.exists() or not _is_target(path, ptw_dir):
        return
    if not _wait_stable(path):
        _log(f"불안정/누락 스킵: {path.name}")
        return

    pq = pc.get_parquet_dir()
    try:
        # 변환 전 백업 사본(_backup) → 성공 시 원본은 _processed 로 이동
        res = tbm_converter.convert_path(path, pq, pc.get_backup_dir("ptw"))
        ok = any(r.get("ok") and r.get("rows", 0) > 0 for r in res)
        msg = "; ".join(r.get("msg", "") for r in res if r.get("msg"))
        if ok:
            pc.touch_sentinel()
            moved = pc.move_file(path, pc.get_processed_dir("ptw")).name
            # 백업·처리완료 보관분만 최신 KEEP_UPLOADS 개 유지 (업로드 폴더는 이동으로 이미 비워짐)
            # 백업 사본은 "YYYYMMDD_ptwlist_*.xlsx" 라 기본 패턴(ptwlist_*)에 안 걸린다 → *.xlsx 로
            n_bk = tbm_converter.prune_uploads(pc.get_backup_dir("ptw"), KEEP_UPLOADS, ("*.xlsx",))
            n_pr = tbm_converter.prune_uploads(pc.get_processed_dir("ptw"), KEEP_UPLOADS, ("*.xlsx",))
            _log(f"OK {path.name}: {msg} (→ _processed/{moved}, prune 백업 {n_bk}·완료 {n_pr})")
            job_status.write_status("ptw_watch", True, f"{path.name}: {msg}")
        else:
            _log(f"결과없음 {path.name}: {msg}")
            job_status.write_status("ptw_watch", False, f"{path.name}: {msg}")
    except Exception as e:
        _log(f"실패 {path.name}: {e}")
        job_status.write_status("ptw_watch", False, f"{path.name}", error=str(e))


def _safe_mtime(p: Path):
    """mtime 조회. DRM/네트워크로 실패하면 None(미상)."""
    try:
        return p.stat().st_mtime
    except Exception:
        return None


def _list_ptwlist(ptw_dir: Path) -> list[Path]:
    """ptwlist_*.xlsx 경로 목록 — os.listdir(이름만, stat·디렉토리 stat 미접근, DRM 안전).
    Path.glob 은 시작 시 부모 디렉토리에 is_dir()=stat 를 호출하므로 사용하지 않는다."""
    try:
        names = os.listdir(ptw_dir)
    except Exception:
        return []
    return [ptw_dir / n for n in names
            if n.lower().endswith(".xlsx") and n.lower().startswith("ptwlist_")]


def _reconcile(ptw_dir: Path, pq_dir: Path) -> None:
    """기동 시: ptwlist.parquet 보다 새 파일(다운타임 중 변경분)만 1회 처리.
    mtime 미상(DRM)이면 누락 방지를 위해 처리 대상에 포함한다."""
    parquet = pq_dir / "ptwlist.parquet"
    base = _safe_mtime(parquet) or 0.0
    todo = []
    for p in _list_ptwlist(ptw_dir):             # os.listdir 이름매칭(stat 미접근, DRM 안전)
        mt = _safe_mtime(p)
        if mt is None or mt > base:              # 미상이면 일단 처리
            todo.append((mt if mt is not None else float("inf"), p))
    for _mt, p in sorted(todo, key=lambda x: x[0]):
        _log(f"재조정 처리: {p.name}")
        process_file(p)


def _run_watchdog(ptw_dir: Path) -> bool:
    """watchdog 감시 루프. 미설치면 False 반환(폴링 폴백)."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except Exception:
        return False

    timers: dict[str, threading.Timer] = {}

    def schedule(path: Path) -> None:
        if not _is_target(path, ptw_dir):   # 하위 _processed/_backup 이동분은 제외 (루프 방지)
            return
        key = str(path)
        old = timers.get(key)
        if old is not None:
            old.cancel()

        def fire() -> None:
            timers.pop(key, None)
            process_file(path)

        t = threading.Timer(DEBOUNCE_SEC, fire)
        timers[key] = t
        t.start()

    class _Handler(FileSystemEventHandler):
        def on_created(self, e):
            if not e.is_directory:
                schedule(Path(e.src_path))

        def on_modified(self, e):
            if not e.is_directory:
                schedule(Path(e.src_path))

        def on_moved(self, e):
            if not e.is_directory:
                schedule(Path(e.dest_path))

    obs = Observer()
    obs.schedule(_Handler(), str(ptw_dir), recursive=False)
    obs.start()
    _log(f"watchdog 감시 시작: {ptw_dir}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        obs.stop()
        obs.join()
    return True


def _run_polling(ptw_dir: Path) -> None:
    """watchdog 미설치 시 폴링 폴백 — mtime 변경 감지."""
    _log(f"폴링 감시 시작({POLL_SEC}s): {ptw_dir}")
    seen: dict[str, float | None] = {
        p.name: _safe_mtime(p) for p in _list_ptwlist(ptw_dir)
    }
    while True:
        time.sleep(POLL_SEC)
        try:
            for p in _list_ptwlist(ptw_dir):
                mt = _safe_mtime(p)
                if p.name not in seen:                      # 신규 → mtime 미상이어도 1회 처리
                    seen[p.name] = mt
                    process_file(p)
                elif mt is not None and seen[p.name] != mt:  # 변경 감지(mtime 가용 시)
                    seen[p.name] = mt
                    process_file(p)
                # 이미 본 파일 + mtime 미상 → 재처리 루프 방지 위해 스킵
        except Exception as e:
            _log(f"폴링 오류: {e}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    import path_config as pc
    ptw_dir = pc.get_upload_dir("ptw")
    ptw_dir.mkdir(parents=True, exist_ok=True)

    _reconcile(ptw_dir, pc.get_parquet_dir())

    if not _run_watchdog(ptw_dir):
        _run_polling(ptw_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
