"""데이터 업로드 관리 앱 — 포트 8510 (산업형 UI, 사내망 전용)

실행: streamlit run app.py --server.port 8510
"""
from __future__ import annotations

import os
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

import path_config as pc
import esg_converter
import tbm_converter
import out_converter
from sn_util import ensure_sn
from parquet_io import save_parquet_atomic
from ui_styles import inject_css
from ui_components import SHEET_LABELS, ESG_KEYS, analyze_diff, esc_html as _esc
from pii import mask_df_for_display
import catalog_view as cv   # 재디자인 UI (좌측 카테고리 내비 + 통합 카탈로그 + 상세)
import table_actions as ta   # 테이블 단위 변환 레지스트리·실행
# 폴더 스캔 헬퍼 정본은 table_actions (카탈로그 테이블 단위 변환과 공용)
from table_actions import list_dir_files as _list_dir_files, scan_folder as _scan_folder

st.set_page_config(
    page_title="Data Manager",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_css()

# ── 상수 ──────────────────────────────────────────────────────────────────────
TABS = ["ESG", "TBM", "Out", "SQL 대체", "날씨", "문서", "매핑", "메시지", "DB·날짜"]

# 탭 → upload/ 하위 섹션 폴더 매핑
TAB_SECTION = {"ESG": "esg", "TBM": "ptw", "Out": "out"}

# 상단 메뉴는 2개로 간소화: 운영 현황(자동 파이프라인 모니터링+수동 fallback) / 안전메시지(직접 입력)
# "데이터 변환" 화면의 변환 섹션 — 문서는 별도 화면, 메시지도 별도 화면이라 제외
# TBM(ptwlist)은 워처(ptw_watch_job) 무인 생성 + 수동 fallback 버튼. Out(out/ra)은 외부 생성이라 제외.
DASH_SECTIONS = ["ESG", "TBM", "Out", "SQL 대체", "날씨", "매핑", "DB·날짜"]

if not st.session_state.get("_dirs_ready"):
    pc.ensure_upload_dirs()  # upload/esg, upload/ptw, upload/out 생성
    st.session_state["_dirs_ready"] = True


@st.cache_data(show_spinner=False)
def _read_parquet_cached(path_str: str, mtime: float) -> pd.DataFrame:
    """parquet 읽기 캐시. mtime을 키로 사용해 저장 시 자동 무효화."""
    return pd.read_parquet(path_str)


def load_parquet(path: Path) -> pd.DataFrame:
    """파일 mtime 기반 캐시 읽기. 파일 없으면 빈 DataFrame."""
    try:
        mt = path.stat().st_mtime
    except OSError:
        return pd.DataFrame()
    return _read_parquet_cached(str(path), mt)


# ── parquet 소스 레지스트리 (입력 폴더 지정 + 사용자 추가 parquet 기억) ──────────
def _sources_path() -> Path:
    return pc.get_parquet_dir() / "_parquet_sources.json"


def load_sources() -> dict:
    import json
    p = _sources_path()
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            d.setdefault("folders", {}); d.setdefault("custom", [])
            return d
    except Exception:
        pass
    return {"folders": {}, "custom": []}


def save_sources(d: dict) -> None:
    import json
    try:
        _sources_path().write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


ESG_SHEET_CANDIDATES: dict[str, list[str]] = {
    "trial_schedule": ["0.시운전일정", "시운전일정"],
    "fuel_usage":     ["1.사용량", "사용량"],
    "fuel_price":     ["2.단가", "2.유류단가", "단가", "유류단가"],
    "lng_usage":      ["3.LNG", "LNG"],
    "fuel_plan":      ["4.연간계획", "연간계획", "시운전유류", "유류계획"],
    "pjtmethod":      ["5.공법", "공법", "List", "list"],
}

# ── session_state ──────────────────────────────────────────────────────────────
_SS: dict = {
    "folder":        {t: "" for t in TABS},
    "scanned":       {t: [] for t in TABS},
    "scan_diag":     {t: [] for t in TABS},
    "preview":       {t: {} for t in TABS},
    "selected_card": {t: None for t in TABS},
    "checked":       {t: {} for t in TABS},
    "save_results":  {t: {} for t in TABS},
    "simulate":      {t: False for t in TABS},
}
for _k, _v in _SS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── 상단 네비/라디오 라우터 제거 — 하단 cv.render_app() 이 좌측 카테고리 내비 +
#    통합 카탈로그 + 상세를 그린다. 화면별 렌더 함수는 handlers 로 재사용된다.


# ══════════════════════════════════════════════════════════════════════════════
# 파싱 함수
# ══════════════════════════════════════════════════════════════════════════════

def _esg_parse(file_bytes: bytes) -> dict:
    result: dict = {
        "_raw_cols": {}, "_raw_preview": {},
        "_normalized_cols": {}, "_sheet_errors": {}, "_error": None,
    }
    try:
        with pd.ExcelFile(BytesIO(file_bytes)) as xl:
            for key in ESG_KEYS:
                cands = ESG_SHEET_CANDIDATES[key]
                try:
                    sheet = esg_converter._find_sheet(xl, cands)
                    probe = xl.parse(sheet, nrows=5, dtype=object)
                    result["_raw_cols"][key]    = list(probe.columns)
                    result["_raw_preview"][key] = probe
                except Exception:
                    result["_raw_cols"][key]    = []
                    result["_raw_preview"][key] = pd.DataFrame()

                conv = esg_converter.SHEET_CONVERTERS.get(key)
                if conv is None:
                    opt = esg_converter._find_sheet_optional(xl, cands)
                    if opt:
                        try:
                            df = esg_converter.convert_fuel_plan(xl)
                            result[key] = df
                            result["_normalized_cols"][key] = list(df.columns)
                            result["_sheet_errors"][key]    = None
                        except Exception as e:
                            result[key] = pd.DataFrame()
                            result["_normalized_cols"][key] = []
                            result["_sheet_errors"][key]    = str(e)
                    else:
                        result[key] = pd.DataFrame()
                        result["_normalized_cols"][key] = []
                        result["_sheet_errors"][key]    = "시트 없음 (선택적)"
                else:
                    try:
                        df = conv(xl)
                        result[key] = df
                        result["_normalized_cols"][key] = list(df.columns)
                        result["_sheet_errors"][key]    = None
                    except Exception as e:
                        result[key] = pd.DataFrame()
                        result["_normalized_cols"][key] = []
                        result["_sheet_errors"][key]    = str(e)
    except Exception as e:
        result["_error"] = str(e)
    # 호선번호 SN 수렴 — 저장과 동일하게 미리보기/신호등도 SN 기준
    for k in ESG_KEYS:
        df = result.get(k)
        if isinstance(df, pd.DataFrame) and "PJT" in df.columns:
            df["PJT"] = df["PJT"].map(ensure_sn)
    return result


def _tbm_parse(src) -> dict:
    """src: bytes(업로더) 또는 파일 경로(폴더선택 — win32/DRM 우선). 미리보기 파싱."""
    result: dict = {
        "ptwlist": pd.DataFrame(), "_raw_df": pd.DataFrame(),
        "_col_map": {}, "_src_rows": 0, "_expanded_rows": 0, "_error": None,
    }
    try:
        raw_df = tbm_converter.read_ptw_excel(src)   # 경로면 win32(DRM)→read_excel 폴백
        result["_raw_df"] = raw_df

        col_map: dict[str, str | None] = {}
        used: set[str] = set()
        for std_col, cands in tbm_converter.COL_CANDIDATES.items():
            for cand in cands:
                matches = [c for c in raw_df.columns
                           if str(c).strip().upper() == cand.upper() and c not in used]
                if matches:
                    col_map[std_col] = matches[0]
                    used.add(matches[0])
                    break
            else:
                col_map[std_col] = None
        result["_col_map"] = col_map

        mapped = tbm_converter._map_columns(raw_df)
        # 저장과 동일: 팀 필터(시운전팀) + 호선 SN 수렴
        if "TEAMNM" in mapped.columns:
            mapped = mapped[mapped["TEAMNM"].astype(str).str.strip() == tbm_converter.TEAM_KEEP].reset_index(drop=True)
        mapped["PJT"] = mapped["PJT"].map(ensure_sn)
        mapped["STDATE"] = tbm_converter._parse_dt_col(mapped["STDATE"])
        mapped["EDDATE"] = tbm_converter._parse_dt_col(mapped["EDDATE"])
        expanded = tbm_converter._expand_to_daily(mapped)

        result["ptwlist"]        = expanded
        result["_src_rows"]      = len(raw_df)
        result["_expanded_rows"] = len(expanded)
    except Exception as e:
        result["_error"] = str(e)
    return result


def _out_parse(pairs: list[tuple[str, bytes]]) -> dict:
    result: dict = {
        "out": pd.DataFrame(), "ra": pd.DataFrame(),
        "_raw_cols_list": [], "_pairs": pairs, "_error": None,
    }
    try:
        frames = []
        for _fname, fbytes in pairs:
            df = out_converter._load_single_out(fbytes)
            result["_raw_cols_list"].append(list(df.columns))
            if "dept" in df.columns:
                pat = "|".join(out_converter.OUT_DEPT_FILTER)
                df = df[df["dept"].fillna("").str.contains(pat, na=False)].reset_index(drop=True)
            frames.append(df)
        if not frames:
            result["_error"] = "처리된 파일 없음"
            return result
        out_df = pd.concat(frames, ignore_index=True)
        dedup  = [c for c in ["name", "company", "visit_start", "visit_end", "work_content"]
                  if c in out_df.columns]
        if dedup:
            out_df = out_df.drop_duplicates(subset=dedup, keep="last").reset_index(drop=True)
        if "project" in out_df.columns:
            out_df["project"] = out_df["project"].map(ensure_sn)
        result["out"] = out_df
        result["ra"]  = out_converter.derive_ra(out_df)
    except Exception as e:
        result["_error"] = str(e)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 저장 함수
# ══════════════════════════════════════════════════════════════════════════════

def _esg_save(parsed: dict, keys_to_save: set[str], simulate: bool = False) -> dict[str, dict]:
    """선택한 ESG 시트만 저장. 병합·저장·백업은 esg_converter 단일본을 쓴다.

    시트 선택은 이 화면만의 기능이라 convert_and_save(6종 일괄)를 그대로 쓸 수 없다.
    대신 그 내부 구현(merge_and_save/backup_source)을 공유해 로직이 갈라지지 않게 한다.
    """
    results: dict[str, dict] = {}
    out_dir = pc.get_parquet_dir()
    for key in keys_to_save:
        new_df = parsed.get(key, pd.DataFrame())
        if new_df is None or new_df.empty:
            err = (parsed.get("_sheet_errors") or {}).get(key, "")
            results[key] = {"ok": False, "msg": err or "데이터 없음"}
            continue
        if simulate:      # 저장 없이 병합 결과만 예고
            results[key] = {"ok": True, "simulated": True, "rows": len(new_df),
                            "msg": f"[시뮬] {len(new_df)}행 병합 예정"}
            continue
        r = esg_converter.merge_and_save(key, new_df, out_dir)
        results[key] = {"ok": r["ok"], "msg": r["msg"], "rows": r["rows"], "simulated": False}
    if not simulate and results:
        fbytes = parsed.get("_bytes")
        if fbytes:        # 원본 Excel 백업 (upload/esg/_backup)
            esg_converter.backup_source(fbytes, parsed.get("_filename", "esg.xlsx"),
                                        pc.get_backup_dir("esg"))
        _prune_upload("esg")
        pc.touch_sentinel()
    return results


def _move_processed(fp: Path, section: str) -> str:
    """변환을 마친 원본을 upload/<section>/_processed 로 이동하고 이동한 파일명을 돌려준다.

    업로드 폴더에 미처리 파일만 남게 해서 다음 스캔·변환이 같은 파일을 다시 읽지 않게 한다
    (ptw 는 파일 1개당 DRM Excel COM 을 새로 띄우므로 재변환 비용이 크다).
    구현은 pc.archive_processed 단일본 — 이동 실패는 변환 결과에 영향을 주지 않는다.
    """
    return pc.archive_processed(fp, section)


def _prune_upload(section: str, keep: int = 7) -> None:
    """upload/<section> 원본·_backup 사본·_processed 보관분을 각각 최신 keep개만 유지.

    데이터 정본은 parquet 이므로 원본 Excel 은 최근 것만 보관한다.
    (_processed 를 정리하지 않으면 이동분이 무한히 쌓인다.)
    """
    pats = ("*.xlsx", "*.xls", "*.csv")
    try:
        n1 = tbm_converter.prune_uploads(pc.get_upload_dir(section), keep, patterns=pats)
        n2 = tbm_converter.prune_uploads(pc.get_backup_dir(section), keep, patterns=pats)
        n3 = tbm_converter.prune_uploads(pc.get_processed_dir(section), keep, patterns=pats)
        if n1 or n2 or n3:
            print(f"[upload 정리] {section}: 원본 {n1}개·백업 {n2}개·처리완료 {n3}개 "
                  f"삭제(각 최신 {keep}개 유지)", flush=True)
    except Exception:
        pass


def _tbm_save(simulate: bool = False) -> dict[str, dict]:
    """preview의 _path(폴더선택, win32/DRM) 또는 _bytes(업로더)로 저장."""
    p = st.session_state["preview"].get("TBM", {})
    if simulate:
        return {"ptwlist": {"ok": True, "simulated": True,
            "msg": f"[시뮬] DATE 확장 {p.get('_expanded_rows',0)}행 저장 예정 (dedup 누적)"}}
    pq = pc.get_parquet_dir()
    path = p.get("_path")
    if path:
        # 대화형 단건 저장 — 백업 사본만 남기고 원본은 그대로 둔다(재저장 가능).
        # 폴더에 쌓인 원본은 폴더 전체 변환 버튼·워처가 _processed 로 옮긴다.
        results_list = tbm_converter.convert_path(Path(path), pq, pc.get_backup_dir("ptw"))
    else:
        results_list = tbm_converter.convert_and_save(
            p.get("_bytes") or b"", p.get("_filename", "ptwlist.xlsx"),
            pq, pc.get_backup_dir("ptw"),
        )
    _prune_upload("ptw")
    return {r["name"]: r for r in results_list}


def _out_save(pairs: list[tuple[str, bytes]], simulate: bool = False) -> dict[str, dict]:
    if simulate:
        p = st.session_state["preview"].get("Out", {})
        return {
            "out": {"ok": True, "simulated": True, "msg": f"[시뮬] {len(p.get('out', pd.DataFrame()))}행"},
            "ra":  {"ok": True, "simulated": True, "msg": f"[시뮬] {len(p.get('ra',  pd.DataFrame()))}행"},
        }
    existing_ra = pc.get_parquet_dir() / "ra.parquet"
    results_list = out_converter.convert_and_save(
        pairs, pc.get_parquet_dir(), pc.get_backup_dir("out"),
        existing_ra if existing_ra.exists() else None,
    )
    _prune_upload("out")
    return {r["name"]: r for r in results_list}


# ══════════════════════════════════════════════════════════════════════════════
# UI 공통 헬퍼
# ══════════════════════════════════════════════════════════════════════════════

def _scan_diagnostics(folder_path: str, pattern) -> tuple[list[str], list[str]]:
    """스캔 각 단계를 진단 문자열로 반환 (cmd/UI 공용). (lines, raw_names)."""
    import fnmatch
    lines: list[str] = [f"경로: {folder_path}"]
    try:
        lines.append(f"os.path.isdir: {os.path.isdir(folder_path)}")
    except Exception as e:
        lines.append(f"isdir 예외: {type(e).__name__}: {e}")
    try:
        raw = os.listdir(folder_path)
        shown = raw[:30]
        lines.append(f"os.listdir: {len(raw)}개" + (f" → {shown}" if shown else " (빈 폴더)"))
    except Exception as e:
        raw = []
        lines.append(f"os.listdir 예외: {type(e).__name__}: {e}")
    pats = [pattern] if isinstance(pattern, str) else list(pattern)
    matched = [n for n in raw
               if not n.startswith("_")
               and any(fnmatch.fnmatch(n.lower(), p.lower()) for p in pats)]
    lines.append(f"패턴 {pats} 매칭: {len(matched)}개" + (f" → {matched[:30]}" if matched else ""))
    return lines, matched


def _handle_parse(tab: str, files: list, from_uploader: bool = False) -> None:
    with st.spinner("파싱 중..."):
        if tab == "ESG":
            # 폴더 선택(경로)은 win32(DRM) 복호 후 읽기 — 업로더는 평문 bytes 그대로
            raw  = files[0].read() if from_uploader else esg_converter.read_esg_source_bytes(files[0])
            name = files[0].name
            parsed = _esg_parse(raw)
            parsed["_filename"] = name
            parsed["_bytes"]    = raw
            st.session_state["preview"]["ESG"] = parsed
        elif tab == "TBM":
            name = files[0].name
            if from_uploader:
                raw = files[0].read()
                parsed = _tbm_parse(raw)          # bytes 경로(DRM 미지원 — 업로더는 해제본 가정)
                parsed["_bytes"] = raw
                parsed["_path"]  = None
            else:
                path = files[0]                   # Path (폴더 선택)
                parsed = _tbm_parse(path)         # win32/DRM 우선
                parsed["_bytes"] = None
                parsed["_path"]  = str(path)
            parsed["_filename"] = name
            st.session_state["preview"]["TBM"] = parsed
        elif tab == "Out":
            # 폴더 선택(경로)은 win32(DRM) 복호 후 읽기 — 업로더는 평문 bytes 그대로
            pairs = [(f.name, f.read() if from_uploader else out_converter.read_out_source_bytes(f))
                     for f in files]
            st.session_state["preview"]["Out"] = _out_parse(pairs)
        st.session_state["save_results"][tab]  = {}
        st.session_state["selected_card"][tab] = None
        st.session_state["checked"][tab]       = {}
    st.rerun()


def _render_source_bar(tab: str) -> None:
    """소스 바 — 폴더스캔 + 파일선택 + 업로드 3행으로 압축."""
    _cfg = {
        "ESG":     {"pattern": "*.xlsx",         "hint": "6개 시트 포함 xlsx", "multi": False},
        "TBM":     {"pattern": tbm_converter.PTW_FILE_GLOBS,
                    "hint": "밀폐구역*.xlsx / ptwlist*.xlsx (xls 도 가능, 날짜접두 허용)", "multi": False},
        "Out":     {"pattern": out_converter.OUT_FILE_GLOBS, "hint": "outside_*.xlsx 또는 사외작업자 출입관리 정보_*.xlsx", "multi": True},
        "SQL 대체": {"pattern": "*.parquet",      "hint": "parquet",              "multi": True},
    }
    cfg = _cfg[tab]

    # ── 행 1: 폴더경로 | 스캔 | 시뮬 ────────────────────────────────
    _default_folder = st.session_state["folder"].get(tab, "")
    if not _default_folder and tab in TAB_SECTION:
        _default_folder = str(pc.get_upload_dir(TAB_SECTION[tab]))

    c1, c2, c3 = st.columns([6, 1, 1])
    with c1:
        folder = st.text_input(
            "폴더", value=_default_folder,
            placeholder=f"폴더 경로 — {cfg['hint']}",
            label_visibility="collapsed", key=f"folder_input_{tab}",
        )
        st.session_state["folder"][tab] = folder
    with c2:
        if st.button("📂 스캔", key=f"scan_{tab}", use_container_width=True):
            diag, _matched = _scan_diagnostics(folder, cfg["pattern"])
            for ln in diag:                                  # cmd 창(streamlit run)에 출력
                print(f"[scan:{tab}] {ln}", flush=True)
            found = _scan_folder(folder, cfg["pattern"])
            st.session_state["scanned"][tab] = found
            st.session_state["scan_diag"][tab] = diag
            st.toast(f"{len(found)}개 파일" if found else "파일 없음",
                     icon="📂" if found else "⚠️")
    with c3:
        sim = st.checkbox("시뮬", value=st.session_state["simulate"].get(tab, False),
                          key=f"sim_{tab}", help="저장 없이 미리보기")
        st.session_state["simulate"][tab] = sim

    # ── 스캔 진단 (스캔 후 표시 — 0개일 때 자동 펼침) ────────────────
    found_files = st.session_state["scanned"].get(tab, [])
    _diag = st.session_state["scan_diag"].get(tab, [])
    if _diag:
        with st.expander("🔍 스캔 진단 (경로·파일목록·패턴매칭)", expanded=not found_files):
            st.code("\n".join(_diag), language="text")
            st.caption("같은 내용이 streamlit 실행 cmd 창에도 `[scan:...]` 으로 출력됩니다.")

    # ── 행 2: 파일선택 | 파싱 (스캔 결과 있을 때만) ──────────────────
    if found_files:
        c_sel, c_parse = st.columns([6, 1])
        with c_sel:
            if cfg["multi"]:
                sel = st.multiselect(
                    "파일", found_files, default=found_files[:3],
                    format_func=lambda p: p.name,
                    key=f"sel_multi_{tab}", label_visibility="collapsed",
                )
            else:
                sel_one = st.selectbox(
                    "파일", found_files,
                    format_func=lambda p: p.name,
                    key=f"sel_one_{tab}", label_visibility="collapsed",
                )
                sel = [sel_one] if sel_one else []
        with c_parse:
            if sel and st.button("파싱", key=f"parse_{tab}", use_container_width=True):
                _handle_parse(tab, sel, from_uploader=False)

    # ── 행 3: 직접 업로드 (compact) ──────────────────────────────────
    # TBM(작업허가서)은 폴더 스캔이 .xls 도 받는다(PTW_FILE_GLOBS) — 업로드도 맞춰 준다.
    if tab == "SQL 대체":
        ftype = ["parquet"]
    elif tab == "TBM":
        ftype = ["xlsx", "xls"]
    else:
        ftype = ["xlsx"]
    uploaded = st.file_uploader(
        "또는 직접 업로드", type=ftype, accept_multiple_files=cfg["multi"],
        key=f"uploader_{tab}",
    )
    if tab in ("ESG", "TBM") and uploaded is not None:
        if uploaded.name != st.session_state.get(f"_up_name_{tab}"):
            st.session_state[f"_up_name_{tab}"] = uploaded.name
            _handle_parse(tab, [uploaded], from_uploader=True)
    elif tab == "Out" and uploaded:
        new_names = sorted(f.name for f in uploaded)
        if new_names != st.session_state.get(f"_up_name_{tab}", []):
            st.session_state[f"_up_name_{tab}"] = new_names
            _handle_parse(tab, list(uploaded), from_uploader=True)


def _card_status(key: str, new_df: pd.DataFrame, save_results: dict) -> str:
    sr = save_results.get(key, {})
    if sr.get("simulated"):    return "simulated"
    if sr.get("ok") is True:   return "saved"
    if sr.get("ok") is False:  return "error"
    if new_df.empty:           return "empty"
    return "pending"


def _render_card(tab: str, key: str, diff: dict, status: str,
                  is_selected: bool) -> tuple[bool, bool]:
    """카드 + 저장 버튼 1열 배치. Returns (clicked, save_clicked)."""
    label      = SHEET_LABELS.get(key, key)
    new_rows   = diff.get("new_rows", 0)
    exist_rows = diff.get("exist_rows", 0)
    added      = diff.get("added", 0)
    changed    = diff.get("changed", 0)
    kept       = diff.get("kept", 0)
    sel_class  = "selected" if is_selected else ""
    icon_map   = {"pending":"🕐","saved":"✅","error":"❌","empty":"—","simulated":"🔍"}
    icon       = icon_map.get(status, "—")

    badges = ""
    if added:   badges += f'<span class="badge badge-green">🟢 +{added}</span>'
    if changed: badges += f'<span class="badge badge-amber">🟡 {changed}</span>'
    if kept:    badges += f'<span class="badge badge-gray">⚪ {kept}</span>'
    if status == "saved":     badges += '<span class="badge badge-saved">✅ 저장됨</span>'
    if status == "simulated": badges += '<span class="badge badge-gray">🔍 시뮬</span>'
    if status == "error":     badges += '<span class="badge badge-error">❌ 오류</span>'
    if not badges and status == "empty":
        badges = '<span class="badge badge-gray">— 데이터 없음</span>'

    exist_txt = f" · 기존 {exist_rows:,}행" if exist_rows else ""
    sel_extra = "border:2px solid #0056D2 !important;background:#EFF6FF !important" if is_selected else ""
    sv_icon = "✅" if status == "saved" else ("🔍" if status == "simulated" else "💾")
    sv_type = "primary" if status == "pending" else "secondary"

    # 카드(좌) + [상세보기 / 저장](우) 1열
    col_card, col_sv = st.columns([7, 1])

    with col_card:
        st.markdown(f"""
        <div class="data-card {sel_class}" style="padding:11px 14px;margin-bottom:0;min-height:84px;display:flex;flex-direction:column;justify-content:space-between;{sel_extra}">
            <div>
                <div class="card-title" style="margin-bottom:2px">{icon} {label}</div>
                <div class="card-meta" style="margin-bottom:5px">{new_rows:,}행{exist_txt}</div>
            </div>
            <div class="card-badges">{badges}</div>
        </div>""", unsafe_allow_html=True)

    with col_sv:
        clicked = st.button(
            "상세",
            key=f"card_{tab}_{key}",
            use_container_width=True,
            help=f"{label} 상세 보기",
        )
        save_clicked = st.button(
            sv_icon,
            key=f"sv_{tab}_{key}",
            disabled=(status == "empty"),
            use_container_width=True,
            type=sv_type,
            help="저장",
        )

    return clicked, save_clicked


def _render_detail(tab: str, key: str, new_df: pd.DataFrame, pjt_col: str,
                   raw_cols: list[str], norm_cols: list[str],
                   save_result: dict | None = None,
                   col_map: dict | None = None) -> None:
    """신호등 KPI + 서브탭(업로드/기존parquet/컬럼매핑) + 저장결과."""
    label         = SHEET_LABELS.get(key, key)
    existing_path = pc.get_parquet_dir() / f"{key}.parquet"

    existing_df = load_parquet(existing_path)
    if "호선" in existing_df.columns and pjt_col not in existing_df.columns:
        existing_df = existing_df.rename(columns={"호선": pjt_col})

    diff    = analyze_diff(new_df, existing_df, pjt_col) if not new_df.empty else {}
    added   = diff.get("added",   0)
    changed = diff.get("changed", 0)
    kept    = diff.get("kept",    0)

    st.markdown(f'<div class="section-title">📊 {label}</div>', unsafe_allow_html=True)

    st.markdown(f"""
    <div class="signal-kpi-row">
      <div class="signal-kpi {'green' if added else 'gray'}">
        <div class="kpi-num">{added}</div>
        <div class="kpi-label">🟢 신규 PJT</div>
        <div class="kpi-tooltip">기존에 없음 → 추가</div>
      </div>
      <div class="signal-kpi {'amber' if changed else 'gray'}">
        <div class="kpi-num">{changed}</div>
        <div class="kpi-label">🟡 교체 PJT</div>
        <div class="kpi-tooltip">기존+신규 → 덮어씀</div>
      </div>
      <div class="signal-kpi gray">
        <div class="kpi-num">{kept}</div>
        <div class="kpi-label">⚪ 유지 PJT</div>
        <div class="kpi-tooltip">이번 업로드에 없음</div>
      </div>
    </div>""", unsafe_allow_html=True)

    t_up, t_pq, t_col = st.tabs(["📋 업로드 데이터", "🗄️ 기존 parquet", "🔀 컬럼 매핑"])

    with t_up:
        if not new_df.empty:
            new_disp = mask_df_for_display(new_df.head(30))   # 개인정보 컬럼 표시 마스킹
            if pjt_col in new_df.columns and not existing_df.empty and pjt_col in existing_df.columns:
                exist_pjts = set(existing_df[pjt_col].astype(str))
                def _row_color(row, _ep=exist_pjts, _pc=pjt_col):
                    pjt = str(row[_pc]) if _pc in row.index else ""
                    color = "#D1FAE5" if pjt not in _ep else "#FEF9C3"
                    return [f"background-color:{color}"] * len(row)
                try:
                    st.dataframe(new_disp.style.apply(_row_color, axis=1),
                                use_container_width=True, height=320)
                except Exception:
                    st.dataframe(new_disp, use_container_width=True, height=320)
            else:
                st.dataframe(new_disp, use_container_width=True, height=320)
            st.caption("🟢 연두: 신규  🟡 노랑: 교체  (최대 30행)")
        else:
            st.info("업로드 데이터 없음")

    with t_pq:
        if existing_df.empty:
            st.info("저장된 parquet 없음")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("전체 행수", f"{len(existing_df):,}")
            c2.metric("컬럼 수", len(existing_df.columns))
            if pjt_col in existing_df.columns:
                c3.metric("PJT 수", existing_df[pjt_col].nunique())
            # TBM ptwlist: LLM 보강 컬럼을 앞쪽으로 배치한 검증 뷰
            if key == "ptwlist" and "risk_keywords" in existing_df.columns:
                filled = existing_df["risk_keywords"].notna().sum()
                st.caption(f"🤖 위험요소 보강: {filled}/{len(existing_df)}행 채워짐 (ACODENM 기준)")
                front = ["ACODENM", "risk_keywords", "warning", "PJT", "DATE", "AREA_DETAIL"]
                cols  = [c for c in front if c in existing_df.columns] + \
                        [c for c in existing_df.columns if c not in front]
                st.dataframe(mask_df_for_display(existing_df[cols].head(30)),
                             use_container_width=True, height=300)
            else:
                st.dataframe(mask_df_for_display(existing_df.head(30)),
                             use_container_width=True, height=300)

    with t_col:
        if col_map:
            rows = [{"표준 컬럼": std, "Excel 컬럼": exc or "(없음)",
                     "상태": "✅ 매핑" if exc else "⚠️ None"}
                    for std, exc in col_map.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=320)
        elif raw_cols or norm_cols:
            cl, cr = st.columns(2)
            with cl:
                st.caption(f"**Excel 원본** ({len(raw_cols)}개)")
                if raw_cols:
                    st.dataframe(pd.DataFrame({"컬럼명": [str(c) for c in raw_cols]}),
                                use_container_width=True, hide_index=True, height=280)
            with cr:
                st.caption(f"**parquet 저장** ({len(norm_cols)}개)")
                if not new_df.empty and norm_cols:
                    st.dataframe(pd.DataFrame([{"컬럼명": c, "dtype": str(new_df[c].dtype)}
                                               for c in new_df.columns]),
                                use_container_width=True, hide_index=True, height=280)
        else:
            st.info("컬럼 정보 없음")

    if save_result:
        if save_result.get("simulated"):
            st.info(f"[시뮬] {save_result.get('msg', '')}")
        elif save_result.get("ok"):
            st.success(f"✅ {save_result.get('msg', '')}")
        else:
            st.error(f"❌ {save_result.get('msg', '')}")


# ══════════════════════════════════════════════════════════════════════════════
# ESG 탭
# ══════════════════════════════════════════════════════════════════════════════

def render_esg_tab() -> None:
    _render_source_bar("ESG")

    # ── 수동 일괄 변환: 폴더의 모든 ESG xlsx → 6개 parquet (win32/DRM 복호 포함) ──
    _folder = st.session_state["folder"].get("ESG") or str(pc.get_upload_dir("esg"))
    if st.button("📥 폴더 전체 변환 → ESG parquet(6종)", key="esg_convert_all", type="primary",
                 help=f"{_folder} 의 모든 xlsx 를 읽어(win32/DRM 복호) 6개 시트 → parquet 병합 저장"):
        files = _scan_folder(_folder, "*.xlsx")
        if not files:
            st.warning(f"변환할 xlsx 가 없습니다 — 폴더 확인: {_folder}")
        else:
            pq, bk = pc.get_parquet_dir(), pc.get_backup_dir("esg")
            ok_n = fail_n = 0
            logs: list[str] = []
            with st.spinner(f"{len(files)}개 파일 변환 중..."):
                for f in files:
                    try:
                        res      = esg_converter.convert_path(f, pq, bk)
                        sheet_ok = sum(1 for r in res if r.get("ok"))
                        ok_n, fail_n = (ok_n + 1, fail_n) if sheet_ok else (ok_n, fail_n + 1)
                        fails = "; ".join(f"{r['name']}({r['msg']})" for r in res if not r.get("ok"))
                        logs.append(f"{f.name}: 시트 {sheet_ok}/{len(res)} 저장" +
                                    (f" — 미저장 {fails}"[:300] if fails else ""))
                        if sheet_ok:      # 성공분만 이동 — 실패분은 남겨 재시도
                            logs.append(f"  └ 원본 → _processed/{_move_processed(f, 'esg')}")
                    except Exception as e:
                        fail_n += 1
                        logs.append(f"{f.name}: 실패 — {e}")
            _prune_upload("esg")
            pc.touch_sentinel()
            (st.success if fail_n == 0 else st.warning)(
                f"변환 완료 — 파일 성공 {ok_n} / 실패 {fail_n}")
            with st.expander("변환 로그", expanded=fail_n > 0):
                st.code("\n".join(logs), language="text")

    preview      = st.session_state["preview"].get("ESG", {})
    save_results = st.session_state["save_results"].get("ESG", {})
    simulate     = st.session_state["simulate"].get("ESG", False)

    if not preview:
        return
    if preview.get("_error"):
        st.error(f"파싱 오류: {preview['_error']}")
        return

    st.markdown("---")
    col_cards, col_detail = st.columns([2, 3])

    with col_cards:
        st.markdown('<div class="section-title">시트별 카드</div>', unsafe_allow_html=True)

        for key in ESG_KEYS:
            new_df = preview.get(key, pd.DataFrame())
            existing_path = pc.get_parquet_dir() / f"{key}.parquet"
            existing_df   = load_parquet(existing_path)
            diff     = analyze_diff(new_df, existing_df, "PJT") if not new_df.empty else \
                       {"added":0,"changed":0,"kept":0,"new_rows":0,"exist_rows":len(existing_df)}
            status   = _card_status(key, new_df, save_results)
            selected = (st.session_state["selected_card"].get("ESG") == key)

            clicked, save_clicked = _render_card("ESG", key, diff, status, selected)
            if clicked:
                st.session_state["selected_card"]["ESG"] = key
                st.rerun()
            if save_clicked and not new_df.empty:
                with st.spinner(f"{SHEET_LABELS.get(key, key)} 저장 중..."):
                    result = _esg_save(preview, {key}, simulate)
                    st.session_state["save_results"]["ESG"].update(result)
                st.rerun()

        st.markdown("---")
        all_keys = {k for k in ESG_KEYS if not preview.get(k, pd.DataFrame()).empty}
        if st.button(
            "🔍 전체 시뮬" if simulate else "💾 전체 저장",
            key="esg_save_all", type="primary", use_container_width=True,
            disabled=not all_keys,
        ):
            with st.spinner("처리 중..."):
                results = _esg_save(preview, all_keys, simulate)
                st.session_state["save_results"]["ESG"] = results
            st.rerun()

    with col_detail:
        sel = st.session_state["selected_card"].get("ESG")
        if sel:
            new_df    = preview.get(sel, pd.DataFrame())
            raw_cols  = (preview.get("_raw_cols") or {}).get(sel, [])
            norm_cols = (preview.get("_normalized_cols") or {}).get(sel, [])
            _render_detail("ESG", sel, new_df, "PJT", raw_cols, norm_cols,
                           save_result=save_results.get(sel))
        else:
            st.info("좌측 카드를 클릭하면 상세 내용이 표시됩니다.")


# ══════════════════════════════════════════════════════════════════════════════
# TBM 탭
# ══════════════════════════════════════════════════════════════════════════════

def render_tbm_tab() -> None:
    _render_source_bar("TBM")

    # ── 수동 일괄 변환 버튼: 폴더의 모든 ptwlist → ptwlist.parquet (스캔·선택·파싱 한 번에) ──
    _folder = st.session_state["folder"].get("TBM") or str(pc.get_upload_dir("ptw"))
    if st.button("📥 폴더 전체 변환 → ptwlist.parquet", key="tbm_convert_all", type="primary",
                 help=f"{_folder} 의 ptwlist 파일을 읽어(win32/DRM) 일별 확장·병합 저장. "
                      f"변환한 원본은 _backup 사본을 남기고 _processed 로 옮긴다"):
        files = _scan_folder(_folder, tbm_converter.PTW_FILE_GLOBS)
        if not files:
            st.warning(f"변환할 ptwlist 파일이 없습니다 — 폴더/파일명 확인: {_folder}")
        else:
            pq, bk = pc.get_parquet_dir(), pc.get_backup_dir("ptw")
            ok_n = fail_n = 0
            logs: list[str] = []
            with st.spinner(f"{len(files)}개 파일 변환 중..."):
                for f in files:
                    try:
                        res  = tbm_converter.convert_path(f, pq, bk)
                        last = res[-1] if res else {}
                        if any(r.get("ok") for r in res):
                            ok_n += 1
                            logs.append(f"{f.name}: {last.get('msg', '')}"
                                        f" · 원본 → _processed/{_move_processed(f, 'ptw')}")
                            continue
                        fail_n += 1
                        logs.append(f"{f.name}: {last.get('msg', '')}")
                    except Exception as e:
                        fail_n += 1
                        logs.append(f"{f.name}: 실패 — {e}")
            _prune_upload("ptw")
            pc.touch_sentinel()
            total = len(load_parquet(pq / "ptwlist.parquet"))
            (st.success if fail_n == 0 else st.warning)(
                f"변환 완료 — 성공 {ok_n} / 실패 {fail_n} · ptwlist.parquet 총 {total:,}행")
            with st.expander("변환 로그", expanded=fail_n > 0):
                st.code("\n".join(logs), language="text")

    preview      = st.session_state["preview"].get("TBM", {})
    save_results = st.session_state["save_results"].get("TBM", {})
    simulate     = st.session_state["simulate"].get("TBM", False)

    if not preview:
        return
    if preview.get("_error"):
        st.error(f"파싱 오류: {preview['_error']}")
        return

    ptwlist = preview.get("ptwlist", pd.DataFrame())
    st.markdown("---")
    col_cards, col_detail = st.columns([2, 3])

    with col_cards:
        st.markdown('<div class="section-title">PTW 카드</div>', unsafe_allow_html=True)

        for key in ("ptwlist",):
            new_df        = ptwlist
            existing_path = pc.get_parquet_dir() / f"{key}.parquet"
            existing_df   = load_parquet(existing_path)
            diff     = analyze_diff(new_df, existing_df, "PJT") if not new_df.empty else \
                       {"added":0,"changed":0,"kept":0,"new_rows":0,"exist_rows":len(existing_df)}
            status   = _card_status(key, new_df, save_results)
            selected = (st.session_state["selected_card"].get("TBM") == key)

            clicked, save_clicked = _render_card("TBM", key, diff, status, selected)
            if clicked:
                st.session_state["selected_card"]["TBM"] = key
                st.rerun()
            if save_clicked and key == "ptwlist" and not new_df.empty:
                with st.spinner("저장 중..."):
                    results = _tbm_save(simulate)
                    st.session_state["save_results"]["TBM"].update(results)
                st.rerun()

        # DATE 확장 KPI
        src      = preview.get("_src_rows", 0)
        expanded = preview.get("_expanded_rows", 0)
        if src:
            st.markdown("---")
            st.metric("원본 → DATE 확장", f"{src} → {expanded}")

        # 미매핑 작업유형 사전 고지 (LLM 호출 없이 차집합만)
        if not ptwlist.empty:
            import ptw_enrich
            _missing = ptw_enrich.unmapped_worktypes(ptwlist)
            if _missing:
                st.info(f"🤖 저장 시 LLM 생성 예정 (미매핑 {len(_missing)}종): {', '.join(_missing)}")

        st.markdown("")
        has_src = bool(preview.get("_bytes") or preview.get("_path"))
        if st.button(
            "▶ 시뮬레이션" if simulate else "▶ 저장",
            key="tbm_save", type="primary", use_container_width=True,
            disabled=not has_src,
        ):
            with st.spinner("처리 중..."):
                results = _tbm_save(simulate)
                st.session_state["save_results"]["TBM"] = results
            st.rerun()

    with col_detail:
        sel = st.session_state["selected_card"].get("TBM")
        if sel:
            new_df  = ptwlist if sel == "ptwlist" else pd.DataFrame()
            col_map = preview.get("_col_map", {}) if sel == "ptwlist" else {}
            _render_detail("TBM", sel, new_df, "PJT", [], [],
                           save_result=save_results.get(sel), col_map=col_map)
        else:
            st.info("좌측 카드를 클릭하면 상세 내용이 표시됩니다.")


# ══════════════════════════════════════════════════════════════════════════════
# Out 탭
# ══════════════════════════════════════════════════════════════════════════════

def render_out_tab() -> None:
    _render_source_bar("Out")

    # ── 수동 일괄 변환: 폴더의 모든 outside_*.xlsx → out.parquet + ra.parquet 파생 ──
    _folder = st.session_state["folder"].get("Out") or str(pc.get_upload_dir("out"))
    if st.button("📥 폴더 전체 변환 → out.parquet + ra.parquet", key="out_convert_all", type="primary",
                 help=f"{_folder} 의 outside 파일을 읽어(win32/DRM 복호) out.parquet 저장 + ra 파생. "
                      f"변환한 원본은 _backup 사본을 남기고 _processed 로 옮긴다"):
        files = _scan_folder(_folder, out_converter.OUT_FILE_GLOBS)
        if not files:
            st.warning(f"변환할 파일이 없습니다 — 폴더/파일명 확인: {_folder}")
        else:
            pq, bk = pc.get_parquet_dir(), pc.get_backup_dir("out")
            try:
                with st.spinner(f"{len(files)}개 파일 변환 중..."):
                    files_bytes = [(f.name, out_converter.read_out_source_bytes(f)) for f in files]
                    res = out_converter.convert_and_save(files_bytes, pq, bk,
                                                         existing_ra_path=pq / "ra.parquet")
                out_r = next((r for r in res if r["name"] == "out"), {})
                if out_r.get("ok"):      # 일괄 성공 시에만 전체 이동 (out 은 파일 단위 성패가 없다)
                    for f in files:
                        _move_processed(f, "out")
                    _prune_upload("out")
                pc.touch_sentinel()
                ra_r  = next((r for r in res if r["name"] == "ra"),  {})
                file_fail = [r for r in res if r["name"] not in ("out", "ra") and not r.get("ok")]
                (st.success if out_r.get("ok") else st.warning)(
                    f"변환 완료 — out {out_r.get('rows', 0):,}행 · ra {ra_r.get('rows', 0):,}행 "
                    f"(파일 {len(files) - len(file_fail)}/{len(files)})")
                with st.expander("변환 로그", expanded=bool(file_fail) or not out_r.get("ok")):
                    st.code("\n".join(f"{r['name']}: ok={r.get('ok')} rows={r.get('rows')} {r.get('msg','')}"
                                      for r in res), language="text")
            except Exception as e:
                st.error(f"변환 실패: {e}")

    preview      = st.session_state["preview"].get("Out", {})
    save_results = st.session_state["save_results"].get("Out", {})
    simulate     = st.session_state["simulate"].get("Out", False)

    if not preview:
        return
    if preview.get("_error"):
        st.error(f"파싱 오류: {preview['_error']}")
        return

    st.markdown("---")
    col_cards, col_detail = st.columns([2, 3])

    with col_cards:
        st.markdown('<div class="section-title">데이터 카드</div>', unsafe_allow_html=True)

        for key in ("out", "ra"):
            new_df        = preview.get(key, pd.DataFrame())
            existing_path = pc.get_parquet_dir() / f"{key}.parquet"
            existing_df   = load_parquet(existing_path)
            diff     = {"added": len(new_df), "changed": 0, "kept": len(existing_df),
                        "new_rows": len(new_df), "exist_rows": len(existing_df)}
            status   = _card_status(key, new_df, save_results)
            selected = (st.session_state["selected_card"].get("Out") == key)

            clicked, save_clicked = _render_card("Out", key, diff, status, selected)
            if clicked:
                st.session_state["selected_card"]["Out"] = key
                st.rerun()
            if save_clicked:
                pairs = preview.get("_pairs", [])
                if pairs:
                    with st.spinner("저장 중..."):
                        results = _out_save(pairs, simulate)
                        st.session_state["save_results"]["Out"].update(results)
                    st.rerun()

        st.markdown("---")
        pairs = preview.get("_pairs", [])
        if st.button(
            "🔍 전체 시뮬" if simulate else "💾 전체 저장",
            key="out_save_all", type="primary", use_container_width=True,
            disabled=not pairs,
        ):
            with st.spinner("처리 중..."):
                results = _out_save(pairs, simulate)
                st.session_state["save_results"]["Out"] = results
            st.rerun()

    with col_detail:
        sel = st.session_state["selected_card"].get("Out")
        if sel:
            new_df   = preview.get(sel, pd.DataFrame())
            raw_cols = next(iter(preview.get("_raw_cols_list") or []), []) if sel == "out" else []
            pjt_col  = "project"
            _render_detail("Out", sel, new_df, pjt_col, raw_cols,
                           list(new_df.columns) if not new_df.empty else [],
                           save_result=save_results.get(sel))
        else:
            st.info("좌측 카드를 클릭하면 상세 내용이 표시됩니다.")


# ══════════════════════════════════════════════════════════════════════════════
# SQL 대체 탭
# ══════════════════════════════════════════════════════════════════════════════

# MySQL 미접속 시 parquet으로 대체되는 파일 목록
_SQL_TARGETS: dict[str, tuple[str, str]] = {
    "pjtlist":   ("호선 마스터",  "MySQL shipinfo"),
    "milestone": ("마일스톤",    "MySQL pjtevnt"),
    "shipbbs":   ("선박 게시판", "MySQL shipbbs"),
}


def render_sql_tab() -> None:
    pq_dir = pc.get_parquet_dir()
    st.markdown("---")
    col_cards, col_detail = st.columns([2, 3])

    with col_cards:
        st.markdown('<div class="section-title">SQL 대체 파일 현황</div>', unsafe_allow_html=True)

        for key, (label, source) in _SQL_TARGETS.items():
            path = pq_dir / f"{key}.parquet"
            selected = (st.session_state["selected_card"].get("SQL 대체") == key)
            sel_extra = "border:2px solid #0056D2 !important;background:#EFF6FF !important" if selected else ""

            # 파일 상태 파악
            try:
                df_s = load_parquet(path)
            except Exception:
                df_s = None
            if df_s is None:
                badge = '<span class="badge badge-error">❌ 읽기 실패</span>'
                icon  = "❌"
                meta  = source
            elif df_s.empty:
                badge = '<span class="badge badge-error">❌ 파일 없음</span>'
                icon  = "❌"
                meta  = source
            else:
                badge = (f'<span class="badge badge-saved">✅ {len(df_s):,}행</span>'
                         f'<span class="badge badge-gray">{len(df_s.columns)}컬럼</span>')
                icon  = "✅"
                meta  = source

            c_card, c_btn = st.columns([7, 1])
            with c_card:
                st.markdown(f"""
                <div class="data-card" style="padding:11px 14px;margin-bottom:0;
                     min-height:38px;{sel_extra}">
                    <div class="card-title" style="margin-bottom:2px">{icon} {label}</div>
                    <div class="card-meta" style="margin-bottom:4px">{meta}</div>
                    <div class="card-badges">{badge}</div>
                </div>""", unsafe_allow_html=True)
            with c_btn:
                if st.button("상세", key=f"sql_card_{key}", use_container_width=True):
                    st.session_state["selected_card"]["SQL 대체"] = key
                    st.rerun()

    with col_detail:
        sel = st.session_state["selected_card"].get("SQL 대체")
        if sel and sel in _SQL_TARGETS:
            label, source = _SQL_TARGETS[sel]
            path = pq_dir / f"{sel}.parquet"
            st.markdown(f'<div class="section-title">📊 {label} — {sel}.parquet</div>',
                        unsafe_allow_html=True)
            df_d = load_parquet(path)
            if df_d.empty and not path.exists():
                st.warning(f"`{sel}.parquet` 파일 없음 — 사내망 접속 후 DB에서 자동 저장됩니다.")
            else:
                try:
                    c1, c2, c3 = st.columns(3)
                    c1.metric("행수", f"{len(df_d):,}")
                    c2.metric("컬럼수", len(df_d.columns))
                    c3.metric("수정일", datetime.fromtimestamp(path.stat().st_mtime).strftime("%y-%m-%d"))
                    st.dataframe(df_d, use_container_width=True, height=420)
                except Exception as e:
                    st.error(f"읽기 실패: {e}")
        else:
            st.info("좌측 카드를 클릭하면 데이터가 표시됩니다.")


# ══════════════════════════════════════════════════════════════════════════════
# 라우터
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# 신규 탭 — 날씨 / 문서 / 매핑 / 메시지 / DB·날짜
# ══════════════════════════════════════════════════════════════════════════════

def render_weather_tab() -> None:
    st.markdown('<div class="section-title">🌤️ 기상청 단기예보 → weather.parquet</div>',
                unsafe_allow_html=True)
    import api_weather
    existing = api_weather.load_weather_parquet()
    if existing is not None and not existing.empty:
        st.caption(f"현재 저장본: {len(existing)}일치")
        st.dataframe(existing, use_container_width=True, height=260)
    else:
        st.info("저장된 weather.parquet 없음")
    if st.button("🌐 날씨 수집 실행", type="primary", key="wx_run"):
        logs = []
        with st.spinner("수집 중..."):
            df, status = api_weather.collect_all(progress_cb=lambda s, m: logs.append(m))
        for m in logs:
            st.write(m)
        if status == "success":
            st.success(f"✅ {len(df)}일치 저장 완료")
            st.rerun()
        else:
            st.error(status)
    st.caption("자동 수집은 weather_job.py 를 스케줄러(일 2회)로 등록하세요.")


def render_mapping_tab() -> None:
    st.markdown('<div class="section-title">🗂️ 위험요소 매핑 (mapping.parquet)</div>',
                unsafe_allow_html=True)
    path = pc.get_parquet_dir() / "mapping.parquet"
    df = load_parquet(path)
    if df.empty:
        df = pd.DataFrame(columns=["work", "keyword", "warning"])

    # ── 미매핑 ACODENM 스캔 + LLM 일괄 생성 ──────────────────────────────
    import ptw_enrich
    ptw_df  = load_parquet(pc.get_parquet_dir() / "ptwlist.parquet")
    mapping = {str(r["work"]).strip(): True for _, r in df.iterrows() if str(r.get("work", "")).strip()}
    missing = ptw_enrich.unmapped_worktypes(ptw_df, mapping)

    st.markdown('<div class="section-title">🤖 미매핑 작업유형 LLM 생성</div>',
                unsafe_allow_html=True)
    if missing:
        st.warning(f"ptwlist에 매핑 안 된 작업유형 **{len(missing)}종**: {', '.join(missing)}")
        if st.button("🤖 미매핑 LLM 생성", type="primary", key="map_gen"):
            prog = st.progress(0.0, text="LLM 생성 준비 중...")
            def _cb(done, total, work):
                prog.progress(done / total if total else 1.0, text=f"[{done}/{total}] {work}")
            with st.spinner("LLM 위험요소 생성 중..."):
                res = ptw_enrich.generate_for_worktypes(missing, progress_cb=_cb)
            prog.empty()
            if res["ok"]:
                st.success(f"✅ {res['ok']}종 생성·매핑 추가: {', '.join(res['added'])}")
            if res["fail"]:
                st.error(f"⚠️ {res['fail']}종 생성 실패 (다음에 재시도 가능)")
            st.rerun()
    else:
        st.success("✅ ptwlist의 모든 작업유형이 매핑되어 있습니다.")

    st.markdown("---")
    st.caption("작업유형(work)별 위험키워드. ptwlist 업로드 시 미매핑은 LLM이 자동 추가합니다. 아래 표에서 직접 편집도 가능합니다.")
    edited = st.data_editor(df, use_container_width=True, num_rows="dynamic", height=420, key="map_edit")
    if st.button("💾 매핑 저장", type="primary", key="map_save"):
        save_parquet_atomic(edited, path)
        st.success(f"✅ mapping.parquet {len(edited)}행 저장")
        st.rerun()


def render_dbgen_tab() -> None:
    st.markdown('<div class="section-title">🗄️ DB→parquet 생성 / 날짜 테이블</div>',
                unsafe_allow_html=True)
    import db_connector, date_manager
    st.caption("MySQL shipinfo/pjtevnt/shipbbs → pjtlist/milestone/shipbbs. "
               "DB 미연결 시 기존/더미 폴백. milestone 은 raw wide 로 저장(unpivot 안 함).")
    if st.button("🗄️ DB 생성 실행 (3종)", type="primary", key="dbgen_run"):
        with st.spinner("DB 조회 중..."):
            results = db_connector.gen_all()
        for r in results:
            (st.success if r["msg"].startswith("✅") else st.warning)(f"{r['name']}: {r['msg']}")
    st.markdown("---")
    st.caption("날짜 테이블(date.parquet) — 2025-01-01 ~ 오늘+7일, 공휴일 포함")
    if st.button("📅 date.parquet 생성", key="date_run"):
        with st.spinner("생성 중..."):
            d = date_manager.generate_date_table()
            date_manager.save_date_parquet(d)
        st.success(f"✅ date.parquet {len(d)}행")


# ══════════════════════════════════════════════════════════════════════════════
# 안전메시지 (직접 입력) — tbm admin message_view 이관
# ══════════════════════════════════════════════════════════════════════════════

_MSG_EXT_TYPE = {
    "pdf": "pdf", "pptx": "ppt", "ppt": "ppt", "xlsx": "excel", "xls": "excel",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
    "mp4": "video", "mov": "video", "avi": "video",
}
_MSG_ICON = {"pdf": "📄", "ppt": "📊", "excel": "📗", "image": "🖼️",
             "video": "🎬", "link": "🔗", "없음": "—"}
_MSG_TEAMS = ["전체", "시운전팀", "시운전1부", "시운전2부", "시운전3부"]


def _msg_save_file(uploaded) -> tuple[str, str]:
    """첨부파일을 upload/message/ 에 저장 → (ref_type, 경로)."""
    save_dir = pc.get_message_dir()
    ext = uploaded.name.rsplit(".", 1)[-1].lower()
    ref_type = _MSG_EXT_TYPE.get(ext, "pdf")
    # 같은 이름이면 타임스탬프 접미 — 덮어쓰면 기존 메시지의 ref_path 가 다른 파일을 가리킨다
    sp = pc.unique_path(save_dir, uploaded.name)
    sp.write_bytes(uploaded.getbuffer())
    return ref_type, str(sp)


def render_message_screen() -> None:
    import message_store
    st.markdown(_STATUS_CSS, unsafe_allow_html=True)
    st.markdown('<div class="section-title">📢 팀장 안전메시지 (message.parquet)</div>',
                unsafe_allow_html=True)
    df = message_store.load_messages()

    # ── 입력 폼 ──────────────────────────────────────
    with st.expander("✏️ 안전메시지 입력", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            m_date = st.date_input("날짜", value=date.today(), key="msg_date")
        with c2:
            m_team = st.selectbox("팀/부서", _MSG_TEAMS, key="msg_team")
        m_content = st.text_area("전달사항 내용 *", height=120, key="msg_content",
                                 placeholder="예) 고소작업 시 안전대 체결을 반드시 확인하세요.")
        st.markdown("##### 📎 참고자료 첨부")
        t_file, t_link = st.tabs(["파일 업로드", "링크 입력"])
        ref_type, ref_path = "없음", ""
        with t_file:
            up = st.file_uploader("파일 선택 (PDF/PPT/Excel/이미지/동영상)",
                                  type=list(_MSG_EXT_TYPE.keys()), key="msg_file")
            if up:
                st.success(f"✅ {up.name} 선택됨")
                ref_type = _MSG_EXT_TYPE.get(up.name.rsplit(".", 1)[-1].lower(), "pdf")
        with t_link:
            link = st.text_input("URL 입력", key="msg_link",
                                 placeholder="https://youtube.com/... 또는 사내 공유 링크")
            if link.strip():
                ref_type, ref_path = "link", link.strip()
        if st.button("💾 저장", type="primary", disabled=not m_content.strip(), key="msg_save"):
            if up and not ref_path:
                try:
                    ref_type, ref_path = _msg_save_file(up)
                except Exception:
                    ref_path = up.name
            new_row = pd.DataFrame([{
                "date": str(m_date), "team": m_team, "content": m_content.strip(),
                "ref_type": ref_type, "ref_path": ref_path,
            }])
            message_store.save_messages(pd.concat([df, new_row], ignore_index=True))
            st.success("✅ 안전메시지 저장 완료")
            st.rerun()

    st.divider()

    # ── 2단: 좌(검색+날짜별 목록) / 우(내용) ────────────────
    col_l, col_r = st.columns([1.1, 2.4])
    with col_l:
        st.markdown('<div class="dm-dhead" style="font-size:14px">📋 안전메시지 목록</div>',
                    unsafe_allow_html=True)
        q = st.text_input("🔍 검색 (날짜·팀·내용)", key="msg_search",
                          placeholder="예: 고소작업 / 시운전팀 / 2026-06").strip()
        view = df.copy()
        if q and not df.empty:
            ql = q.lower()
            mask = (view["date"].astype(str).str.lower().str.contains(ql, na=False)
                    | view["team"].astype(str).str.lower().str.contains(ql, na=False)
                    | view["content"].astype(str).str.lower().str.contains(ql, na=False))
            view = view[mask]
        view = view.sort_values("date", ascending=False) if not view.empty else view
        st.caption(f"검색 결과 {len(view)}건 / 전체 {len(df)}건")

        sel = st.session_state.get("msg_sel")
        if sel not in view.index:
            sel = view.index[0] if not view.empty else None

        if df.empty:
            st.info("등록된 안전메시지가 없습니다. 상단 '✏️ 새 안전메시지 입력'에서 추가하세요.")
        elif view.empty:
            st.info("검색 결과가 없습니다.")
        else:
            cur_date = None
            for idx, row in view.iterrows():
                d = str(row["date"])
                if d != cur_date:
                    st.markdown(f'<div class="msg-date-h">📅 {d}</div>', unsafe_allow_html=True)
                    cur_date = d
                icon = _MSG_ICON.get(row.get("ref_type", "없음"), "—")
                snippet = str(row["content"]).replace("\n", " ")[:22]
                if st.button(f"{icon} {row['team']} — {snippet}", key=f"msgsel_{idx}",
                             type="primary" if idx == sel else "secondary", use_container_width=True):
                    st.session_state["msg_sel"] = idx
                    st.rerun()
    with col_r:
        if sel is not None and sel in df.index:
            _render_msg_detail(df.loc[sel], sel, df, message_store)
        else:
            st.info("좌측 목록에서 메시지를 선택하면 내용이 표시됩니다.")


def _render_msg_detail(row, idx, df, message_store) -> None:
    """우측 — 선택 메시지 전체 내용 + 첨부 + 삭제."""
    icon = _MSG_ICON.get(row.get("ref_type", "없음"), "—")
    st.markdown(f'<div class="msg-detail-head">{icon} {row["date"]} '
                f'<span style="color:#0056D2">· {_esc(row["team"])}</span></div>',
                unsafe_allow_html=True)
    st.markdown(f'<div class="msg-content">{_esc(row["content"])}</div>', unsafe_allow_html=True)
    ref_t, ref_p = row.get("ref_type", "없음"), row.get("ref_path", "")
    if ref_t != "없음" and ref_p:
        st.markdown(f"**📎 참고자료** · `{ref_t}`")
        if ref_t == "link":
            st.markdown(f"🔗 [링크 열기]({ref_p})")
        elif ref_t == "video" and ("youtube" in str(ref_p) or "youtu.be" in str(ref_p)):
            vid = str(ref_p).split("v=")[-1].split("&")[0].split("/")[-1]
            st.video(f"https://www.youtube.com/watch?v={vid}")
        else:
            fp = Path(ref_p)
            st.markdown(f"📁 파일명: `{fp.name}`")
            if fp.exists():
                st.download_button(f"⬇️ {fp.name} 다운로드", data=fp.read_bytes(),
                                   file_name=fp.name, key=f"msg_dl_{idx}")
    st.markdown("")
    if st.button("🗑️ 이 메시지 삭제", key=f"msg_del_{idx}"):
        mask = ((df["date"] == row["date"]) & (df["team"] == row["team"]) &
                (df["content"] == row["content"]))
        message_store.save_messages(df[~mask].reset_index(drop=True))
        st.session_state.pop("msg_sel", None)
        st.success("삭제 완료")
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# 데이터 변환(수동 fallback 탭) + 사용자 정의 parquet 추가
# ══════════════════════════════════════════════════════════════════════════════

def _render_convert_tabs() -> None:
    """원본 업로드·변환 (수동) — 기존 변환 섹션 재사용.

    DASH_SECTIONS 7개 섹션. 문서·메시지는 별도 화면이라 제외.

    카탈로그에서 '변환 화면' 링크로 들어오면(session_state["cv_sec"]) 해당 섹션만
    단독으로 그린다. st.tabs 는 코드로 특정 탭을 열 수 없어서, 탭 없이 렌더한다.
    """
    _dispatch = {
        "ESG": render_esg_tab, "TBM": render_tbm_tab, "Out": render_out_tab,
        "SQL 대체": render_sql_tab, "날씨": render_weather_tab,
        "매핑": render_mapping_tab, "DB·날짜": render_dbgen_tab,
    }
    sec = st.session_state.get("cv_sec")   # pop 아님 — 섹션 내 버튼이 rerun 해도 유지돼야 함
    if sec in _dispatch:
        if st.button("← 전체 변환 화면", key="cv_sec_all"):
            st.session_state.pop("cv_sec", None)
            st.rerun()
        reason = ta.MENU_REASON.get(sec, "")
        if reason:
            st.caption(reason)
        _dispatch[sec]()
    else:
        sub = st.tabs(DASH_SECTIONS)
        for t, s in zip(sub, DASH_SECTIONS):
            with t:
                _dispatch[s]()

    # 운영서버(MSSQL) 전송 — .env DB_* 설정 시에만 활성(휴면). parquet → jsh_* 테이블 전체 교체.
    # 저장 시 자동 전송(parquet_io → db.auto_sync)과 별개로, 전체 수동 재전송 버튼 유지.
    st.divider()
    import db.connection as _dbc
    with st.expander("🛢 운영서버(MSSQL) 전송", expanded=False):
        st.caption(_dbc.status_text())
        if _dbc.is_configured():
            st.caption("💡 parquet 저장 완료 시 저장된 파일을 읽어 MSSQL로 순차 자동 전송합니다 "
                       "(엑셀 → parquet → MSSQL). 아래 버튼은 전체 테이블 수동 재전송용입니다.")
            if st.button("⬆️ 전체 테이블 → MSSQL 전송 (jsh_*)", key="mssql_sync", use_container_width=True):
                import db.sync as _dbs
                with st.spinner("MSSQL 전송 중..."):
                    res = _dbs.sync_all(pc.get_parquet_dir())
                ok = sum(1 for r in res if r.get("ok"))
                (st.success if res and all(r.get("ok") for r in res) else st.warning)(
                    f"전송 {ok}/{len(res)} 테이블 완료")
                st.dataframe(pd.DataFrame(res), use_container_width=True, hide_index=True)
            import db.auto_sync as _dba
            _auto = _dba.recent_results()
            if _auto:
                st.caption("최근 자동 전송 결과 (최신순)")
                st.dataframe(pd.DataFrame(_auto), use_container_width=True, hide_index=True)
        else:
            st.info("운영서버 접속정보(.env DB_*)를 입력하면 전송 버튼이 활성화됩니다. (사내망 전용)")


def _folder_browser(state_key: str, default: Path) -> str:
    """입력 폴더 선택 — 경로 직접 입력 + ⬆상위 이동. 현재 폴더가 곧 선택값.

    (이미 생성된 폴더를 지정하는 용도 — 새 폴더 생성·하위 탐색 버튼 없음)
    """
    pk = f"{state_key}_path"
    if pk not in st.session_state:
        st.session_state[pk] = str(default)
    c_path, c_up = st.columns([5, 1])
    with c_up:                          # 텍스트입력보다 먼저 실행 → 상위 경로 반영
        st.caption("​")                 # 버튼 수직 정렬용
        if st.button("⬆ 상위", key=f"{state_key}_up", use_container_width=True):
            st.session_state[pk] = str(Path(st.session_state[pk]).parent)
    with c_path:
        cur = st.text_input("현재 폴더 경로 (직접 입력 가능)", key=pk,
                            placeholder=r"예: F:\code\data\upload\esg  또는  D:\reports").strip()
    if cur:
        try:                                  # os.listdir = 존재확인 + 이름 카운트 (DRM 안전)
            n = len([x for x in os.listdir(cur) if not x.startswith("_")])
            st.success(f"✅ 선택된 입력 폴더: `{cur}`  (파일 {n}개)")
        except Exception:
            st.warning(f"해당 경로의 폴더가 없습니다: `{cur}`")
    return cur


def _render_pq_add() -> None:
    """사용자 정의 parquet 추가 — 폴더 선택 + 이름 + 파일 업로드 → raw 저장. (별도 메뉴)"""
    st.caption("입력 폴더를 **파일탐색기처럼 자유롭게 선택**하고 parquet 이름을 지정하면, 업로드한 엑셀/CSV를 "
               "그대로 변환·저장합니다. (도메인 변환 없음) · 추가분은 '📊 데이터 현황'에 자동 표시됩니다.")
    st.markdown("##### 📂 입력 폴더 선택")
    folder = _folder_browser("add_browse", pc.get_upload_dir())
    st.markdown("---")
    c_form, c_prev = st.columns([1, 1.4])
    with c_form:
        name   = st.text_input("parquet 이름", placeholder="예: custom_table", key="add_name")
        up     = st.file_uploader("원본 파일 (xlsx/csv)", type=["xlsx", "csv"], key="add_file")
        do_save = st.button("💾 생성·저장", type="primary",
                            disabled=not (name.strip() and up), key="add_save")
    with c_prev:
        if up is not None:
            try:
                _pv = pd.read_csv(up, nrows=20) if up.name.lower().endswith(".csv") else pd.read_excel(up, nrows=20)
                st.caption(f"업로드 미리보기 — {up.name} (상위 20행)")
                st.dataframe(_pv, use_container_width=True, height=280)
            except Exception as e:
                st.warning(f"미리보기 실패: {e}")
        else:
            st.info("원본 파일을 업로드하면 미리보기가 표시됩니다.")
    if do_save:
        try:
            df = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
            key = name.strip().replace(" ", "_")
            try:
                # 원본 사본은 upload/custom/_backup 에 둔다 — 선택한 폴더에 그대로 쓰면
                # 그 폴더가 upload/esg 일 때 다음 ESG 폴더 변환이 이 파일을 ESG 원본으로
                # 읽고, 시트명 불일치 시 인덱스 폴백이라 엉뚱한 시트가 조용히 병합된다.
                bpath = pc.unique_path(pc.get_backup_dir("custom"),
                                       f"{datetime.now():%Y%m%d}_{up.name}")
                bpath.write_bytes(up.getbuffer())
            except Exception:   # noqa: BLE001 — 사본 실패로 parquet 생성을 막지 않는다
                pass
            save_parquet_atomic(df, pc.get_parquet_dir() / f"{key}.parquet")
            d = load_sources()
            if not any(c["key"] == key for c in d["custom"]):
                d["custom"].append({"key": key, "label": name.strip(), "folder": folder})
            d["folders"][key] = folder
            save_sources(d)
            st.query_params["pq"] = key   # 데이터 현황에서 선택되도록 예약
            st.success(f"✅ {key}.parquet 생성 ({len(df):,}행) — 상단 '📊 데이터 현황' 메뉴에서 확인하세요.")
        except Exception as e:
            st.error(f"실패: {e}")


_STATUS_CSS = """
<style>
.dm-wrap{display:grid;grid-template-columns:360px 1fr;gap:16px;margin-top:4px}
.dm-panel{background:#fff;border:1px solid #E2E8F0;border-radius:10px;overflow:hidden}
.dm-lh{padding:10px 14px;font-weight:700;font-size:13px;border-bottom:1px solid #E2E8F0;background:#F8FAFC}
.dm-lh .n{color:#94A3B8;font-weight:400}
.dm-item{display:block;padding:9px 13px;border-bottom:1px solid #F1F5F9;border-left:3px solid transparent;text-decoration:none;color:#1E293B}
.dm-item:hover{background:#FAFCFF}
.dm-item.on{background:#EFF6FF;border-left-color:#0056D2}
.dm-item .nm{font-weight:700;font-size:13px;display:flex;align-items:center;gap:6px}
.dm-item .pf{font-family:monospace;font-size:10px;color:#94A3B8;margin-top:1px}
.dm-item .mt{font-size:9.5px;color:#64748B;margin-top:3px;display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.dm-chip{background:#F1F5F9;border-radius:4px;padding:1px 5px;font-family:monospace;font-size:9.5px}
.dm-chip.blank{color:#CBD5E1;font-style:italic;font-family:inherit}
.dm-badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:10.5px;font-weight:700}
.b-ok{background:#DCFCE7;color:#16A34A}.b-gray{background:#F1F5F9;color:#64748B}
.dm-date{padding:1px 6px;border-radius:4px;font-size:9.5px;font-weight:700;font-family:monospace}
.dm-date.d-new{background:#DCFCE7;color:#16A34A}
.dm-date.d-mid{background:#EFF6FF;color:#0056D2}
.dm-date.d-old{background:#FEF3C7;color:#D97706}
.dm-date.d-none{background:#F1F5F9;color:#CBD5E1}
.dm-add{display:block;text-align:center;padding:9px;margin:8px;border:1px dashed #BFDBFE;border-radius:8px;color:#0056D2;font-weight:700;text-decoration:none;font-size:12.5px}
.dm-add:hover{background:#EFF6FF}
.dm-dbar{padding:12px 16px;border-bottom:1px solid #E2E8F0;background:#F8FAFC}
.dm-dbar .t{font-weight:700;font-size:15px}
.dm-meta{display:flex;gap:18px;margin-top:9px;flex-wrap:wrap;font-size:12px;align-items:center}
.dm-meta .k{color:#94A3B8;margin-right:5px}.dm-meta .v{font-family:monospace}
.fold-chip{text-decoration:none;color:#64748B;background:#F1F5F9;border:1px solid #E2E8F0;border-radius:5px;padding:2px 8px;font-family:monospace;font-size:10.5px;margin-right:3px}
.fold-chip.on{background:#0056D2;color:#fff;border-color:#0056D2}
.dm-cols{padding:8px 16px;font-size:11.5px;color:#64748B;border-bottom:1px solid #E2E8F0}
.dm-cols b{color:#1E293B}
.dm-tbl{overflow:auto;max-height:520px}
table.dm-data{width:100%;border-collapse:collapse;font-size:12px}
table.dm-data th{position:sticky;top:0;background:#EEF2F7;text-align:left;padding:7px 12px;font-weight:700;color:#334155;border-bottom:2px solid #E2E8F0;white-space:nowrap;font-size:11px}
table.dm-data td{padding:6px 12px;border-bottom:1px solid #F1F5F9;white-space:nowrap;color:#334155;max-width:280px;overflow:hidden;text-overflow:ellipsis}
table.dm-data td.hl{color:#0056D2;font-weight:600}
.dm-none{color:#CBD5E1;font-style:italic}
/* 목록 네이티브 버튼 카드화 (리로드 없는 즉시 전환) */
div[class*="st-key-pqsel_"] button{width:100%;justify-content:flex-start;text-align:left;
  border:1px solid #E2E8F0;border-radius:8px;font-weight:700;font-size:13px;padding:8px 12px;}
div[class*="st-key-pqsel_"] button:hover{border-color:#0056D2;}
.dm-meta-row{font-size:9.5px;color:#94A3B8;font-family:monospace;margin:-6px 0 9px 4px;
  display:flex;gap:5px;align-items:center;flex-wrap:wrap}
.dm-dhead{font-size:15px;font-weight:700;margin-bottom:8px}
.dm-dhead .k2{font-family:monospace;font-size:12px;color:#94A3B8;font-weight:400}
/* 안전메시지 2단 */
.msg-date-h{font-size:11px;font-weight:700;color:#0056D2;background:#EFF6FF;
  padding:4px 10px;border-radius:5px;margin:12px 0 5px;display:inline-block}
.msg-detail-head{font-size:15px;font-weight:700;margin-bottom:10px}
.msg-content{font-size:13px;line-height:1.7;color:#1E293B;background:#F8FAFC;
  border:1px solid #E2E8F0;border-radius:8px;padding:14px 16px;white-space:pre-wrap}
</style>
"""


# ══════════════════════════════════════════════════════════════════════════════
# 문서 파싱 (사고 / 가이드) — 좌 목록 / 우 원본 PDF
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def _pdf_page_png(pdf_bytes: bytes, page_idx: int, scale: float = 1.7) -> "tuple[bytes | None, int]":
    """PDF의 page_idx(0-base) 한 페이지만 PNG로 렌더 + 총 페이지수 (넘김 뷰어용).

    base64 iframe은 Chrome이 차단하므로 pypdfium2로 이미지 렌더. 페이지별 캐시(넘김 빠름).
    """
    import io
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    total = len(doc)
    try:
        idx = max(0, min(page_idx, total - 1))
        img = doc[idx].render(scale=scale).to_pil()
        buf = io.BytesIO(); img.save(buf, format="PNG")
        return buf.getvalue(), total
    finally:
        doc.close()


def _doc_status(sec: str) -> dict:
    """parquet의 pdf_filename별 추출 건수 {파일명: 건수} (파싱여부 표시)."""
    import doc_parser
    df = doc_parser.load_accident() if sec == "accident" else doc_parser.load_guide()
    if df is None or df.empty or "pdf_filename" not in df.columns:
        return {}
    return df["pdf_filename"].astype(str).value_counts().to_dict()


def render_doc_screen() -> None:
    import doc_parser
    st.markdown('<div class="section-title">📄 문서 파싱 (사고 / 가이드)</div>', unsafe_allow_html=True)
    kind = st.radio("문서 종류", ["🔴 사고 (accident)", "🔵 가이드 (guide)"],
                    horizontal=True, label_visibility="collapsed", key="doc_kind")
    sec = "accident" if "accident" in kind else "guide"
    folder = pc.get_upload_dir(sec)
    pdfs = [folder / n for n in sorted(_list_dir_files(folder, ["*.pdf"]))]   # DRM/네트워크 안전
    status = _doc_status(sec)
    sel_key = f"doc_sel_{sec}"
    if pdfs and st.session_state.get(sel_key) not in [f.name for f in pdfs]:
        st.session_state[sel_key] = pdfs[0].name

    _parse = doc_parser.parse_accident_pdfs if sec == "accident" else doc_parser.parse_guide_pdfs
    b1, b2, b3 = st.columns([1, 1, 3])
    if b1.button("🤖 전체 파싱", key=f"parse_all_{sec}", disabled=not pdfs, use_container_width=True):
        with st.spinner("파싱 중..."):
            _parse()
        st.cache_data.clear(); st.success("✅ 전체 파싱 완료"); st.rerun()
    if b2.button("➕ 신규만", key=f"parse_new_{sec}", disabled=not pdfs, use_container_width=True,
                 help="이미 파싱된 PDF는 건너뜀"):
        with st.spinner("신규 파싱 중..."):
            _parse(skip_existing=True)
        st.cache_data.clear(); st.success("✅ 신규 파싱 완료"); st.rerun()
    done_n = sum(1 for f in pdfs if status.get(f.name))
    b3.caption(f"폴더: {folder}  ·  PDF {len(pdfs)}건 · 파싱완료 {done_n}건")

    if not pdfs:
        st.info(f"upload/{sec} 폴더에 PDF가 없습니다.")
        return

    left, mid, right = st.columns([1.1, 2, 1.8])

    # ── 좌: 목록 + 파싱여부 ──
    with left:
        st.markdown("**📋 문서 목록**")
        for f in pdfs:
            cnt = status.get(f.name, 0)
            badge = f"✅ {cnt}건" if cnt else "⬜ 미파싱"
            on = (st.session_state.get(sel_key) == f.name)
            if st.button(f"{'📕' if on else '📄'} {f.name}\n{badge}", key=f"docpick_{sec}_{f.name}",
                         use_container_width=True, type="primary" if on else "secondary"):
                st.session_state[sel_key] = f.name
                st.session_state[f"docpage_{sec}_{f.name}"] = 0
                st.rerun()

    name = st.session_state.get(sel_key)
    fp = folder / name

    # ── 중: 넘김 뷰어 (1페이지씩, 스크롤 없음) ──
    with mid:
        st.markdown(f"**📕 {name}**")
        pk = f"docpage_{sec}_{name}"
        idx = int(st.session_state.get(pk, 0))
        try:
            data = fp.read_bytes()
            png, total = _pdf_page_png(data, idx)
            n_l, n_m, n_r = st.columns([1, 2, 1])
            if n_l.button("◀ 이전", key=f"prev_{sec}", use_container_width=True, disabled=idx <= 0):
                st.session_state[pk] = max(0, idx - 1); st.rerun()
            n_m.markdown(f"<div style='text-align:center;padding-top:6px'>{idx + 1} / {total}</div>",
                         unsafe_allow_html=True)
            if n_r.button("다음 ▶", key=f"next_{sec}", use_container_width=True, disabled=idx >= total - 1):
                st.session_state[pk] = min(total - 1, idx + 1); st.rerun()
            if png:
                st.image(png, use_container_width=True)
            st.download_button("⬇️ 원본 PDF", data=data, file_name=name, key=f"dl_{sec}",
                               use_container_width=True)
        except Exception as e:
            st.error(f"뷰어 표시 실패: {e}")

    # ── 우: 추출 레코드 + 원문 텍스트 ──
    with right:
        st.markdown("**🧩 추출 결과**")
        dfp = doc_parser.load_accident() if sec == "accident" else doc_parser.load_guide()
        recs = (dfp[dfp["pdf_filename"].astype(str) == name]
                if (dfp is not None and not dfp.empty and "pdf_filename" in dfp.columns) else None)
        if recs is None or recs.empty:
            st.info("아직 파싱되지 않았습니다. 위 ‘전체/신규 파싱’을 실행하세요.")
        else:
            st.caption(f"이 문서에서 추출 {len(recs)}건")
            for _, r in recs.iterrows():
                with st.container(border=True):
                    if sec == "accident":
                        st.markdown(f"**#{r.get('seq', '')} · {r.get('accident_type', '') or '사고'}**")
                        st.write(r.get("summary", ""))
                        st.caption(f"원인: {r.get('cause', '')}\n\n대책: {r.get('countermeasure', '')}"
                                   f"\n\n키워드: {r.get('keywords', '')}")
                    else:
                        st.markdown(f"**{r.get('standard_id', '') or r.get('id', '')} · {r.get('title', '')}**")
                        st.caption(f"키워드: {r.get('keywords', '')}")
        with st.expander("📝 파싱된 원문 텍스트", expanded=False):
            try:
                txt = doc_parser.extract_text_from_pdf(fp)
                st.text_area("원문", txt or "(텍스트 추출 실패)", height=240,
                             label_visibility="collapsed", key=f"txt_{sec}_{name}")
            except Exception as e:
                st.caption(f"텍스트 추출 실패: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 라우터
# ══════════════════════════════════════════════════════════════════════════════

# ── 라우터 — 새 카탈로그 UI. 기능 화면(변환/추가/문서/메시지)은 handlers 로 전달.
cv.inject_css()
cv.render_app(handlers={
    "convert": _render_convert_tabs,   # 원본 업로드·변환 (수동 fallback 탭)
    "add":     _render_pq_add,         # 사용자 정의 parquet 추가
    "doc":     render_doc_screen,      # 문서 파싱 (사고/가이드)
    "message": render_message_screen,  # 팀장 안전메시지
})
