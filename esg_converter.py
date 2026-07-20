"""esg_converter.py — ESG 엑셀 6개 시트 → parquet 변환

esg/batch/convert_excel.py의 변환 함수를 그대로 내장하여
별도 sys.path 조작 없이 독립 실행 가능.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd

from sn_util import ensure_sn
from parquet_io import save_parquet_atomic
from datetime_util import is_blank, passthrough_dt

logger = logging.getLogger(__name__)

# ── datetime 파싱 ──────────────────────────────────────────────────────────────

_AMPM_TAIL_RE = re.compile(
    r"(\d{4}[-/]\d{2}[-/]\d{2})\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+(AM|PM)",
    re.IGNORECASE,
)
_AMPM_KR_MID_RE = re.compile(
    r"(\d{4}[-/]\d{2}[-/]\d{2})\s+(오전|오후)\s+(\d{1,2}:\d{2}(?::\d{2})?)",
    re.IGNORECASE,
)
_EXCEL_EPOCH = pd.Timestamp("1899-12-30")


def _parse_single_dt(val) -> pd.Timestamp:
    if is_blank(val):
        return pd.NaT
    ts = passthrough_dt(val)
    if ts is not None:
        return ts
    if isinstance(val, (int, float)):
        try:
            f = float(val)
            if 1000 < f < 200000:
                return _EXCEL_EPOCH + pd.Timedelta(days=f)
        except Exception:
            pass
        return pd.NaT
    s = str(val).strip()
    m = _AMPM_TAIL_RE.match(s)
    if m:
        d, t, mer = m.groups()
        t_full = t if ":" in t[3:] else t + ":00"
        d = d.replace("/", "-")
        return pd.to_datetime(f"{d} {t_full} {mer.upper()}",
                              format="%Y-%m-%d %I:%M:%S %p", errors="coerce")
    m2 = _AMPM_KR_MID_RE.match(s)
    if m2:
        d, kr, t = m2.groups()
        mer = "AM" if "오전" in kr else "PM"
        t_full = t if ":" in t[3:] else t + ":00"
        d = d.replace("/", "-")
        return pd.to_datetime(f"{d} {t_full} {mer}",
                              format="%Y-%m-%d %I:%M:%S %p", errors="coerce")
    return pd.to_datetime(s, errors="coerce")


def _parse_datetime_col(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    return series.map(_parse_single_dt)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = []
    for col in df.columns:
        s = str(col).strip().replace("\n", " ").replace("\r", " ")
        if "(" in s:
            s = s[:s.index("(")]
        s = s.strip().replace("/", "").replace(" ", "_").strip("_")
        cleaned.append(s if s else str(col))
    seen: dict[str, int] = {}
    new_cols: list[str] = []
    for col in cleaned:
        if col in seen:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 1
            new_cols.append(col)
    df.columns = new_cols
    return df


def _find_sheet(xl: pd.ExcelFile, candidates: list[str], index: int | None = None) -> str:
    for sheet in xl.sheet_names:
        if sheet in candidates or sheet.strip() in candidates:
            return sheet
    keywords = []
    for c in candidates:
        kw = c.split(".")[-1].strip() if "." in c else c.strip()
        if kw:
            keywords.append(kw)
    for sheet in xl.sheet_names:
        sheet_clean = sheet.strip()
        for kw in keywords:
            if kw in sheet_clean or sheet_clean in kw:
                return sheet
    if index is not None and index < len(xl.sheet_names):
        logger.warning("시트명 탐색 실패, 인덱스 %d 로 폴백: '%s'", index, xl.sheet_names[index])
        return xl.sheet_names[index]
    raise ValueError(
        f"시트를 찾을 수 없습니다.\n  후보: {candidates}\n  실제: {xl.sheet_names}"
    )


def _find_sheet_optional(xl: pd.ExcelFile, candidates: list[str]) -> str | None:
    try:
        return _find_sheet(xl, candidates)
    except ValueError:
        return None


# ── 시트별 변환 함수 ──────────────────────────────────────────────────────────

def convert_trial_schedule(xl: pd.ExcelFile) -> pd.DataFrame:
    sheet = _find_sheet(xl, ["0.시운전일정", "시운전일정"], index=0)
    df = xl.parse(sheet, dtype=object)
    df = _normalize_columns(df)
    for col in ("출항", "복귀"):
        if col in df.columns:
            df[col] = _parse_datetime_col(df[col])
    return df


def convert_fuel_usage(xl: pd.ExcelFile) -> pd.DataFrame:
    sheet = _find_sheet(xl, ["1.사용량", "사용량"], index=1)
    df = xl.parse(sheet, dtype=object)
    df = _normalize_columns(df)
    for col in ("Start", "Finish"):
        if col in df.columns:
            df[col] = _parse_datetime_col(df[col])
    for col in ["HFO", "LS_HFO", "LS_MGO", "RMA10", "LDO", "메탄올", "LNG", "LPG"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(3)
    return df


def convert_fuel_price(xl: pd.ExcelFile) -> pd.DataFrame:
    sheet = _find_sheet(xl, ["2.단가", "2.유류단가", "단가", "유류단가"], index=2)
    df = xl.parse(sheet, dtype=object)
    df = _normalize_columns(df)
    for col in ["HFO", "LS_HFO", "LS_MGO", "RMA10", "LDO", "메탄올"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(3)
    return df


def convert_lng_usage(xl: pd.ExcelFile) -> pd.DataFrame:
    sheet = _find_sheet(xl, ["3.LNG", "LNG"], index=3)
    df = xl.parse(sheet, dtype=object)
    df = _normalize_columns(df)
    for col in ("비용", "양", "단가"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("Loading", "Unloading"):
        if col in df.columns:
            df[col] = _parse_datetime_col(df[col])
    return df


_PLAN_COL_RENAME  = {"PJT": "호선", "MGO": "LS_MGO"}
_PLAN_FLOAT_COLS  = ["LNG", "HFO", "LS_HFO", "RMA10", "LS_MGO", "LDO", "메탄올", "LPG"]
_PLAN_COL_ORDER   = ["PJT", "인도년", "인도월",
                     "LNG", "HFO", "LS_HFO", "RMA10", "LS_MGO", "LDO", "메탄올", "LPG"]
_PLAN_COL_ORDER   = [unicodedata.normalize("NFC", c) for c in _PLAN_COL_ORDER]
_PLAN_SKIP_RE     = re.compile(r"^(합계|소계|계|total|sum)", re.IGNORECASE)


def convert_fuel_plan(xl: pd.ExcelFile) -> pd.DataFrame:
    """4.연간계획 시트 → fuel_plan.parquet (PJT 컬럼으로 통일)"""
    sheet = _find_sheet(xl, ["4.연간계획", "연간계획", "시운전유류", "유류계획"])
    df_probe = xl.parse(sheet, header=None, dtype=object, nrows=10)
    header_row = 0
    for i, row in df_probe.iterrows():
        vals = [str(v).strip().upper() for v in row.dropna().values]
        if "PJT" in vals or "호선" in vals:
            header_row = int(i)
            break
    df = xl.parse(sheet, header=header_row, dtype=object)
    df = _normalize_columns(df)
    # PJT 또는 호선 컬럼 → 통일: 모두 PJT로
    if "호선" in df.columns and "PJT" not in df.columns:
        df = df.rename(columns={"호선": "PJT"})
    df = df.rename(columns={"MGO": "LS_MGO"})
    if "PJT" not in df.columns:
        raise ValueError(f"'PJT'(또는 '호선') 컬럼 없음. 실제: {list(df.columns)}")
    df = df.dropna(subset=["PJT"]).copy()
    df["PJT"] = df["PJT"].astype(str).str.strip()
    df = df[~df["PJT"].str.match(_PLAN_SKIP_RE, na=False)]
    df = df[df["PJT"] != ""]
    if "인도년" in df.columns:
        df["인도년"] = pd.to_numeric(df["인도년"], errors="coerce").fillna(0).astype(int)
    else:
        df["인도년"] = 0
    df = df[df["인도년"] >= 2000].copy()
    if "인도월" in df.columns:
        df["인도월"] = (
            df["인도월"].astype(str).str.replace("월", "", regex=False).str.strip()
        )
        df["인도월"] = pd.to_numeric(df["인도월"], errors="coerce").fillna(0).astype(int)
    else:
        df["인도월"] = 0
    for col in _PLAN_FLOAT_COLS:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0.0)
    df.columns = [unicodedata.normalize("NFC", c) for c in df.columns]
    return df.reindex(columns=_PLAN_COL_ORDER, fill_value=0)


def convert_pjtmethod(xl: pd.ExcelFile) -> pd.DataFrame:
    sheet = _find_sheet(xl, ["5.공법", "공법", "List", "list"])
    df = xl.parse(sheet, dtype=object)
    df = _normalize_columns(df)
    # 구버전 parquet/더미 데이터에서 잘못 저장된 컬럼명 보정
    _COL_FIX = {"부": "역", "부두1": "선적"}
    df = df.rename(columns={k: v for k, v in _COL_FIX.items() if k in df.columns})
    _DROP = {"No", "시리즈", "OWNER", "TYPE", "선종"}
    _KEEP_ORDER = ["PJT", "공법", "통합", "역", "선적", "하역", "Speed", "SG"]
    if "PJT" not in df.columns:
        raise ValueError(f"PJT 컬럼 없음. 실제: {list(df.columns)}")
    df = df.drop(columns=[c for c in _DROP if c in df.columns])
    df = df.dropna(subset=["PJT"]).copy()
    df["PJT"] = df["PJT"].astype(str).str.strip()
    for col in _KEEP_ORDER[1:]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().replace("nan", pd.NA)
    return df[[c for c in _KEEP_ORDER if c in df.columns]]


# ── 변환 + 저장 ────────────────────────────────────────────────────────────────

SHEET_CONVERTERS = {
    "trial_schedule": convert_trial_schedule,
    "fuel_usage":     convert_fuel_usage,
    "fuel_price":     convert_fuel_price,
    "lng_usage":      convert_lng_usage,
    "pjtmethod":      convert_pjtmethod,
}


def _pjt_merge(existing: pd.DataFrame, new_df: pd.DataFrame,
               pjt_col: str = "PJT") -> tuple[pd.DataFrame, int, int]:
    """기존 parquet에서 새 업로드의 PJT 행을 제거하고 신규 데이터 추가 (PJT 단위 교체).

    Returns: (merged_df, kept_rows, replaced_pjt_count)
    """
    if existing.empty:
        return new_df.reset_index(drop=True), 0, 0
    # 컬럼명 정규화 (호선 → PJT 하위호환)
    if "호선" in existing.columns and pjt_col not in existing.columns:
        existing = existing.rename(columns={"호선": pjt_col})
    if pjt_col not in existing.columns or pjt_col not in new_df.columns:
        merged = pd.concat([existing, new_df], ignore_index=True)
        return merged, len(existing), 0
    new_pjts  = set(new_df[pjt_col].astype(str).unique())
    kept      = existing[~existing[pjt_col].astype(str).isin(new_pjts)]
    replaced  = len(existing) - len(kept)
    merged    = pd.concat([kept, new_df], ignore_index=True).reset_index(drop=True)
    return merged, len(kept), replaced


def convert_and_save(
    file_bytes: bytes,
    filename: str,
    parquet_dir: Path,
    upload_dir: Path,
) -> list[dict]:
    """엑셀 바이트 → 6개 parquet 변환 후 저장 (PJT 단위 병합).

    업로드 Excel에 있는 PJT만 교체, 없는 PJT는 기존 parquet에서 유지.
    Returns: [{"name": str, "ok": bool, "rows": int, "cols": list, "msg": str}, ...]
    """
    results = []

    # 원본 Excel 백업
    today = datetime.now().strftime("%Y%m%d")
    backup_path = upload_dir / f"{today}_{filename}"
    try:
        backup_path.write_bytes(file_bytes)
    except Exception as e:
        logger.warning("원본 백업 실패: %s", e)

    with pd.ExcelFile(BytesIO(file_bytes)) as xl:
        # 기본 5개 시트 — PJT 단위 병합
        for key, converter in SHEET_CONVERTERS.items():
            try:
                new_df    = converter(xl)
                if "PJT" in new_df.columns:
                    new_df["PJT"] = new_df["PJT"].map(ensure_sn)
                out_path  = parquet_dir / f"{key}.parquet"
                new_count = len(new_df)
                if out_path.exists() and not new_df.empty:
                    existing              = pd.read_parquet(out_path)
                    merged, kept, replaced = _pjt_merge(existing, new_df)
                    note = f"PJT {len(new_df['PJT'].unique()) if 'PJT' in new_df.columns else '?'}개 교체, 기존 {kept}행 유지 → 전체 {len(merged)}행"
                else:
                    merged, note = new_df, f"{new_count}행 신규 저장"
                save_parquet_atomic(merged, out_path)
                results.append({
                    "name": key, "ok": True,
                    "rows": len(merged), "cols": list(merged.columns), "msg": note,
                })
            except Exception as e:
                results.append({
                    "name": key, "ok": False,
                    "rows": 0, "cols": [], "msg": str(e),
                })

        # 연간계획 — PJT 단위 병합
        if _find_sheet_optional(xl, ["4.연간계획", "연간계획", "시운전유류", "유류계획"]):
            try:
                new_plan  = convert_fuel_plan(xl)
                if "PJT" in new_plan.columns:
                    new_plan["PJT"] = new_plan["PJT"].map(ensure_sn)
                plan_path = parquet_dir / "fuel_plan.parquet"
                new_count = len(new_plan)
                if plan_path.exists() and not new_plan.empty:
                    existing              = pd.read_parquet(plan_path)
                    merged, kept, replaced = _pjt_merge(existing, new_plan, pjt_col="PJT")
                    note = f"PJT {len(new_plan['PJT'].unique()) if 'PJT' in new_plan.columns else '?'}개 교체, 기존 {kept}행 유지 → 전체 {len(merged)}행"
                else:
                    merged, note = new_plan, f"{new_count}행 신규 저장"
                save_parquet_atomic(merged, plan_path)
                results.append({
                    "name": "fuel_plan", "ok": True,
                    "rows": len(merged), "cols": list(merged.columns), "msg": note,
                })
            except Exception as e:
                results.append({
                    "name": "fuel_plan", "ok": False,
                    "rows": 0, "cols": [], "msg": str(e),
                })
        else:
            results.append({
                "name": "fuel_plan", "ok": False,
                "rows": 0, "cols": [], "msg": "4.연간계획 시트 없음 (건너뜀)",
            })

    return results


def read_esg_source_bytes(src: bytes | Path | str) -> bytes:
    """ESG 엑셀 소스 → bytes. 경로면 win32(DRM) 복호 우선, 실패 시 원본 bytes 폴백.

    bytes(업로더)는 그대로(이미 평문). 경로(폴더 선택/워처)는 DRM일 수 있어 win32로 평문 복원.
    """
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    import drm_reader
    decrypted = drm_reader.drm_to_xlsx_bytes(src)
    if decrypted is not None:
        return decrypted
    return Path(src).read_bytes()   # win32 미가용/실패 → 원본 그대로(평문이면 정상 동작)


def convert_path(path: Path | str, parquet_dir: Path, upload_dir: Path) -> list[dict]:
    """ESG 엑셀(경로) → win32(DRM) 복호 후 6개 parquet 저장 (폴더 변환/DRM 경로).

    tbm_converter.convert_path 미러. 읽기 실패 시 결과 리스트로 사유 반환.
    """
    path = Path(path)
    try:
        file_bytes = read_esg_source_bytes(path)
    except Exception as e:
        return [{"name": path.name, "ok": False, "rows": 0, "cols": [],
                 "msg": f"Excel 읽기 실패: {e}"}]
    return convert_and_save(file_bytes, path.name, parquet_dir, upload_dir)
