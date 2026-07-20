"""job_status.py — 무인 잡 실행 상태 기록/조회.

각 잡(import/doc/weather/db/date)이 실행 끝에 parquet/_jobstatus/<job>.json 에
{job, ok, ts, summary, error} 를 원자적으로 기록한다. 운영 대시보드(app.py)가 이를 읽어
'마지막 실행 시각 / 성공·실패 / 요약'을 한 화면에 표시한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import path_config as pc


def _dir() -> Path:
    d = pc.get_parquet_dir() / "_jobstatus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_status(job: str, ok: bool, summary: str = "", error: str = "") -> None:
    """잡 상태 1건 기록 (원자적)."""
    payload = {
        "job": job,
        "ok": bool(ok),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "error": error,
    }
    p = _dir() / f"{job}.json"
    tmp = p.with_suffix(f".json.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def read_all() -> dict[str, dict]:
    """모든 잡 상태 조회 → {job: payload}."""
    out: dict[str, dict] = {}
    d = pc.get_parquet_dir() / "_jobstatus"
    if not d.exists():
        return out
    for f in d.glob("*.json"):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return out
