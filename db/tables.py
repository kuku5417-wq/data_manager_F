"""db/tables.py — parquet명 ↔ MSSQL 테이블명 매핑.

규칙: MSSQL 테이블명 = "jsh_" + parquet stem (전부 소문자).
data_manager가 생산하는 테이블형 parquet 목록(단일 정의). 새 parquet은 목록에만 추가.
"""
from __future__ import annotations

from pathlib import Path

# data_manager 생산 테이블형 parquet (문서 PDF·sentinel 등 비테이블 제외)
TABLES = [
    "ptwlist", "out", "ra", "mapping", "message",
    "trial_schedule", "fuel_usage", "fuel_price", "lng_usage", "fuel_plan", "pjtmethod",
    "weather", "date", "milestone", "pjtlist", "shipbbs",
]


def table_of(name: str) -> str:
    """parquet 파일명/키 → MSSQL 테이블명 (jsh_<stem>, 소문자).

    TABLES 화이트리스트 외 키는 거부(심층 방어).
    """
    stem = Path(str(name)).stem.lower()
    if stem not in TABLES:
        raise KeyError(f"허용되지 않은 테이블 키: {name!r} (TABLES 목록에 추가 필요)")
    return "jsh_" + stem
