"""date_manager.py — 날짜 테이블 생성 (data_manager 이식본).

원본: tbm_system_v6/modules/date_manager.py
변경: 경로를 path_config로, 저장은 원자적.
의존: holidays 라이브러리.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import path_config as pc
from parquet_io import save_parquet_atomic


def _date_path() -> Path:
    return pc.get_parquet_dir() / "date.parquet"


def generate_date_table(
    start: date | None = None,
    end: date | None = None,
    company_holidays: list[date] | None = None,
) -> pd.DataFrame:
    """날짜 테이블 생성 (기본 2025-01-01 ~ 오늘+7일)."""
    import holidays

    today = date.today()
    start = start or date(2025, 1, 1)
    end   = end   or (today + timedelta(days=7))
    company_holidays = company_holidays or []

    kr_holidays = holidays.KR(years=range(start.year, end.year + 1))

    rows = []
    current = start
    while current <= end:
        is_weekend   = current.weekday() >= 5
        is_holiday   = current in kr_holidays
        holiday_name = kr_holidays.get(current, "")
        is_company   = current in company_holidays
        is_business  = not (is_weekend or is_holiday or is_company)
        rows.append({
            "date":               current,
            "year":               current.year,
            "month":              current.month,
            "day":                current.day,
            "weekday":            current.weekday(),
            "weekday_name_kor":   "월화수목금토일"[current.weekday()],
            "quarter":            (current.month - 1) // 3 + 1,
            "week_of_year":       current.isocalendar()[1],
            "is_weekend":         is_weekend,
            "is_holiday":         is_holiday,
            "holiday_name":       holiday_name,
            "is_company_holiday": is_company,
            "is_business_day":    is_business,
        })
        current += timedelta(days=1)
    return pd.DataFrame(rows)


def save_date_parquet(df: pd.DataFrame) -> Path:
    return save_parquet_atomic(df, _date_path())


def load_date_parquet() -> pd.DataFrame | None:
    path = _date_path()
    return pd.read_parquet(path) if path.exists() else None
