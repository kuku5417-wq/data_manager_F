"""parquet_io.py — 원자적 parquet 저장.

공유 폴더(F:\\code\\data\\parquet 또는 NAS)에 여러 앱이 동시에 읽기/쓰기 하므로,
쓰는 도중 부분 파일을 읽는 사고를 막기 위해 임시파일→os.replace(원자적 rename) 사용.

읽는 쪽은 항상 완전한 파일만 보게 된다.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pandas as pd


def _sanitize_mixed_object_cols(df: pd.DataFrame) -> pd.DataFrame:
    """pyarrow가 못 쓰는 혼합타입 object 컬럼만 문자열로 통일 (엑셀발 TYPE 등).

    object 컬럼의 비결측값에 **문자열과 비문자열이 동시에** 있으면 pyarrow가
    타입 추론에 실패한다("Expected bytes got a 'int' object"). 그런 컬럼만
    str 로 통일하고 결측(NaN/NA)은 보존한다. 동질 컬럼·순수 숫자혼합(int+float)은
    pyarrow가 처리하므로 건드리지 않아 기존 정상 저장은 무변경(회귀 없음).
    """
    bad_cols = []
    for col in df.columns:
        if df[col].dtype != object:
            continue
        non_null = df[col].dropna()
        if non_null.empty:
            continue
        has_str = False
        has_non_str = False
        for v in non_null:
            if isinstance(v, str):
                has_str = True
            else:
                has_non_str = True
            if has_str and has_non_str:
                bad_cols.append(col)
                break
    if not bad_cols:
        return df
    df = df.copy()
    for col in bad_cols:
        df[col] = df[col].map(lambda v: v if (v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA) else str(v))
    return df


def save_parquet_atomic(df: pd.DataFrame, path: str | Path) -> Path:
    """df 를 path 에 원자적으로 저장.

    같은 디렉토리에 .{uuid}.tmp 로 먼저 쓴 뒤 os.replace 로 교체한다.
    (os.replace 는 동일 볼륨에서 원자적. 임시파일을 같은 폴더에 둬서 보장.)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    df = _sanitize_mixed_object_cols(df)   # 혼합타입 object 컬럼(TYPE 등) → str (pyarrow 저장 가능)
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    finally:
        # 실패 시 임시파일 잔존 방지
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    # 저장 성공 시 저장된 parquet 을 읽어 운영서버(MSSQL) 순차 자동 전송
    # (엑셀 → parquet → MSSQL) — best-effort, 미설정/실패해도 저장은 유효
    try:
        from db import auto_sync
        auto_sync.push_saved(path)
    except Exception:
        pass
    return path
