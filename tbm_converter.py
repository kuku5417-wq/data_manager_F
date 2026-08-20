"""tbm_converter.py — 작업허가서 엑셀 → ptwlist.parquet (일별 확장 + 누적 병합)

STDATE~EDDATE 범위를 하루씩 DATE 행으로 확장.
예) 6/14 08:00 ~ 6/16 13:00 → DATE="26-06-14", "26-06-15", "26-06-16" 3행.
매일 신규 파일 업로드 시 기존 parquet과 병합, 동일 DATE+위치 항목은 신규로 교체.

인식 파일명은 PTW_FILE_GLOBS 하나로 관리한다 — 워처·수동 변환·진단이 같은 값을 쓴다.
"""
from __future__ import annotations

import fnmatch
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd

# 작업허가서 원본 파일 인식 패턴. 현장 내보내기 파일명이 "밀폐구역…" 이라 함께 잡는다.
# ptwlist 는 이름 어디에 있어도(날짜접두 20260819_ptwlist.xlsx 허용), 밀폐구역은 시작 기준.
# 리더가 win32 COM(Excel) 이라 구형 .xls 도 읽힌다.
PTW_FILE_GLOBS = ["*ptwlist*.xlsx", "*ptwlist*.xls", "밀폐구역*.xlsx", "밀폐구역*.xls"]


def is_ptw_file(name: str) -> bool:
    """파일명이 작업허가서 원본 패턴인지(대소문자 무시). '_' 접두(_processed 등)는 제외."""
    n = str(name).strip()
    if not n or n.startswith("_"):
        return False
    low = n.lower()
    return any(fnmatch.fnmatch(low, g.lower()) for g in PTW_FILE_GLOBS)

from sn_util import ensure_sn
from parquet_io import save_parquet_atomic
from datetime_util import is_blank, passthrough_dt

# 저장 대상 팀 (이 팀만 유지) — 저장 시점에 필터
TEAM_KEEP = "시운전팀"

# ── 표준 컬럼 키워드 매핑 ──────────────────────────────────────────────────────
# 개인정보 최소수집(개인정보보호법 제16조): 담당자/작업자 실명(HNAME)·HSE담당자(HSE_MANAGE)는
# 소비앱(tbm)·LLM 어디에서도 사용하지 않으므로 수집·저장하지 않는다. TBM/RA는 부서(DEPTNM/TEAMNM)
# 단위로만 다룬다. (재도입이 필요하면 마스킹·보유기간 정책과 함께 검토)
COL_CANDIDATES: dict[str, list[str]] = {
    "KYULGBN":    ["KYULGBN", "결재구분", "결재상태", "상태"],
    "PTW_AGBN":   ["PTW_AGBN", "등급", "AGBN"],
    "TEAMNM":     ["TEAMNM", "팀명", "팀"],
    "DEPTNM":     ["DEPTNM", "부서명", "부서"],
    "IOWKGBNNM":  ["IOWKGBNNM", "밀폐구분", "밀폐여부"],
    "WKGBNNM":    ["WKGBNNM", "작업구분명", "작업구분"],
    "STDATE":     ["STDATE", "시작일시", "시작일", "작업시작", "START"],
    "EDDATE":     ["EDDATE", "종료일시", "종료일", "작업종료", "END", "FINISH"],
    "PJT":        ["HULLNO", "HULL_NO", "호선번호", "호선", "PJT", "PROJECT"],
    "AREA_DETAIL":["AREA_DETAIL", "작업위치", "위치", "장소", "AREA"],
    "WORK_NM":    ["WORK_NM", "작업명", "작업내용", "작업상세"],
    "ACODENM":    ["ACODENM", "작업허가대상", "작업코드명", "허가대상"],
}

# DATE 포함 최종 컬럼
FINAL_COLS   = list(COL_CANDIDATES.keys()) + ["DATE"]
DEDUP_COLS   = ["DEPTNM", "PJT", "AREA_DETAIL", "ACODENM", "DATE"]
ARCHIVE_DAYS = 14   # 이 기간(일) 초과 데이터는 ptwlist_archive.parquet으로 분리


def _norm_col(name) -> str:
    """컬럼명 비교용 정규화 — 괄호 이후 절삭 + 공백·언더바·하이픈·줄바꿈 제거 후 대문자.

    현장 파일 헤더는 "호선(선박)" · "작업 시작일" · 줄바꿈 섞인 형태로 온다. 정확 일치로만
    비교하면 못 잡고, 못 잡은 표준 컬럼은 아래에서 전부 None 이 돼 **행이 통째로 사라진다**
    (STDATE 가 None 이면 _expand_to_daily 가 전 행을 건너뛴다).
    """
    t = str(name)
    for br in ("(", "（", "["):
        cut = t.find(br)
        if cut > 0:
            t = t[:cut]
    t = "".join(str(t).split())          # 공백/탭/개행 전부 제거
    return "".join(ch for ch in t.upper() if ch not in "_-./")


def _map_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Excel 컬럼명 → 표준 컬럼명. 매칭 안 되면 None.

    2단계로 찾는다 — ① 정규화 후 정확 일치, ② 남은 것만 부분 포함(`작업 시작일` ⊃ `시작일`).
    ②를 나중에 도는 이유는 정확 일치가 항상 이겨야 하기 때문이다(먼저 돌면 포함 매칭이
    다른 표준 컬럼의 정답을 가로챈다). ②는 3글자 이상 후보만 쓴다 — `팀`·`호선` 같은
    짧은 후보는 `팀장명` 처럼 엉뚱한 컬럼에 붙는다.

    못 찾은 표준 컬럼 목록을 df.attrs["unmapped"] 에 남긴다(변환 결과 메시지에 쓴다).
    """
    col_map: dict[str, str] = {}
    used: set[str] = set()
    norm_cols = {c: _norm_col(c) for c in df.columns}

    # ① 정확 일치
    rest: list[str] = []
    for std_col, cands in COL_CANDIDATES.items():
        hit = None
        for cand in cands:
            nc = _norm_col(cand)
            matches = [c for c, n in norm_cols.items() if n == nc and c not in used]
            if matches:
                hit = matches[0]
                break
        if hit is None:
            rest.append(std_col)
            continue
        col_map[hit] = std_col
        used.add(hit)

    # ② 부분 포함 — 가장 긴 후보가 걸린 컬럼을 택한다
    unmapped: list[str] = []
    for std_col in rest:
        cands = sorted((_norm_col(c) for c in COL_CANDIDATES[std_col]), key=len, reverse=True)
        hit = None
        for nc in cands:
            if len(nc) < 3:
                continue
            matches = [c for c, n in norm_cols.items() if nc in n and c not in used]
            if matches:
                hit = matches[0]
                break
        if hit is None:
            unmapped.append(std_col)
            continue
        col_map[hit] = std_col
        used.add(hit)

    result = df.rename(columns=col_map)
    for std_col in COL_CANDIDATES:
        if std_col not in result.columns:
            result[std_col] = None
    out = result[list(COL_CANDIDATES.keys())]
    out.attrs["unmapped"] = unmapped
    return out


def _parse_dt_col(series: pd.Series) -> pd.Series:
    """다양한 날짜 형식 파싱.

    우선순위:
      1. "26/05/06 08:00"  → %y/%m/%d %H:%M  (실제 파일 형식)
      2. pandas 자동 추론 (ISO 형식 등)
    """
    def _parse_one(val):
        if is_blank(val):
            return pd.NaT
        ts = passthrough_dt(val)
        if ts is not None:
            return ts
        s = str(val).strip()
        for fmt in ("%y/%m/%d %H:%M", "%y/%m/%d %H:%M:%S",
                    "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d %H:%M:%S"):
            try:
                return pd.Timestamp(datetime.strptime(s, fmt))
            except ValueError:
                pass
        return pd.to_datetime(s, errors="coerce")

    return series.map(_parse_one)


def _expand_to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """STDATE~EDDATE 범위를 DATE별 행으로 확장.

    DATE 컬럼 형식: "YY-MM-DD" (예: "26-06-14")
    STDATE가 NaT인 행은 제거.
    EDDATE가 NaT이거나 STDATE보다 이전이면 STDATE 날짜 하루만 생성.
    """
    rows = []
    for _, r in df.iterrows():
        s = r["STDATE"]
        e = r["EDDATE"]
        if pd.isna(s):
            continue
        start_d = s.date()
        end_d   = e.date() if pd.notna(e) else start_d
        if end_d < start_d:
            end_d = start_d
        cur = start_d
        while cur <= end_d:
            row = r.copy()
            row["DATE"] = cur.strftime("%y-%m-%d")
            rows.append(row)
            cur += timedelta(days=1)
    if not rows:
        return pd.DataFrame(columns=FINAL_COLS)
    return pd.DataFrame(rows).reset_index(drop=True)[FINAL_COLS]


def _stringify_cells(df: pd.DataFrame) -> pd.DataFrame:
    """win32 COM이 돌려준 native 값을 read_excel(dtype=str) 유사하게 정규화.
    datetime은 그대로(통과 파싱), 정수형 float은 '2701.0'→'2701'로, 그 외는 str."""
    def _one(v):
        if v is None:
            return None
        if hasattr(v, "year"):          # datetime → 그대로 (_parse_dt_col passthrough 처리)
            return v
        if isinstance(v, float):
            if pd.isna(v):
                return None
            return str(int(v)) if v.is_integer() else str(v)
        return str(v)
    for c in df.columns:
        df[c] = df[c].map(_one)
    return df


# win32(DRM) 마지막 실패 사유 — 호출부/진단이 진짜 원인을 표시하도록 보관
_DRM_LAST_ERR = ""


def read_drm_excel(path: Path | str) -> pd.DataFrame | None:
    """DRM 걸린 엑셀을 win32 Excel COM(DispatchEx)으로 열어 첫 시트 → DataFrame.

    로컬 Excel을 ReadOnly로 띄워 DRM을 해제 상태로 읽는다.
    pywin32 미설치·Excel 부재·사외망 등 실패 시 None 반환(실패 사유는 _DRM_LAST_ERR)
    → 호출부가 read_excel 폴백.
    """
    global _DRM_LAST_ERR
    _DRM_LAST_ERR = ""
    excel_app = None
    own_instance = False
    wb = None
    co_init = False
    try:
        import os
        import win32com.client as win32          # 지연 import (사외망/미설치 대비)
        try:
            import pythoncom                      # Streamlit 워커 스레드는 COM 초기화 선행 필수
            pythoncom.CoInitialize()
            co_init = True
        except Exception:
            pass
        try:
            excel_app = win32.DispatchEx("Excel.Application")
            own_instance = True                   # 우리가 새로 띄운 인스턴스만 Quit 대상
        except Exception:
            # 사용자가 열어둔 Excel 에 attach — Quit 시 편집 중 문서가 닫히므로 금지
            excel_app = win32.Dispatch("Excel.Application")
        excel_app.Visible = False
        excel_app.DisplayAlerts = False
        excel_app.AskToUpdateLinks = False
        abs_path = os.path.abspath(str(path))
        # UpdateLinks=0, ReadOnly=True 로 팝업 원천 차단
        wb = excel_app.Workbooks.Open(abs_path, UpdateLinks=0, ReadOnly=True)
        ws = wb.Sheets(1)
        raw = ws.UsedRange()
        values = raw.Value if hasattr(raw, "Value") else raw
        if not values:
            return pd.DataFrame()
        if not isinstance(values, (list, tuple)):
            values = [[values]]
        header = [str(h).strip() if h is not None else "" for h in values[0]]
        df = pd.DataFrame(list(values[1:]), columns=header)
        return _stringify_cells(df)
    except Exception as e:
        import logging
        _DRM_LAST_ERR = f"{type(e).__name__}: {e}"
        logging.warning("read_drm_excel 실패 → read_excel 폴백: %s", _DRM_LAST_ERR)
        return None
    finally:
        if wb is not None:
            try: wb.Close(SaveChanges=False)
            except Exception: pass
        if excel_app is not None and own_instance:
            try: excel_app.Quit()
            except Exception: pass
        if co_init:
            try:
                import pythoncom; pythoncom.CoUninitialize()
            except Exception: pass


def read_ptw_excel(src: bytes | Path | str) -> pd.DataFrame:
    """ptwlist 엑셀 읽기. 경로면 win32(DRM) 우선→read_excel 폴백, bytes면 read_excel."""
    if isinstance(src, (bytes, bytearray)):
        return pd.read_excel(BytesIO(bytes(src)), dtype=str, engine="openpyxl")
    df = read_drm_excel(src)
    if df is not None:
        return df
    # win32(DRM) 실패 → read_excel 폴백. DRM blob이면 여기서도 실패 → 진짜 원인을 합쳐 표시.
    try:
        return pd.read_excel(src, dtype=str, engine="openpyxl")
    except Exception as e:
        win = _DRM_LAST_ERR or "win32 미시도"
        raise RuntimeError(
            f"DRM 해제(win32) 실패: {win} · read_excel 실패: {type(e).__name__}: {e} "
            f"— 이 PC에 pywin32/Excel 설치 확인 필요(진단: run_diag.bat)"
        ) from e


def prune_uploads(ptw_dir: Path | str, keep: int = 7,
                  patterns: "tuple[str, ...] | list[str]" = tuple(PTW_FILE_GLOBS)) -> int:
    """upload 폴더의 지정 패턴 원본을 mtime 최신순 keep개만 남기고 삭제.

    여러 glob(patterns)을 합쳐 최신순 정렬 → 초과분 삭제. 기본값은 작업허가서 원본 전체.
    _backup/_processed 등 하위폴더는 glob 비재귀라 영향 없음. 삭제한 파일 수 반환.
    """
    ptw_dir = Path(ptw_dir)
    seen: set[Path] = set()
    files: list[Path] = []
    for pat in patterns:
        for p in ptw_dir.glob(pat):
            if p.is_file() and p not in seen:
                seen.add(p)
                files.append(p)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for p in files[keep:]:
        try:
            p.unlink()
            removed += 1
        except Exception:
            pass
    return removed


def _process_raw(raw_df: pd.DataFrame, filename: str, parquet_dir: Path) -> list[dict]:
    """읽은 raw DataFrame → 매핑·팀필터·SN·날짜파싱·일별확장·dedup 누적 저장.

    단일 ptwlist.parquet 에 누적(아카이브 분리 없음). Returns: 결과 dict 리스트.
    """
    if raw_df is None or raw_df.empty:
        return [{"name": filename, "ok": False, "rows": 0, "msg": "파일에 데이터 없음"}]

    results: list[dict] = []

    # 컬럼 매핑 → 팀 필터 → 호선 SN 수렴 → 날짜 파싱 → 일별 확장
    mapped   = _map_columns(raw_df)
    unmapped = list(mapped.attrs.get("unmapped", []))
    raw_rows = len(mapped)

    # 0행이 되는 경로가 둘(팀 필터·헤더 매핑 실패)인데 결과 메시지가 같아 원인을 알 수 없었다.
    # 어느 쪽에서 몇 행이 사라졌는지 메시지에 남긴다.
    team_note = ""
    if "TEAMNM" in mapped.columns:   # 시운전팀만 유지 (파일·LLM 비용 절감)
        keep    = mapped["TEAMNM"].astype(str).str.strip() == TEAM_KEEP
        dropped = int((~keep).sum())
        if dropped:
            found = (mapped.loc[~keep, "TEAMNM"].astype(str).str.strip()
                     .replace({"": "(빈값)", "None": "(빈값)", "nan": "(빈값)"})
                     .value_counts().head(5))
            names = " · ".join(f"{k}({v})" for k, v in found.items())
            team_note = f" → 팀 필터 제외 {dropped}행(발견된 팀: {names})"
        mapped = mapped[keep].reset_index(drop=True)
    mapped["PJT"] = mapped["PJT"].map(ensure_sn)        # HULLNO "2701" → "SN2701"
    mapped["STDATE"] = _parse_dt_col(mapped["STDATE"])
    mapped["EDDATE"] = _parse_dt_col(mapped["EDDATE"])
    new_df   = _expand_to_daily(mapped)
    src_rows = len(mapped)
    new_rows = len(new_df)

    msg = f"원본 {raw_rows}행{team_note} → DATE 확장 {new_rows}행"
    if unmapped:
        cols = " · ".join(str(c) for c in list(raw_df.columns)[:15])
        msg += f"\n매핑 실패 컬럼: {' · '.join(unmapped)}\n실제 컬럼: {cols}"
    if new_rows == 0 and not unmapped and not team_note:
        msg += "\n시작일시(STDATE)를 날짜로 읽지 못했을 수 있음 — 원본 날짜 형식 확인 필요"

    # rows 0 을 성공으로 보고하면 워처가 원본을 _processed 로 옮겨 재시도가 불가능해진다.
    results.append({"name": filename, "ok": new_rows > 0, "rows": new_rows, "msg": msg})

    out_path = parquet_dir / "ptwlist.parquet"
    try:
        # 기존 parquet 로드 후 누적 병합 (없으면 신규)
        if out_path.exists():
            existing = pd.read_parquet(out_path)
            before   = len(existing)
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            before   = 0
            combined = new_df
        # 중복 제거 (keep=last) — 첫 변환·단일 파일 내부 중복도 항상 제거
        _keys    = [c for c in DEDUP_COLS if c in combined.columns]
        combined = combined.drop_duplicates(subset=_keys, keep="last").reset_index(drop=True)
        replaced = (before + new_rows) - len(combined)   # 중복으로 제거/교체된 행 수

        # ── 위험요소 LLM 보강 (risk_keywords/warning) ───────────────────
        # ACODENM 키로 mapping 조회 + 미매핑 LLM. 실패해도 저장 진행.
        enrich_stats = {"unmapped": 0, "llm_ok": 0, "llm_fail": 0, "added": []}
        try:
            from ptw_enrich import enrich_ptwlist
            combined, enrich_stats = enrich_ptwlist(combined, use_llm=True)
        except Exception as _e:
            import logging
            logging.warning("ptw_enrich 실패 (저장 계속 진행): %s", _e)

        save_parquet_atomic(combined, out_path)
        total = len(combined)

        if enrich_stats["unmapped"]:
            fail_note = f"/{enrich_stats['llm_fail']}건 실패" if enrich_stats["llm_fail"] else ""
            err_note  = f" [원인: {enrich_stats['llm_err']}]" if enrich_stats.get("llm_err") else ""
            enrich_note = (f" · 위험요소: 미매핑 {enrich_stats['unmapped']}종 → "
                           f"LLM {enrich_stats['llm_ok']}건 추가{fail_note}{err_note}")
        else:
            enrich_note = ""
        msg = f"신규 {new_rows}행 추가, 중복 {replaced}건 교체 → 총 {total}행{enrich_note}"
        results.append({"name": "ptwlist", "ok": True, "rows": new_rows,
                        "total": total, "replaced": replaced,
                        "enrich": enrich_stats, "msg": msg})
    except Exception as e:
        results.append({"name": "ptwlist", "ok": False, "rows": 0, "msg": str(e)})

    return results


def convert_and_save(
    file_bytes: bytes,
    filename: str,
    parquet_dir: Path,
    backup_dir: Path,
) -> list[dict]:
    """ptwlist 엑셀(bytes) → 일별 확장 후 ptwlist.parquet 누적 저장 (UI/업로더 경로).

    backup_dir: 원본 Excel 사본(`{YYYYMMDD}_{파일명}`)을 남길 폴더.
      **반드시 `pc.get_backup_dir("ptw")` 를 넘긴다.** 업로드 폴더를 넘기면 사본이
      업로드 폴더에 쌓여 스캔·변환이 회차마다 느려진다(구 버그).
    Returns: [{"name": str, "ok": bool, "rows": int, "msg": str}, ...]
    """
    # 원본 백업
    try:
        (backup_dir / f"{datetime.now().strftime('%Y%m%d')}_{filename}").write_bytes(file_bytes)
    except Exception:
        pass

    try:
        raw_df = read_ptw_excel(bytes(file_bytes))
    except Exception as e:
        return [{"name": filename, "ok": False, "rows": 0, "msg": f"Excel 읽기 실패: {e}"}]

    return _process_raw(raw_df, filename, parquet_dir)


def convert_path(path: Path | str, parquet_dir: Path,
                 backup_dir: Path | None = None) -> list[dict]:
    """ptwlist 엑셀(경로) → win32(DRM) 우선 읽기 후 저장 (워처/DRM 경로).

    backup_dir 지정 시 읽기 전에 원본 사본을 `{YYYYMMDD}_{파일명}` 으로 남긴다.
    호출부가 변환 성공 후 원본을 `_processed/` 로 옮기므로 사본이 있어야 복구가 된다.
    복사는 read_bytes 재기록이 아니라 shutil.copy2 로 한다 — DRM 파일은 암호문 그대로
    보존해야 하고, 메타데이터(mtime)도 유지해야 백업 정리(prune) 순서가 맞는다.
    """
    path = Path(path)
    if backup_dir is not None:
        try:
            import shutil
            Path(backup_dir).mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, Path(backup_dir) / f"{datetime.now():%Y%m%d}_{path.name}")
        except Exception:   # noqa: BLE001 — 백업 실패로 변환을 막지 않는다
            pass
    try:
        raw_df = read_ptw_excel(path)
    except Exception as e:
        return [{"name": path.name, "ok": False, "rows": 0, "msg": f"Excel 읽기 실패: {e}"}]

    return _process_raw(raw_df, path.name, parquet_dir)
