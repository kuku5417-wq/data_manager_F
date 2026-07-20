"""db_connector.py — MySQL → parquet 생성기 (data_manager).

원본 참조: tbm_system_v6/modules/db_connector.py
역할: shipinfo→pjtlist, pjtevnt→milestone(raw wide, **unpivot 안 함**), shipbbs→shipbbs.
규칙: 호선 컬럼 ensure_sn(SN 부착), 원자적 저장, 한국어 datetime 보정.
폴백: DB 불가 시 기존 parquet (사내망 전용 — data2 더미 폴백 제거).

⚠️ tbm 원본과 달리 milestone unpivot(_unpivot_pjtevnt) 하지 않는다.
   (ESG는 wide 직접 소비, tbm은 read 시 자체 unpivot)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import path_config as pc
from settings import secrets
from sn_util import ensure_sn
from parquet_io import save_parquet_atomic


def _pq(key: str) -> Path:
    return pc.get_parquet_dir() / f"{key}.parquet"


# 개인정보 최소수집: SELECT * 로 들어온 운영 DB 테이블에서 인명/연락처류 컬럼이 있으면
# 저장 전 제거한다(개인정보보호법 제16조). 컬럼명(소문자) 부분일치로 판별.
_PII_COL_TOKENS = (
    "hname", "담당자", "작성자", "신청자", "작업자", "성명", "이름", "insertby",
    "phone", "연락처", "휴대", "핸드폰", "전화", "mobile", "tel", "hse_manage",
    "email", "메일", "주민", "생년", "주소",
)


def _drop_pii_cols(df: pd.DataFrame) -> pd.DataFrame:
    """SELECT * 유입분 중 미사용 개인정보 컬럼 제거(최소수집). 대상 없으면 원본 반환.

    milestone(pjtevnt)의 wide 이벤트 컬럼 등 개인정보가 아닌 컬럼은 건드리지 않는다.
    """
    drop = [c for c in df.columns
            if any(tok in str(c).strip().lower() for tok in _PII_COL_TOKENS)]
    return df.drop(columns=drop) if drop else df


def _get_engine():
    url = secrets.db_url
    if not url:
        return None
    try:
        from sqlalchemy import create_engine, text, pool
        engine = create_engine(url, poolclass=pool.QueuePool, pool_size=5,
                               max_overflow=10, pool_recycle=1800,
                               pool_pre_ping=True, echo=False)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        return None


def _fix_korean_datetime(df: pd.DataFrame) -> pd.DataFrame:
    """object 컬럼 중 '오전/오후' 패턴을 Timestamp 로 변환.

    컬럼 전체를 단일 포맷으로 coerce 하면 포맷이 다른 값이 무음으로 NaT 가 되므로,
    esg_converter 의 셀 단위 파서(_parse_datetime_col)로 값별 변환한다
    (오전/오후·AM/PM·Excel serial·일반 날짜 혼재 허용). 변환 후 NaT 가
    대량 발생하면 경고를 남기고 원본을 보존한다.
    """
    from esg_converter import _parse_datetime_col
    for col in df.columns:
        if df[col].dtype != object:
            continue
        sample = df[col].dropna().astype(str)
        if sample.empty:
            continue
        if sample.str.contains("오전|오후", na=False).any():
            before_nonnull = df[col].notna().sum()
            converted = _parse_datetime_col(df[col])
            lost = int(before_nonnull - converted.notna().sum())
            if before_nonnull and lost > before_nonnull * 0.5:
                print(f"[db_connector] 경고: '{col}' 날짜 변환에서 {lost}/{before_nonnull}건 "
                      f"NaT 발생 — 원본 유지", flush=True)
                continue
            if lost:
                print(f"[db_connector] '{col}' 날짜 변환: {lost}건 파싱 실패(NaT)", flush=True)
            df[col] = converted
    return df


def _fallback(key: str, dummy_name: str | None = None) -> pd.DataFrame:
    """DB 미연결/실패 시 기존 parquet 폴백 (사내 DB 장애에도 무중단).

    data_manager_F(사내망 전용): data2 더미 폴백 제거. dummy_name 인자는
    호출부 호환을 위해 남겨두되 사용하지 않는다.
    """
    p = _pq(key)
    if p.exists():
        try:
            return pd.read_parquet(p)
        except Exception:
            pass
    return pd.DataFrame()


# ══════════════════════════════════════════════════════
# pjtlist (shipinfo)
# ══════════════════════════════════════════════════════

def _apply_ethane_shiptype(df: pd.DataFrame) -> pd.DataFrame:
    """TYPEMODEL 이 'ETHANE' 으로 끝나면 SHIPTYPE='LPG' 로 지정(대소문자 무시).

    에탄운반선을 기존 선종 LPG 로 편입 → 새 선종 미추가, 소비측(esg) 변경 불필요.
    """
    if "TYPEMODEL" in df.columns and "SHIPTYPE" in df.columns:
        m = df["TYPEMODEL"].astype(str).str.strip().str.upper().str.endswith("ETHANE")
        df.loc[m, "SHIPTYPE"] = "LPG"
    return df


def gen_pjtlist() -> tuple[pd.DataFrame, str]:
    """shipinfo → pjtlist.parquet. SHIPNUM ensure_sn. 폴백 시 저장 안 함."""
    engine = _get_engine()
    if engine is not None:
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                df = pd.read_sql(text("SELECT * FROM shipinfo ORDER BY SHIPNUM"), conn)
            if not df.empty:
                df = _drop_pii_cols(df)          # 최소수집: 인명/연락처류 컬럼 제거
                df = _fix_korean_datetime(df)
                if "SHIPNUM" in df.columns:
                    df["SHIPNUM"] = df["SHIPNUM"].map(ensure_sn)
                df = _apply_ethane_shiptype(df)
                save_parquet_atomic(df, _pq("pjtlist"))
                return df, f"✅ shipinfo {len(df)}행 → pjtlist.parquet"
        except Exception as e:
            return _fallback("pjtlist", "shipinfo"), f"⚠️ DB 실패, 폴백: {e}"
    return _fallback("pjtlist", "shipinfo"), "⚠️ DB 미연결 → 폴백(기존/더미)"


# ══════════════════════════════════════════════════════
# milestone (pjtevnt) — raw wide, unpivot 안 함
# ══════════════════════════════════════════════════════

def gen_milestone() -> tuple[pd.DataFrame, str]:
    """pjtevnt → milestone.parquet (raw wide). PJT→project 리네임 + ensure_sn."""
    engine = _get_engine()
    if engine is not None:
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                df = pd.read_sql(text("SELECT * FROM pjtevnt"), conn)
            if not df.empty:
                df = _drop_pii_cols(df)          # 최소수집: 인명/연락처류 컬럼 제거(wide 이벤트 컬럼은 보존)
                df = _fix_korean_datetime(df)
                # 호선 컬럼 통일: PJT → project (ESG·tbm 소비 컬럼명)
                if "project" not in df.columns:
                    for src in ("PJT", "pjt", "SHIPNUM", "shipnum"):
                        if src in df.columns:
                            df = df.rename(columns={src: "project"})
                            break
                if "project" in df.columns:
                    df["project"] = df["project"].map(ensure_sn)
                save_parquet_atomic(df, _pq("milestone"))
                return df, f"✅ pjtevnt {len(df)}행 → milestone.parquet (wide)"
        except Exception as e:
            return _fallback("milestone", "pjtevnt"), f"⚠️ DB 실패, 폴백: {e}"
    return _fallback("milestone", "pjtevnt"), "⚠️ DB 미연결 → 폴백(기존/더미)"


# ══════════════════════════════════════════════════════
# shipbbs
# ══════════════════════════════════════════════════════

def gen_shipbbs() -> tuple[pd.DataFrame, str]:
    """shipbbs → shipbbs.parquet. project=ensure_sn(PJT)."""
    engine = _get_engine()
    if engine is not None:
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                # 최소수집: 작성자(INSERTBY)는 소비앱 어디에서도 사용 안 하므로 조회 제외
                df = pd.read_sql(text(
                    "SELECT PJT, KIND, REMARK, INSERTDATE FROM shipbbs "
                    "ORDER BY PJT"), conn)
            if not df.empty:
                df.columns = df.columns.str.lower()
                df = _fix_korean_datetime(df)
                if "pjt" in df.columns:
                    df["project"] = df["pjt"].map(ensure_sn)
                save_parquet_atomic(df, _pq("shipbbs"))
                return df, f"✅ shipbbs {len(df)}행 → shipbbs.parquet"
        except Exception as e:
            return _fallback("shipbbs"), f"⚠️ DB 실패, 폴백: {e}"
    return _fallback("shipbbs"), "⚠️ DB 미연결 → 폴백(기존)"


def gen_all() -> list[dict]:
    """3종 일괄 생성. UI에서 호출."""
    results = []
    for name, fn in [("pjtlist", gen_pjtlist), ("milestone", gen_milestone), ("shipbbs", gen_shipbbs)]:
        df, msg = fn()
        results.append({"name": name, "rows": len(df), "msg": msg})
    return results
