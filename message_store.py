"""message_store.py — 팀장 안전메시지 message.parquet CRUD.

스키마(신): date, team, content, ref_type, ref_path
  - tbm TBM 스크립트(views/tbm/script_view.py:_sec7_message)가 소비하는 컬럼과 일치.
  - 구 스키마(id/author/message/active)는 load 시 자동 매핑(아래 _to_new_schema):
      message→content, team="전체", ref_type="없음", ref_path="". 첫 저장 시 신스키마로 영속.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import path_config as pc
from parquet_io import save_parquet_atomic

COLUMNS = ["date", "team", "content", "ref_type", "ref_path"]


def _path() -> Path:
    return pc.get_parquet_dir() / "message.parquet"


def _to_new_schema(df: pd.DataFrame) -> pd.DataFrame:
    """구/혼합 스키마를 신 스키마로 정규화."""
    df = df.copy()
    # 구 스키마 message → content
    if "content" not in df.columns and "message" in df.columns:
        df = df.rename(columns={"message": "content"})
    # 누락 컬럼 보강
    if "team" not in df.columns:
        df["team"] = "전체"
    if "ref_type" not in df.columns:
        df["ref_type"] = "없음"
    if "ref_path" not in df.columns:
        df["ref_path"] = ""
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    # 결측/빈값 기본치
    df["team"] = df["team"].fillna("전체").replace("", "전체")
    df["ref_type"] = df["ref_type"].fillna("없음").replace("", "없음")
    df["ref_path"] = df["ref_path"].fillna("")
    df["content"] = df["content"].fillna("")
    df["date"] = df["date"].fillna("").astype(str)
    return df[COLUMNS]


def load_messages() -> pd.DataFrame:
    p = _path()
    if not p.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_parquet(p)
    except Exception:
        return pd.DataFrame(columns=COLUMNS)
    return _to_new_schema(df)


def save_messages(df: pd.DataFrame) -> Path:
    """편집된 message DataFrame을 신 스키마로 원자적 저장."""
    return save_parquet_atomic(_to_new_schema(df.copy()), _path())


def migrate() -> Path | None:
    """기존 message.parquet를 신 스키마로 1회 변환·영속(디스크 갱신).

    tbm 스크립트가 raw parquet를 직접 읽으므로, UI 저장 전이라도 디스크 스키마를
    맞추고 싶을 때 호출. 파일이 없으면 None.
    """
    p = _path()
    if not p.exists():
        return None
    return save_messages(load_messages())
