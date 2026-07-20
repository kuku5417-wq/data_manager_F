"""sn_util.py — 호선번호 SN 표기 통일 유틸.

정본 규칙: 모든 parquet 저장 시 호선 컬럼을 'SN2601' 형태로 수렴(SN 부착).

주의: 이 함수는 SN을 **부착**한다 (ESG modules/constants.normalize_sn 과 동일 동작).
      tbm_system_v6 의 _normalize_project / _normalize_hullno 는 SN을 **제거**하므로
      정반대다. 통합 후 tbm 측 함수는 ensure_sn 동작으로 교체한다.
"""
from __future__ import annotations

import pandas as pd


def ensure_sn(val) -> str:
    """SN2601, S2601, 2601 → 'SN2601' 로 통일 (SN 부착).

    빈값/NaN 은 '' 반환. 'SN' 또는 'S'+숫자 또는 순수숫자 패턴만 변환하고,
    그 외 형태는 원본을 그대로 둔다(예상치 못한 값 보존).
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip().upper()
    if not s or s in ("NAN", "NAT", "NONE"):
        return ""
    if s.startswith("SN"):
        return s
    if s.startswith("S") and s[1:].isdigit():
        return "SN" + s[1:]
    if s.isdigit():
        return "SN" + s
    return s


def ensure_sn_col(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """DataFrame 의 지정 호선 컬럼에 ensure_sn 일괄 적용 (in-place 아님, 복사본 반환)."""
    if df is None or df.empty or col not in df.columns:
        return df
    out = df.copy()
    out[col] = out[col].map(ensure_sn)
    return out
