"""catalog_view.py — 재디자인 UI (좌측 카테고리 내비 + 통합 카탈로그 + 상세)

프로토타입 "데이터 매니저 프로토타입" 을 Streamlit 으로 옮긴 드롭인 모듈.
기존 app.py 의 데이터 배관(path_config, parquet 읽기)만 재사용하고,
상단 4메뉴 → 좌측 사이드바 카테고리 내비 + 필터바 + 통합 표 + 상세로 재구성한다.

app.py 통합:
    import catalog_view as cv
    cv.inject_css()
    cv.render_app(handlers={
        "convert": _render_convert_tabs,     # 원본 업로드·변환 (수동)
        "add":     _render_pq_add,           # parquet 추가
        "doc":     render_doc_screen,        # 문서 파싱
        "message": render_message_screen,    # 안전메시지
    })
그리고 기존 상단 st.radio(MENU...) 라우터는 제거한다.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import path_config as pc
import table_actions as ta   # 테이블 단위 변환 레지스트리(RUN_SINGLE/RUN_MENU)·실행
from ui_components import SHEET_LABELS, esc_html as _esc
from pii import mask_df_for_display

# ── 카탈로그 정의 ────────────────────────────────────────────────────────────
# (라벨, 파일키)  — 문서/메시지 포함 전체
DASH_PARQUETS: list[tuple[str, str]] = [
    ("시운전일정", "trial_schedule"), ("사용량", "fuel_usage"), ("유류단가", "fuel_price"),
    ("LNG", "lng_usage"), ("연간계획", "fuel_plan"), ("공법", "pjtmethod"),
    ("PTW 리스트", "ptwlist"),
    # ptwlist_archive 는 현재 생성 로직이 없어(항상 빈 항목) 카탈로그에서 제외.
    #   아카이브 분리 로직 도입 시 다시 추가.
    ("사외작업자", "out"), ("위험성평가", "ra"),
    ("호선 마스터", "pjtlist"), ("마일스톤", "milestone"), ("선박게시판", "shipbbs"),
    ("날씨", "weather"), ("사고", "accident"), ("가이드", "guide"),
    ("매핑", "mapping"), ("달력", "date"), ("안전메시지", "message"),
]

CATEGORY: dict[str, str] = {
    "trial_schedule": "유류·ESG", "fuel_usage": "유류·ESG", "fuel_price": "유류·ESG",
    "lng_usage": "유류·ESG", "fuel_plan": "유류·ESG", "pjtmethod": "유류·ESG",
    "ptwlist": "안전·작업허가", "ptwlist_archive": "안전·작업허가", "out": "안전·작업허가", "ra": "안전·작업허가",
    "pjtlist": "마스터·SQL", "milestone": "마스터·SQL", "shipbbs": "마스터·SQL",
    "weather": "자동수집", "date": "자동수집", "mapping": "자동수집",
    "accident": "문서", "guide": "문서", "message": "메시지",
}
CAT_ORDER = ["유류·ESG", "안전·작업허가", "마스터·SQL", "자동수집", "문서", "메시지"]
CAT_COLOR = {
    "유류·ESG": "#0A5AD4", "안전·작업허가": "#12A150", "마스터·SQL": "#6D5AE0",
    "자동수집": "#0EA5B7", "문서": "#C77700", "메시지": "#DC2626",
}
# 카테고리 → 반영할 원본 그룹(table_actions.RUN_GROUPS 키). 사이드바 카테고리 행의 ↻ 버튼용.
# 카테고리를 콕 집어 누른 것은 의도적 실행으로 보고 **LLM 그룹까지 포함**한다.
# (전체 반영은 반대로 in_all=True 인 빠른 그룹만 돈다 — run_all 참조)
CAT_GROUPS: dict[str, list[str]] = {
    "유류·ESG": ["esg"],
    "안전·작업허가": ["ptw", "out"],
    "마스터·SQL": ["db"],
    "자동수집": ["weather", "date", "mapping"],
    "문서": ["docs"],
    "메시지": [],          # 직접 입력 — 반영 대상 없음(버튼 미표시)
}

# 원본 없는 자동 생성 데이터(입력 폴더 대신 소스 라벨 표시 / 상태 '자동')
NO_SOURCE = {
    "weather": "기상청 API", "mapping": "LLM 생성", "date": "자동 생성",
    "pjtlist": "MySQL shipinfo", "milestone": "MySQL pjtevnt", "shipbbs": "MySQL shipbbs",
    "message": "직접 입력",
}
# parquet → 기본 입력 폴더(섹션)
DEFAULT_FOLDER = {
    "trial_schedule": "esg", "fuel_usage": "esg", "fuel_price": "esg", "lng_usage": "esg",
    "fuel_plan": "esg", "pjtmethod": "esg", "ptwlist": "ptw", "ptwlist_archive": "ptw",
    "out": "out", "ra": "out", "accident": "accident", "guide": "guide",
}
# 대형 운영테이블은 상위 N행만 미리보기
PREVIEW_LIMIT = 200
LIMITED_PREVIEW = {"ptwlist", "ptwlist_archive", "out", "ra", "shipbbs", "milestone"}
PJT_COLS = {"PJT", "호선", "project", "PROJECT", "HULLNO"}


# ── 데이터 헬퍼 ──────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def _read_parquet(path_str: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path_str)


def load_parquet(path: Path) -> pd.DataFrame:
    try:
        mt = path.stat().st_mtime
    except OSError:
        return pd.DataFrame()
    return _read_parquet(str(path), mt)


def _sources() -> dict:
    try:
        p = pc.get_parquet_dir() / "_parquet_sources.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"folders": {}, "custom": []}


def get_folder(key: str) -> str:
    return _sources().get("folders", {}).get(key) or DEFAULT_FOLDER.get(key, "")


def _folder_dir(folder: str) -> "Path | None":
    if not folder:
        return None
    if folder in ("esg", "ptw", "out", "accident", "guide"):
        return pc.get_upload_dir(folder)
    if folder == "sql":
        return pc.get_sql_dir()
    return Path(folder)


def _list_names(d) -> list[str]:
    if d is None:
        return []
    try:
        return [n for n in os.listdir(d) if not n.startswith("_")]
    except Exception:
        return []


def source_files(key: str) -> tuple[int, str]:
    """(원본 파일 수, 최신 파일명)."""
    names = _list_names(_folder_dir(get_folder(key)))
    if not names:
        return 0, ""
    return len(names), sorted(names)[-1]


def recency(path: Path) -> tuple[str, str]:
    """(new|mid|old|none, 표시라벨)."""
    if not path.exists():
        return "none", "없음"
    d = datetime.fromtimestamp(path.stat().st_mtime)
    days = (date.today() - d.date()).days
    label = ("오늘 " + d.strftime("%H:%M")) if days == 0 else (
        "어제" if days == 1 else f"{days}일 전")
    cls = "new" if days <= 1 else ("mid" if days <= 7 else "old")
    return cls, label


def dataset_meta(key: str) -> dict:
    """카탈로그 한 행에 필요한 메타 계산."""
    p = pc.get_parquet_dir() / f"{key}.parquet"
    df = load_parquet(p)
    rec, rec_label = recency(p)
    exists = p.exists() and not df.empty
    pjt = 0
    if exists:
        col = next((c for c in df.columns if c in PJT_COLS), None)
        if col:
            pjt = df[col].astype(str).nunique()
    if not exists:
        status = "empty"
    elif key in NO_SOURCE and key not in ("accident", "guide"):
        status = "auto"
    else:
        status = "saved"
    return {
        "key": key, "cat": CATEGORY.get(key, "기타"),
        "rows": len(df), "cols": df.shape[1] if exists else 0, "pjt": pjt,
        "rec": rec, "rec_label": rec_label, "status": status, "exists": exists,
    }


# ── CSS ──────────────────────────────────────────────────────────────────────
_CSS = """
<style>
:root{--navy:#0B1F3A;--blue:#0A5AD4;--bg:#EEF2F7;--line:#E5E9F0;--ink:#0F172A;--mut:#64748B;--faint:#94A3B8;}
.stApp{background:var(--bg);}
.block-container{padding-top:1.1rem !important;padding-bottom:1.2rem;max-width:100% !important;}
header[data-testid="stHeader"]{background:transparent;height:0;}
/* 사이드바 = 네이비 내비 */
section[data-testid="stSidebar"]{background:var(--navy) !important;width:250px !important;}
section[data-testid="stSidebar"] *{color:#CBD5E1;}
section[data-testid="stSidebar"] .stButton>button{width:100%;text-align:left;justify-content:flex-start;
  background:transparent;border:none;color:#B7C4D8;font-size:13.5px;font-weight:600;padding:9px 12px;border-radius:8px;}
section[data-testid="stSidebar"] .stButton>button:hover{background:rgba(255,255,255,.07);color:#fff;}
.cv-brand{display:flex;align-items:center;gap:9px;padding:6px 6px 14px;}
.cv-logo{width:31px;height:31px;border-radius:8px;background:linear-gradient(135deg,#0A5AD4,#1E88FF);
  display:flex;align-items:center;justify-content:center;font-size:15px;}
.cv-brand .t{color:#fff;font-size:15px;font-weight:800;} .cv-brand .s{color:#7E93B0;font-size:10.5px;}
.cv-env{display:inline-flex;align-items:center;gap:6px;background:rgba(18,161,80,.16);border:1px solid rgba(18,161,80,.4);
  color:#4ADE80 !important;font-size:11px;font-weight:600;padding:4px 10px;border-radius:20px;margin:2px 0 6px;}
.cv-env i{width:6px;height:6px;border-radius:50%;background:#4ADE80;display:inline-block;}
.cv-navlbl{font-size:10px;font-weight:700;letter-spacing:.12em;color:#5B7194;padding:10px 12px 4px;}
.cv-nav{display:flex;align-items:center;justify-content:space-between;padding:9px 12px;border-radius:8px;
  font-size:13px;font-weight:600;color:#B7C4D8;text-decoration:none;margin:1px 4px;}
.cv-nav:hover{background:rgba(255,255,255,.07);color:#fff;}
.cv-nav.on{background:rgba(10,90,212,.30);color:#fff;font-weight:700;}
.cv-nav .l{display:flex;align-items:center;gap:9px;}
.cv-nav .dot{width:8px;height:8px;border-radius:2px;} .cv-nav .n{font-size:11px;color:#5B7194;}
.cv-nav.on .n{color:#B7C4D8;}
/* 카테고리 행 = 내비 링크 + 반영(↻) 앵커. <a> 안에 <a> 를 넣을 수 없어 형제로 배치한다. */
.cv-navrow{display:flex;align-items:center;gap:4px;}
.cv-navrow .cv-nav{flex:1;min-width:0;}
.cv-apply{flex:none;display:inline-flex;align-items:center;justify-content:center;
  width:26px;height:26px;border-radius:7px;font-size:13px;font-weight:700;text-decoration:none;
  color:#8FA3BF;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.10);}
.cv-apply:hover{background:var(--blue);color:#fff;border-color:var(--blue);}
.cv-apply.llm{color:#E0A34E;border-color:rgba(224,163,78,.35);}
.cv-apply.llm:hover{background:#C77700;color:#fff;border-color:#C77700;}
/* 헤더 */
.cv-htitle{font-size:20px;font-weight:800;color:var(--ink);letter-spacing:-.02em;}
.cv-hsub{font-size:12.5px;color:var(--mut);margin-top:2px;}
/* 필터 칩 (라디오/멀티셀렉트 위 라벨) */
.cv-flabel{font-size:11.5px;color:var(--faint);font-weight:700;margin:6px 0 2px;}
/* 통합 표 */
.cv-table{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;}
.cv-th,.cv-tr{display:grid;grid-template-columns:2.5fr 1.15fr .8fr .55fr .7fr 1.3fr .85fr .95fr;align-items:center;gap:8px;padding:11px 20px;}
.cv-th{background:#EFF3F8;border-bottom:1px solid #E0E6EF;font-size:11px;font-weight:700;color:var(--mut);letter-spacing:.03em;}
.cv-tr{border-bottom:1px solid #EDF1F6;}
.cv-tr:hover{background:#F6F9FE;}
/* 행의 각 셀 = 상세로 가는 앵커. 스타일은 안쪽 span 이 갖고, 여기선 링크 티만 없앤다.
   (행 전체를 <a> 로 감싸면 실행 열의 <a> 가 중첩돼 무효 HTML 이 된다) */
.cv-cell{display:block;min-width:0;text-decoration:none;color:inherit;}
/* 실행 열 */
.cv-run{display:inline-block;font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:7px;
  text-decoration:none;white-space:nowrap;color:var(--blue);background:#EEF4FF;border:1px solid #CFE0FA;}
.cv-run:hover{background:var(--blue);color:#fff;border-color:var(--blue);}
.cv-run-menu{color:#64748B;background:#F1F5F9;border-color:#E2E8F0;}
.cv-run-menu:hover{background:#64748B;color:#fff;border-color:#64748B;}
.cv-run-off{font-size:12.5px;color:#CBD5E1;}
.cv-nm{font-weight:700;font-size:13.5px;color:var(--ink);}
.cv-file{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;color:var(--faint);}
.cv-cat{justify-self:start;font-size:11.5px;font-weight:600;color:#475569;background:#EEF2F7;padding:3px 10px;border-radius:6px;}
.cv-num{font-size:13px;color:#334155;font-weight:600;} .cv-dim{font-size:13px;color:var(--mut);}
.cv-chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;padding:4px 10px;border-radius:7px;}
.cv-chip i{width:6px;height:6px;border-radius:50%;}
.rc-new{color:#12A150;background:#E7F7EE;} .rc-new i{background:#12A150;}
.rc-mid{color:#0A5AD4;background:#E8F0FE;} .rc-mid i{background:#0A5AD4;}
.rc-old{color:#C77700;background:#FEF3C7;} .rc-old i{background:#C77700;}
.rc-none{color:#94A3B8;background:#F1F5F9;} .rc-none i{background:#CBD5E1;}
.st-saved{color:#12A150;background:#E7F7EE;} .st-auto{color:#6D5AE0;background:#EDEAFB;}
.st-warn{color:#C77700;background:#FEF3C7;} .st-empty{color:#94A3B8;background:#F1F5F9;}
.cv-badge{font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:7px;}
/* 상세 */
.cv-crumb{font-size:12.5px;color:var(--mut);margin-bottom:10px;}
.cv-crumb a{color:var(--blue);font-weight:600;text-decoration:none;}
.cv-dhead{background:#fff;border:1px solid var(--line);border-radius:12px;padding:13px 18px;margin-bottom:12px;}
.cv-dtitle{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.cv-dtitle .nm{font-size:18px;font-weight:800;color:var(--ink);}
.cv-dtitle .f{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--faint);}
.cv-meta{display:flex;gap:24px;flex-wrap:wrap;margin-top:11px;align-items:flex-end;}
.cv-meta .k{font-size:11px;color:var(--faint);font-weight:600;display:block;margin-bottom:4px;}
.cv-meta .v{font-size:12.5px;color:#334155;font-weight:600;}
.cv-fold{font-family:ui-monospace,Menlo,monospace;font-size:12px;background:#EEF4FF;color:var(--blue);
  border:1px solid #CFE0FA;border-radius:6px;padding:3px 10px;}
.cv-src{font-size:12.5px;color:var(--faint);font-style:italic;}
/* 상세 데이터 표 */
.cv-colstrip{padding:10px 18px;font-size:12px;color:var(--mut);border-bottom:1px solid #EDF1F6;}
.cv-colstrip b{color:var(--ink);}
.cv-dtbl{overflow:auto;max-height:60vh;}
table.cv-data{width:100%;border-collapse:collapse;font-size:12.5px;white-space:nowrap;}
table.cv-data th{position:sticky;top:0;background:#EFF3F8;text-align:left;padding:9px 14px;font-weight:700;
  color:#334155;border-bottom:2px solid #E0E6EF;font-size:11.5px;}
table.cv-data td{padding:8px 14px;border-bottom:1px solid #F1F5F9;color:#334155;max-width:280px;
  overflow:hidden;text-overflow:ellipsis;}
table.cv-data td.hl{color:var(--blue);font-weight:700;font-family:ui-monospace,Menlo,monospace;}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


# ── 사이드바 ──────────────────────────────────────────────────────────────────
def _apply_anchor(c: str) -> str:
    """카테고리 행의 ↻ 반영 앵커. 대상 그룹이 없으면(메시지) 빈 문자열.

    카탈로그 행의 cv-run 과 같은 방식(쿼리파라미터 앵커) — render_app 이 ?apply= 를 처리한다.
    """
    gkeys = CAT_GROUPS.get(c) or []
    if not gkeys:
        return ""
    labels = [ta.RUN_GROUPS[g]["label"] for g in gkeys if g in ta.RUN_GROUPS]
    has_llm = any(not ta.RUN_GROUPS[g]["in_all"] for g in gkeys if g in ta.RUN_GROUPS)
    tip = f"{c} 반영 — {' + '.join(labels)}"
    if has_llm:
        tip += " · LLM 포함(오래 걸릴 수 있음)"
    return (f'<a class="cv-apply{" llm" if has_llm else ""}" href="?cat={c}&apply={c}" '
            f'target="_self" title="{_esc(tip)}">↻</a>')


def _exec_all() -> None:
    """전체 반영 실행 — 결과를 세션에 담고 rerun.

    전역 st.cache_data.clear() 는 호출하지 않는다(LLM 캐시까지 날아간다).
    _read_parquet 가 mtime 을 캐시 키로 쓰므로 저장 즉시 자동 무효화된다.
    """
    bar = st.sidebar.progress(0.0, text="전체 반영 준비 중...")

    def _cb(done: int, total: int, label: str) -> None:
        bar.progress(done / total if total else 1.0, text=f"[{done}/{total}] {label}")

    res = ta.run_all(progress_cb=_cb)
    bar.empty()
    st.session_state["cv_group_result"] = {"title": "전체 반영", "res": res}
    st.session_state["cv_screen"] = "catalog"
    st.rerun()


def render_sidebar(handlers: dict) -> None:
    env = "🟢 사내망 · NAS 연결됨"   # data_manager_F: 사내망 고정
    cat = st.session_state.get("cv_cat", "전체")
    screen = st.session_state.get("cv_screen", "catalog")
    with st.sidebar:
        st.markdown(
            f'<div class="cv-brand"><div class="cv-logo">📦</div>'
            f'<div><div class="t">Data Manager</div><div class="s">조선소 시운전팀</div></div></div>'
            f'<div class="cv-env"><i></i>{_esc(env)}</div>', unsafe_allow_html=True)

        # 데이터 카테고리 (앵커 링크 = ?cat=)
        counts = {c: 0 for c in CAT_ORDER}
        for _, k in DASH_PARQUETS:
            counts[CATEGORY.get(k, "")] = counts.get(CATEGORY.get(k, ""), 0) + 1
        st.markdown('<div class="cv-navlbl">전체</div>', unsafe_allow_html=True)
        on = " on" if (screen == "catalog" and cat == "전체") else ""
        st.markdown(f'<a class="cv-nav{on}" href="?cat=전체" target="_self">'
                    f'<span class="l">▦ 전체 데이터</span>'
                    f'<span class="n">{len(DASH_PARQUETS)}</span></a>',
                    unsafe_allow_html=True)
        st.markdown('<div class="cv-navlbl">카테고리</div>', unsafe_allow_html=True)
        for c in CAT_ORDER:
            on = " on" if (screen == "catalog" and cat == c) else ""
            nav = (f'<a class="cv-nav{on}" href="?cat={c}" target="_self">'
                   f'<span class="l"><span class="dot" style="background:{CAT_COLOR[c]}"></span>'
                   f'{_esc(c)}</span><span class="n">{counts[c]}</span></a>')
            st.markdown(f'<div class="cv-navrow">{nav}{_apply_anchor(c)}</div>',
                        unsafe_allow_html=True)

        # 반영 (원본 그룹별 변환 실행) — 전체는 바깥, 그룹별은 접어둔다
        n_all = sum(1 for g in ta.GROUP_ORDER if ta.RUN_GROUPS[g]["in_all"])
        if st.button("↻ 전체 반영", key="cv_run_all", type="primary",
                     use_container_width=True,
                     help=f"원본 {n_all}종을 순서대로 반영한다(빠른 것만). "
                          f"LLM 전용(문서 PDF·매핑 생성)은 제외 — 해당 카테고리의 ↻ 로 실행."):
            _exec_all()

        # 기능
        st.markdown('<div class="cv-navlbl">기능</div>', unsafe_allow_html=True)
        func_items = [("convert", "↻ 데이터 변환"), ("add", "＋ parquet 추가"),
                      ("doc", "📄 문서 파싱"), ("message", "💬 안전메시지")]
        for fk, flabel in func_items:
            if fk in handlers and st.button(flabel, key=f"cv_func_{fk}"):
                st.session_state["cv_screen"] = f"func:{fk}"
                st.session_state.pop("cv_sec", None)   # 메뉴 직접 진입 시엔 항상 전체 탭
                st.query_params.clear()
                st.rerun()


# ── 필터바 ────────────────────────────────────────────────────────────────────
def _filter_bar() -> dict:
    c1, c2, c3, c4, c5 = st.columns([1.1, 1.1, 2.2, 1.2, 2.0])
    with c1:
        pjt = st.selectbox("호선", ["전체 호선", "PJT 있는 데이터만"], key="cv_pjt",
                           label_visibility="collapsed")
    with c2:
        period = st.selectbox("기간", ["전체 기간", "오늘", "최근 7일", "최근 30일"],
                              key="cv_period", label_visibility="collapsed")
    with c3:
        rec = st.multiselect("최신도", ["오늘/어제", "최근(7일)", "오래됨"], key="cv_rec",
                             placeholder="최신도 필터", label_visibility="collapsed")
    with c4:
        check = st.toggle("점검 필요만", key="cv_check")
    with c5:
        q = st.text_input("검색", key="cv_q", placeholder="🔍 데이터명·파일 검색",
                          label_visibility="collapsed")
    rec_map = {"오늘/어제": "new", "최근(7일)": "mid", "오래됨": "old"}
    return {"pjt": pjt, "period": period, "rec": {rec_map[r] for r in rec},
            "check": check, "q": (q or "").strip().lower()}


def _passes(meta: dict, label: str, f: dict) -> bool:
    if f["pjt"] == "PJT 있는 데이터만" and meta["pjt"] <= 0:
        return False
    if f["rec"] and meta["rec"] not in f["rec"]:
        return False
    if f["period"] != "전체 기간":
        lim = {"오늘": 0, "최근 7일": 7, "최근 30일": 30}[f["period"]]
        # rec class 근사: new<=1, mid<=7, old>7
        approx = {"new": 1, "mid": 7, "old": 999, "none": 9999}[meta["rec"]]
        if approx > max(lim, 1) and not (lim == 0 and meta["rec"] == "new"):
            if lim < approx:
                return False
    if f["check"] and meta["status"] not in ("warn", "empty"):
        return False
    if f["q"] and f["q"] not in (label + meta["key"] + meta["cat"]).lower():
        return False
    return True


# ── 카탈로그 ──────────────────────────────────────────────────────────────────
_REC_CLS = {"new": "rc-new", "mid": "rc-mid", "old": "rc-old", "none": "rc-none"}
_ST = {"saved": ("st-saved", "정상"), "auto": ("st-auto", "자동"),
       "warn": ("st-warn", "점검"), "empty": ("st-empty", "없음")}


def _run_cell(key: str, cat: str) -> str:
    """행의 '실행' 셀 HTML.

    RUN_SINGLE = 딸깍 즉시 변환(?run=), RUN_MENU = 변환 화면 해당 섹션으로(?goto=).
    """
    if key in ta.RUN_SINGLE:
        return f'<a class="cv-run" href="?cat={cat}&run={key}" target="_self">↻ 변환</a>'
    sec = ta.RUN_MENU.get(key)
    if sec:
        return (f'<a class="cv-run cv-run-menu" href="?cat={cat}&goto=convert&sec={sec}" '
                f'target="_self">↻ 변환 화면</a>')
    return '<span class="cv-run-off">—</span>'


def _render_run_result(key: str, results: list[dict]) -> None:
    """변환 결과 배너. results 는 table_actions.run() 반환값."""
    label = next((l for l, k in DASH_PARQUETS if k == key), key)
    main = next((r for r in results if r.get("name") == key), None) or \
           (results[-1] if results else {})
    ok = bool(main.get("ok"))
    (st.success if ok else st.error)(f"{label} — {main.get('msg', '')}")
    if len(results) > 1:
        with st.expander("변환 로그", expanded=not ok):
            st.code("\n".join(
                f"{r.get('name')}: ok={r.get('ok')} rows={r.get('rows')} {r.get('msg', '')}"
                for r in results), language="text")


def _render_group_result(title: str, results: list[dict]) -> None:
    """반영 결과 배너 (그룹/전체). results 는 run_group()/run_all() 반환값."""
    ok_n = sum(1 for r in results if r.get("ok"))
    skip_n = sum(1 for r in results if not r.get("ok") and r.get("skipped"))
    fail_n = len(results) - ok_n - skip_n
    # 원본이 없어 건너뛴 것은 실패가 아니다 — 매일 올리지 않는 원본 때문에
    # 전체 반영이 늘 경고로 끝나지 않게 따로 센다.
    msg = f"{title} — 성공 {ok_n} / 실패 {fail_n}" + (f" / 건너뜀 {skip_n}(원본 없음)" if skip_n else "")
    (st.success if fail_n == 0 else st.warning)(msg)
    if results:
        with st.expander("반영 로그", expanded=fail_n > 0):
            st.code("\n".join(
                f"{'OK  ' if r.get('ok') else ('SKIP' if r.get('skipped') else 'FAIL')} {r.get('name')}: "
                f"rows={r.get('rows')} {r.get('msg', '')}"
                for r in results), language="text")


def render_catalog(cat: str, f: dict) -> None:
    items = [(l, k) for l, k in DASH_PARQUETS if cat == "전체" or CATEGORY.get(k) == cat]
    metas = [(l, dataset_meta(k)) for l, k in items]
    shown = [(l, m) for l, m in metas if _passes(m, l, f)]

    title = "전체 데이터" if cat == "전체" else cat
    st.markdown(f'<div class="cv-htitle">{_esc(title)}</div>'
                f'<div class="cv-hsub">{len(shown)}종 표시 · 전체 {len(DASH_PARQUETS)}종</div>',
                unsafe_allow_html=True)

    # 직전 ?run= 변환 결과 (1회 표시)
    res = st.session_state.pop("cv_run_result", None)
    if res:
        _render_run_result(res["key"], res["res"])

    rows = ['<div class="cv-table">',
            '<div class="cv-th"><span>데이터</span><span>카테고리</span><span>행수</span>'
            '<span>컬럼</span><span>호선</span><span>최근 업데이트</span><span>상태</span>'
            '<span>실행</span></div>']
    for label, m in shown:
        rc = _REC_CLS.get(m["rec"], "rc-none")
        stc, stl = _ST.get(m["status"], _ST["empty"])
        href = f'?cat={cat}&pq={m["key"]}'
        rows.append(
            f'<div class="cv-tr">'
            f'<a class="cv-cell" href="{href}" target="_self">'
            f'<span class="cv-nm">{_esc(label)}</span><br>'
            f'<span class="cv-file">{m["key"]}.parquet</span></a>'
            f'<a class="cv-cell" href="{href}" target="_self"><span class="cv-cat">{_esc(m["cat"])}</span></a>'
            f'<a class="cv-cell" href="{href}" target="_self"><span class="cv-num">{m["rows"]:,}</span></a>'
            f'<a class="cv-cell" href="{href}" target="_self"><span class="cv-dim">{m["cols"] or "—"}</span></a>'
            f'<a class="cv-cell" href="{href}" target="_self">'
            f'<span class="cv-dim">{(str(m["pjt"]) + "척") if m["pjt"] else "—"}</span></a>'
            f'<a class="cv-cell" href="{href}" target="_self">'
            f'<span class="cv-chip {rc}"><i></i>{_esc(m["rec_label"])}</span></a>'
            f'<a class="cv-cell" href="{href}" target="_self">'
            f'<span class="cv-badge {stc}">{stl}</span></a>'
            f'<span>{_run_cell(m["key"], cat)}</span>'
            f'</div>')
    rows.append("</div>")
    if not shown:
        rows = ['<div style="padding:60px;text-align:center;color:#94A3B8;">'
                '조건에 맞는 데이터가 없습니다. 필터를 조정해 보세요.</div>']
    st.markdown("".join(rows), unsafe_allow_html=True)


# ── 상세 ──────────────────────────────────────────────────────────────────────
def render_detail(key: str, label: str, cat: str, handlers: dict) -> None:
    p = pc.get_parquet_dir() / f"{key}.parquet"
    df = load_parquet(p)
    m = dataset_meta(key)
    rc = _REC_CLS.get(m["rec"], "rc-none")
    stc, stl = _ST.get(m["status"], _ST["empty"])

    st.markdown(f'<div class="cv-crumb"><a href="?cat={cat}" target="_self">← 전체 데이터</a> '
                f'&nbsp;/&nbsp; <b>{_esc(label)}</b></div>', unsafe_allow_html=True)

    # 입력 폴더 / 소스
    if key in NO_SOURCE:
        fold_html = f'<span class="cv-src">— {_esc(NO_SOURCE[key])} —</span>'
        src_html = '<span class="cv-src">—</span>'
    else:
        fold_html = f'<span class="cv-fold">📁 {_esc(get_folder(key) or "—")}</span>'
        n, latest = source_files(key)
        src_html = (f'<span class="v">{n}개</span> '
                    f'<span class="cv-src">(최신: {_esc(latest)})</span>') if n else '<span class="cv-src">—</span>'

    st.markdown(
        f'<div class="cv-dhead"><div class="cv-dtitle">'
        f'<span class="nm">{_esc(label)}</span><span class="f">{key}.parquet</span>'
        f'<span class="cv-badge {stc}">{stl}</span></div>'
        f'<div class="cv-meta">'
        f'<div><span class="k">입력 폴더</span>{fold_html}</div>'
        f'<div><span class="k">원본 파일</span>{src_html}</div>'
        f'<div><span class="k">업로드 일자</span><span class="cv-chip {rc}"><i></i>{_esc(m["rec_label"])}</span></div>'
        f'<div><span class="k">행수 / 컬럼</span><span class="v">{m["rows"]:,}행 · {m["cols"]}컬럼</span></div>'
        f'</div></div>', unsafe_allow_html=True)

    # 컨텍스트 액션
    _detail_actions(key, handlers)

    # 직전 변환 결과 (1회 표시)
    res = st.session_state.pop("cv_run_result", None)
    if res:
        _render_run_result(res["key"], res["res"])

    # 데이터 표
    if df.empty:
        st.info("parquet 없음 또는 빈 데이터")
        return
    cols = list(df.columns)
    limited = key in LIMITED_PREVIEW
    max_rows = PREVIEW_LIMIT if limited else len(df)
    rows_label = (f"상위 {min(len(df), PREVIEW_LIMIT):,}행 (전체 {len(df):,}행)"
                  if limited else f"전체 {len(df):,}행")
    colstrip = (f'<div class="cv-colstrip"><b>컬럼 {len(cols)}개</b> · '
                f'{_esc(", ".join(map(str, cols)))} · {rows_label} (스크롤)</div>')
    th = "<tr>" + "".join(f"<th>{_esc(c)}</th>" for c in cols) + "</tr>"
    disp = mask_df_for_display(df.head(max_rows))   # 개인정보 컬럼(name/phone/greeter 등) 표시 마스킹
    body = []
    for _, r in disp.iterrows():
        body.append("<tr>" + "".join(
            (f'<td class="hl">{_esc(r[c])[:80]}</td>' if ci == 0 else f'<td>{_esc(r[c])[:80]}</td>')
            for ci, c in enumerate(cols)) + "</tr>")
    st.markdown(f'<div class="cv-table">{colstrip}'
                f'<div class="cv-dtbl"><table class="cv-data">{th}{"".join(body)}</table></div></div>',
                unsafe_allow_html=True)


def _detail_actions(key: str, handlers: dict) -> None:
    """데이터별 컨텍스트 액션 — table_actions 레지스트리 + 보조 액션."""
    entry = ta.RUN_SINGLE.get(key)
    if entry:
        if st.button(entry["label"], key=f"cv_run_{key}", type="primary", help=entry["help"]):
            with st.spinner("변환 중..."):
                res = ta.run(key)
            st.session_state["cv_run_result"] = {"key": key, "res": res}
            st.rerun()      # 아래 데이터 표를 갱신본으로 다시 그린다
        _detail_extra(key)
        return

    sec = ta.RUN_MENU.get(key)
    if sec:
        st.caption(f'{ta.MENU_REASON.get(sec, "")} 변환 화면에서 진행하세요.')
        if st.button(f"↻ {sec} 변환 화면으로", key=f"cv_goto_{key}"):
            st.session_state["cv_screen"] = "func:convert"
            st.session_state["cv_sec"] = sec
            st.query_params.clear()
            st.rerun()
        return

    if key == "ptwlist_archive":
        st.caption("현재 아카이브 분리 로직이 없어 생성되지 않는 테이블입니다 "
                   "(tbm_converter.ARCHIVE_DAYS 상수만 존재).")
    elif key in ("accident", "guide") and "doc" in handlers:
        st.caption("문서 파싱은 좌측 '📄 문서 파싱' 화면에서 진행하세요.")
    elif key == "message" and "message" in handlers:
        st.caption("안전메시지 입력은 좌측 '💬 안전메시지' 화면에서 진행하세요.")


def _detail_extra(key: str) -> None:
    """주 변환 버튼 외 보조 액션 (기존 기능 유지).

    pjtlist/milestone/shipbbs 는 각 상세의 개별 재생성 버튼(RUN_SINGLE)으로 충분하고,
    3종 일괄 재생성은 'DB·날짜' 변환 화면에 있으므로 여기서는 중복 노출하지 않는다.
    """
    if key == "ptwlist":
        if st.button("🤖 위험요소(키워드/경고) 재생성", key="cv_regen"):
            try:
                import ptw_enrich
                res = ptw_enrich.regenerate_keywords(pc.get_parquet_dir())
                (st.success if res.get("ok") else st.error)(res.get("msg", ""))
            except Exception as e:
                st.error(f"실패: {e}")


# ── 라우터 ────────────────────────────────────────────────────────────────────
def render_app(handlers: dict | None = None) -> None:
    handlers = handlers or {}
    qp = st.query_params

    # 테이블 단위 변환 실행 (?run=키) — 실행 직후 파라미터를 지워 새로고침 재실행을 막는다
    if "run" in qp:
        rkey = qp.get("run")
        rcat = qp.get("cat") or st.session_state.get("cv_cat", "전체")
        if rkey in ta.RUN_SINGLE:
            rlabel = next((l for l, k in DASH_PARQUETS if k == rkey), rkey)
            with st.spinner(f"{rlabel} 변환 중..."):
                st.session_state["cv_run_result"] = {"key": rkey, "res": ta.run(rkey)}
        st.session_state["cv_cat"] = rcat
        st.session_state["cv_screen"] = "catalog"
        st.query_params.clear()
        st.query_params["cat"] = rcat
        st.rerun()

    # 카테고리 반영 실행 (?apply=카테고리) — 그 카테고리의 원본 그룹을 모두 돌린다.
    # 전체 반영과 달리 LLM 그룹도 포함한다(카테고리를 직접 누른 것 = 의도적 실행).
    if "apply" in qp:
        ckey = qp.get("apply")
        acat = qp.get("cat") or st.session_state.get("cv_cat", "전체")
        gkeys = CAT_GROUPS.get(ckey) or []
        if gkeys:
            with st.spinner(f"{ckey} 반영 중..."):
                st.session_state["cv_group_result"] = {
                    "title": f"{ckey} 반영", "res": ta.run_groups(gkeys)}
        st.session_state["cv_cat"] = acat
        st.session_state["cv_screen"] = "catalog"
        st.query_params.clear()
        st.query_params["cat"] = acat
        st.rerun()

    # 기능 화면 이동 (?goto=convert&sec=ESG) — 원본 1개 → parquet 여러 개인 변환
    if "goto" in qp:
        st.session_state["cv_screen"] = f'func:{qp.get("goto")}'
        st.session_state["cv_sec"] = qp.get("sec")
        st.query_params.clear()
        st.rerun()

    # 카테고리 앵커 처리
    if "cat" in qp:
        st.session_state["cv_cat"] = qp.get("cat")
        st.session_state["cv_screen"] = "catalog"
    cat = st.session_state.get("cv_cat", "전체")
    screen = st.session_state.get("cv_screen", "catalog")

    render_sidebar(handlers)

    # 사이드바 반영 결과 (1회 표시)
    gres = st.session_state.pop("cv_group_result", None)
    if gres:
        _render_group_result(gres["title"], gres["res"])

    # 기능 화면
    if screen.startswith("func:"):
        fk = screen.split(":", 1)[1]
        st.markdown(f'<div class="cv-htitle">{_esc(dict(convert="데이터 변환", add="parquet 추가", doc="문서 파싱", message="안전메시지").get(fk, fk))}</div>', unsafe_allow_html=True)
        st.markdown("")
        fn = handlers.get(fk)
        if fn:
            fn()
        return

    # 상세 or 카탈로그
    sel = qp.get("pq")
    if sel and sel in CATEGORY:
        label = next((l for l, k in DASH_PARQUETS if k == sel), sel)
        # 상세 상단에도 필터바 유지 X — 목록으로 돌아가면 다시 표시
        render_detail(sel, label, cat, handlers)
    else:
        f = _filter_bar()
        render_catalog(cat, f)
