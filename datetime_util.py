"""datetime_util.py — 컨버터 공통 날짜 파싱 프리미티브.

세 컨버터(esg/tbm/out)의 날짜 파서는 입력 형식이 서로 달라 본체는 분리 유지한다:
  - esg : Excel serial + "AM/PM" + "오전/오후" → Timestamp
  - tbm : "%y/%m/%d %H:%M" 등 포맷 목록 → Timestamp
  - out : "YYYYMMDD" 8자리 + 일반 → date

이 형식별 로직은 의도적으로 각 컨버터에 둔다(한 곳을 고치면 세 파이프라인이 같이
영향받는 결합을 피함). 다만 세 파서가 공통으로 복붙하던 '빈값 판정'과
'이미 datetime인 값 통과' 보일러플레이트만 여기로 모아 중복을 제거한다.
파싱 동작 자체는 변경 없음.
"""
from __future__ import annotations

import pandas as pd

_BLANK_TOKENS = ("", "nan", "nat", "none")


def is_blank(val) -> bool:
    """None/NaN(float) 및 ''/'nan'/'nat'/'none'(대소문자무관) → True."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    return str(val).strip().lower() in _BLANK_TOKENS


def passthrough_dt(val):
    """이미 날짜/시간 객체면 pd.Timestamp 로, 아니면 None."""
    return pd.Timestamp(val) if hasattr(val, "year") else None
