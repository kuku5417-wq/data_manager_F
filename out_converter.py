"""out_converter.py — outside_*.xlsx → out.parquet + ra.parquet

tbm_system_v6/modules/out_processor.py 로직을 독립 모듈로 재구성.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd

from sn_util import ensure_sn
from parquet_io import save_parquet_atomic
from datetime_util import is_blank

# ── 상수 ──────────────────────────────────────────────────────────────────────
RA_MIN_DAYS   = 2
RA_MAX_DAYS   = 7
RA_KEEP_YEARS = 3

COMMISSIONING_DEPTS = [
    "시운전1부", "시운전2부", "시운전3부",
    "안벽의장", "시운전팀", "해운부", "해운1과", "해운2과",
    "친환경실증", "시운전과", "시운전기술", "LNG설비운영",
]
OUT_DEPT_FILTER = [
    "시운전팀", "시운전", "기장운전", "전장운전", "선장운전",
    "CSU", "친환경실증", "해운", "해운1과", "해운2과",
    "시운전기술", "LNG설비운영",
]

# Out 업로드 파일 인식 패턴 (둘 중 하나로 시작하면 인식). 새 이름 규칙 추가 시 여기에.
OUT_FILE_GLOBS = ["outside_*.xlsx", "사외작업자 출입관리 정보_*.xlsx"]

OUT_COL_MAP = {
    "이름":    "name",   "연락처":  "phone",    "회사명":   "company",
    "방문시작": "visit_start", "방문종료": "visit_end", "호선":    "project",
    "PJT":    "project",
    "업무내용": "work_content", "접견자": "greeter",  "방문부서": "dept",
}

RA_COLS = [
    "name", "phone", "company", "visit_start", "visit_end", "project",
    "work_content", "greeter", "dept", "period_start", "period_end",
    "is_commissioning", "ra_done", "ra_file",
    # 소비앱(tbm) 기록 상태 — 재생성 시 복원/보존
    "excluded", "exclude_reason", "greeter_actual", "greeter_actual_dept", "manual",
]

# 재생성 시 소비앱 상태 복원 대상(카드키=RA_CARD_KEYS 매칭)
RA_STATE_COLS = ["ra_done", "ra_file", "excluded", "exclude_reason",
                 "greeter_actual", "greeter_actual_dept"]


def _parse_date_flex(val) -> "date | None":
    if is_blank(val):
        return None
    s = str(val).strip()
    if s.isdigit() and len(s) == 8:
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    ts = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def _is_commissioning(dept: str) -> bool:
    return any(kw in str(dept) for kw in COMMISSIONING_DEPTS)


def _split_periods(visit_start: date, visit_end: date) -> list[tuple[date, date]]:
    periods = []
    cur = visit_start
    while cur <= visit_end:
        chunk_end = min(cur + timedelta(days=RA_MAX_DAYS - 1), visit_end)
        if (chunk_end - cur).days + 1 >= RA_MIN_DAYS:
            periods.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return periods if periods else [(visit_start, visit_end)]


def read_out_source_bytes(src: bytes | Path | str) -> bytes:
    """outside 엑셀 소스 → bytes. 경로면 win32(DRM) 복호 우선, 실패 시 원본 bytes 폴백.

    bytes(업로더)는 그대로(이미 평문). 경로(폴더 선택/스캔)는 DRM일 수 있어 win32로 평문 복원.
    """
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    import drm_reader
    decrypted = drm_reader.drm_to_xlsx_bytes(src)
    if decrypted is not None:
        return decrypted
    return Path(src).read_bytes()   # win32 미가용/실패 → 원본 그대로(평문이면 정상 동작)


def _load_single_out(file_bytes: bytes) -> pd.DataFrame:
    """단일 Excel 바이트 → out DataFrame (컬럼 매핑 후)"""
    try:
        df = pd.read_excel(BytesIO(file_bytes), dtype=str, engine="openpyxl")
    except Exception:
        df = pd.read_excel(BytesIO(file_bytes), dtype=str)
    df = df.rename(columns={k: v for k, v in OUT_COL_MAP.items() if k in df.columns})
    for col in ("visit_start", "visit_end"):
        if col in df.columns:
            df[col] = df[col].apply(_parse_date_flex)
    return df


def derive_ra(out_df: pd.DataFrame) -> pd.DataFrame:
    if out_df.empty:
        return pd.DataFrame(columns=RA_COLS)

    def _str(val) -> str:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return ""
        return str(val).strip()

    cutoff = date.today() - timedelta(days=RA_KEEP_YEARS * 365)
    rows = []
    for _, r in out_df.iterrows():
        dept = str(r.get("dept", ""))
        vs, ve = r.get("visit_start"), r.get("visit_end")
        if pd.isna(vs) or pd.isna(ve):
            continue
        if isinstance(vs, str):
            vs = _parse_date_flex(vs)
        if isinstance(ve, str):
            ve = _parse_date_flex(ve)
        if vs is None or ve is None or vs < cutoff:
            continue
        for ps, pe in _split_periods(vs, ve):
            rows.append({
                "name":             _str(r.get("name")),
                "phone":            _str(r.get("phone")),
                "company":          _str(r.get("company")),
                "visit_start":      str(vs),
                "visit_end":        str(ve),
                "project":          _str(r.get("project")),
                "work_content":     _str(r.get("work_content")),
                "greeter":          _str(r.get("greeter")),
                "dept":             dept,
                "period_start":     str(ps),
                "period_end":       str(pe),
                "is_commissioning": _is_commissioning(dept),
                "ra_done":          "N",
                "ra_file":          "",
                "excluded":         "N",
                "exclude_reason":   "",
                "greeter_actual":      "",
                "greeter_actual_dept": "",
                "manual":              "N",
            })

    if not rows:
        return pd.DataFrame(columns=RA_COLS)

    df = pd.DataFrame(rows)
    GROUP_KEYS = ["company", "period_start", "period_end", "work_content", "dept"]

    def _join_unique(s):
        return ", ".join(str(v) for v in dict.fromkeys(v for v in s if v and str(v) not in ("", "nan")))

    agg = (
        df.groupby(GROUP_KEYS, as_index=False, sort=False)
        .agg(
            name             = ("name",             _join_unique),
            phone            = ("phone",            _join_unique),
            visit_start      = ("visit_start",      "first"),
            visit_end        = ("visit_end",        "last"),
            project          = ("project",          "first"),
            greeter          = ("greeter",          "first"),
            is_commissioning = ("is_commissioning", "any"),
            ra_done          = ("ra_done",          "first"),
            ra_file          = ("ra_file",          "first"),
            excluded         = ("excluded",         "first"),
            exclude_reason   = ("exclude_reason",   "first"),
            greeter_actual   = ("greeter_actual",   "first"),
            greeter_actual_dept = ("greeter_actual_dept", "first"),
            manual           = ("manual",           "first"),
        )
    )
    return agg[RA_COLS]


_RA_KCOLS = ["company", "period_start", "period_end", "work_content", "dept"]


def _restore_ra_state(ra_df: pd.DataFrame, existing_ra: pd.DataFrame | None) -> pd.DataFrame:
    """재생성 시 기존 ra.parquet의 소비앱 상태(RA_STATE_COLS)를 카드키로 복원 + 수기(manual) 행 보존.

    - 상태 복원: 이름 집합이 바뀌어도 실적/제외/재배정 유지(안정키=RA_KCOLS).
    - 수기 보존: manual=="Y" 행 중 재생성 결과에 없는 카드키는 그대로 유지(신청 없는 수기 항목 안 사라짐).
    """
    if existing_ra is None or existing_ra.empty or ra_df.empty:
        return ra_df
    if not all(c in existing_ra.columns for c in _RA_KCOLS):
        return ra_df
    try:
        state_cols = [c for c in RA_STATE_COLS if c in existing_ra.columns]
        smap = (existing_ra.drop_duplicates(subset=_RA_KCOLS, keep="last")
                .set_index(_RA_KCOLS)[state_cols].to_dict("index"))
        for idx, row in ra_df.iterrows():
            key = tuple(row[c] for c in _RA_KCOLS)
            if key in smap:
                for c in state_cols:
                    ra_df.at[idx, c] = smap[key][c]
        # 수기 행 보존
        if "manual" in existing_ra.columns:
            manual = existing_ra[existing_ra["manual"].astype(str) == "Y"]
            if not manual.empty:
                present = {tuple(r[c] for c in _RA_KCOLS) for _, r in ra_df.iterrows()}
                keep_rows = [r for _, r in manual.iterrows()
                             if tuple(r[c] for c in _RA_KCOLS) not in present]
                if keep_rows:
                    keep = pd.DataFrame(keep_rows)
                    for c in RA_COLS:
                        if c not in keep.columns:
                            keep[c] = False if c == "is_commissioning" else (
                                "N" if c in ("ra_done", "excluded", "manual") else "")
                    ra_df = pd.concat([ra_df, keep[RA_COLS]], ignore_index=True)
    except Exception:
        pass
    return ra_df


def convert_and_save(
    files_bytes: list[tuple[str, bytes]],   # [(filename, bytes), ...]
    parquet_dir: Path,
    backup_dir: Path,
    existing_ra_path: Path | None = None,
) -> list[dict]:
    """outside_*.xlsx 복수 파일 → out.parquet + ra.parquet 저장.

    backup_dir: 원본 Excel 사본(`{YYYYMMDD}_{파일명}`)을 남길 폴더.
      **반드시 `pc.get_backup_dir("out")` 를 넘긴다.** 업로드 폴더를 넘기면 사본이
      업로드 폴더에 무한 누적된다(구 버그 — 글롭 불일치라 정리도 안 됐다).
    Returns: [{"name": str, "ok": bool, "rows": int, "cols": list, "msg": str}, ...]
    """
    from datetime import datetime as dt
    results = []
    today = dt.now().strftime("%Y%m%d")
    frames = []

    for fname, fbytes in files_bytes:
        try:
            (backup_dir / f"{today}_{fname}").write_bytes(fbytes)
            df = _load_single_out(fbytes)
            # 방문부서 필터
            if "dept" in df.columns:
                pat = "|".join(OUT_DEPT_FILTER)
                df = df[df["dept"].fillna("").str.contains(pat, na=False)].reset_index(drop=True)
            frames.append(df)
            results.append({"name": fname, "ok": True, "rows": len(df), "cols": list(df.columns), "msg": ""})
        except Exception as e:
            results.append({"name": fname, "ok": False, "rows": 0, "cols": [], "msg": str(e)})

    if not frames:
        results.append({"name": "out", "ok": False, "rows": 0, "cols": [], "msg": "처리된 파일 없음"})
        return results

    out_df = pd.concat(frames, ignore_index=True)
    # 누적 병합: 기존 out.parquet를 포함해 dedup → 폴더에 최신 파일만 있어도 과거 보존.
    # (구 동작은 입력 파일들만으로 전체 재생성 = 일부만 있으면 과거 유실)
    out_path = parquet_dir / "out.parquet"
    if out_path.exists():
        try:
            out_df = pd.concat([pd.read_parquet(out_path), out_df], ignore_index=True)
        except Exception:
            pass
    dedup = [c for c in ["name", "company", "visit_start", "visit_end", "work_content"] if c in out_df.columns]
    out_df = out_df.drop_duplicates(subset=dedup, keep="last").reset_index(drop=True)
    # 호선번호 SN 수렴
    if "project" in out_df.columns:
        out_df["project"] = out_df["project"].map(ensure_sn)

    # out.parquet 저장
    try:
        save_parquet_atomic(out_df, out_path)
        results.append({"name": "out", "ok": True, "rows": len(out_df), "cols": list(out_df.columns), "msg": ""})
    except Exception as e:
        results.append({"name": "out", "ok": False, "rows": 0, "cols": [], "msg": str(e)})
        return results

    # ra.parquet 파생
    ra_df = derive_ra(out_df)

    # 기존 소비앱 상태 복원 + 수기행 보존 (안정키=카드키)
    if existing_ra_path and existing_ra_path.exists():
        try:
            ra_df = _restore_ra_state(ra_df, pd.read_parquet(existing_ra_path))
        except Exception:
            pass

    try:
        ra_path = parquet_dir / "ra.parquet"
        if "project" in ra_df.columns:
            ra_df["project"] = ra_df["project"].map(ensure_sn)
        save_parquet_atomic(ra_df, ra_path)
        results.append({"name": "ra", "ok": True, "rows": len(ra_df), "cols": list(ra_df.columns), "msg": ""})
    except Exception as e:
        results.append({"name": "ra", "ok": False, "rows": 0, "cols": [], "msg": str(e)})

    return results


def regenerate_ra(parquet_dir: Path) -> dict:
    """현재 out.parquet에서 ra.parquet 파생·저장 (업로드 없이). ra_done/ra_file 상태 복원.

    out.parquet은 사용자가 직접 생성·배치하고, ra.parquet만 data_manager가 파생할 때 사용.
    Returns: {"ok": bool, "rows": int, "msg": str}
    """
    out_path = parquet_dir / "out.parquet"
    ra_path  = parquet_dir / "ra.parquet"
    if not out_path.exists():
        return {"ok": False, "rows": 0, "msg": "out.parquet 없음 — 먼저 배치하세요."}
    try:
        out_df = pd.read_parquet(out_path)
    except Exception as e:
        return {"ok": False, "rows": 0, "msg": f"out.parquet 읽기 실패: {e}"}
    if "project" in out_df.columns:
        out_df["project"] = out_df["project"].map(ensure_sn)

    ra_df = derive_ra(out_df)

    # 기존 소비앱 상태 복원 + 수기행 보존 (안정키=카드키)
    if ra_path.exists():
        try:
            ra_df = _restore_ra_state(ra_df, pd.read_parquet(ra_path))
        except Exception:
            pass

    if "project" in ra_df.columns:
        ra_df["project"] = ra_df["project"].map(ensure_sn)
    try:
        save_parquet_atomic(ra_df, ra_path)
        return {"ok": True, "rows": len(ra_df), "msg": f"ra.parquet {len(ra_df)}행 파생 완료"}
    except Exception as e:
        return {"ok": False, "rows": 0, "msg": f"ra.parquet 저장 실패: {e}"}
