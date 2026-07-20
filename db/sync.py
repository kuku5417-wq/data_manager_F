"""db/sync.py — parquet → MSSQL(jsh_*) 전송.

생산자(data_manager) 단일이므로 **테이블 전체 교체**(if_exists="replace") = idempotent.
운영서버 미설정(is_configured=False) 시 아무것도 하지 않음(휴면).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import connection, tables


def push_table(name: str, df: pd.DataFrame, engine=None) -> dict:
    """단일 테이블 push (전체 교체). engine 재사용 가능."""
    tbl = tables.table_of(name)
    try:
        eng = engine or connection.get_engine()
        df.to_sql(tbl, eng, if_exists="replace", index=False)
        return {"table": tbl, "rows": len(df), "ok": True, "msg": ""}
    except Exception as e:
        return {"table": tbl, "rows": 0, "ok": False, "msg": f"{type(e).__name__}: {e}"}


def sync_all(parquet_dir: Path) -> list[dict]:
    """parquet_dir 의 TABLES parquet 전부 → MSSQL 전체 교체. 미설정이면 빈 결과."""
    if not connection.is_configured():
        return [{"table": "-", "rows": 0, "ok": False, "msg": "운영서버 미설정 — .env DB_* 입력 필요"}]
    try:
        eng = connection.get_engine()
    except Exception as e:
        return [{"table": "-", "rows": 0, "ok": False, "msg": f"엔진 생성 실패: {type(e).__name__}: {e}"}]
    out: list[dict] = []
    for name in tables.TABLES:
        p = Path(parquet_dir) / f"{name}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            out.append({"table": tables.table_of(name), "rows": 0, "ok": False, "msg": f"읽기 실패: {e}"})
            continue
        out.append(push_table(name, df, eng))
    return out
