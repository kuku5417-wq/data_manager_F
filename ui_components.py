"""ui_components.py — 카드·신호등·테이블 컴포넌트"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from pii import mask_df_for_display   # 개인정보 컬럼 표시 마스킹

ESG_KEYS = ["trial_schedule", "fuel_usage", "fuel_price", "lng_usage", "fuel_plan", "pjtmethod"]


def esc_html(v) -> str:
    """HTML 이스케이프 (unsafe_allow_html 마크업용 공용 헬퍼)."""
    s = "" if v is None else str(v)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── 신호등 분석 ───────────────────────────────────────────────────────────────

def analyze_diff(new_df: pd.DataFrame, existing_df: pd.DataFrame,
                 key_col: str = "PJT") -> dict:
    """새 업로드 vs 기존 parquet PJT 단위 차이 분석.

    Returns:
        added     : 신규 PJT 수 (🟢 추가)
        changed   : 겹치는 PJT 수 (🟡 교체 예정)
        kept      : 기존에만 있는 PJT 수 (⚪ 유지)
        new_rows  : 업로드 행수
        exist_rows: 기존 행수
    """
    if new_df.empty:
        return {"added": 0, "changed": 0, "kept": 0, "new_rows": 0, "exist_rows": len(existing_df)}

    new_pjts = set(new_df[key_col].astype(str).unique()) if key_col in new_df.columns else set()

    if existing_df.empty:
        return {"added": len(new_pjts), "changed": 0, "kept": 0,
                "new_rows": len(new_df), "exist_rows": 0}

    # 기존 컬럼 정규화 (호선→PJT 하위호환)
    ex = existing_df.copy()
    if "호선" in ex.columns and key_col not in ex.columns:
        ex = ex.rename(columns={"호선": key_col})
    exist_pjts = set(ex[key_col].astype(str).unique()) if key_col in ex.columns else set()

    return {
        "added":      len(new_pjts - exist_pjts),
        "changed":    len(new_pjts & exist_pjts),
        "kept":       len(exist_pjts - new_pjts),
        "new_rows":   len(new_df),
        "exist_rows": len(existing_df),
    }


# ── 카드 컴포넌트 ──────────────────────────────────────────────────────────────

SHEET_LABELS = {
    "trial_schedule": "시운전일정",
    "fuel_usage":     "사용량",
    "fuel_price":     "유류단가",
    "lng_usage":      "LNG",
    "fuel_plan":      "연간계획",
    "pjtmethod":      "공법",
    "ptwlist":        "PTW 리스트",
    "ptwlist_archive":"PTW 아카이브",
    "out":            "사외작업자",
    "ra":             "위험성평가",
    "pjtlist":        "호선 마스터",
    "milestone":      "마일스톤",
    "shipbbs":        "선박 게시판",
}

STATUS_ICON = {
    "pending": "🕐",
    "saved":   "✅",
    "error":   "❌",
    "empty":   "—",
}


def render_card(
    key: str,
    diff: dict,
    status: str,       # "pending"|"saved"|"error"|"empty"
    selected: bool,
    checked: bool,
) -> tuple[bool, bool]:
    """데이터 카드 렌더링.

    Returns: (clicked: bool, checked: bool)
    """
    label     = SHEET_LABELS.get(key, key)
    icon      = STATUS_ICON.get(status, "—")
    sel_class = "selected" if selected else ""

    # 신호등 배지 HTML
    badges = ""
    if diff.get("added", 0):
        badges += f'<span class="badge badge-green">🟢 +{diff["added"]} 신규</span>'
    if diff.get("changed", 0):
        badges += f'<span class="badge badge-amber">🟡 {diff["changed"]} 교체</span>'
    if diff.get("kept", 0):
        badges += f'<span class="badge badge-gray">⚪ {diff["kept"]} 유지</span>'
    if status == "saved":
        badges += f'<span class="badge badge-saved">✅ 저장됨</span>'
    if status == "error":
        badges += f'<span class="badge badge-error">❌ 오류</span>'
    if not badges and status == "empty":
        badges = '<span class="badge badge-gray">— 데이터 없음</span>'

    rows_text = f'{diff.get("new_rows", 0):,}행' if diff.get("new_rows") else "—"
    exist_text = f'기존 {diff.get("exist_rows", 0):,}행' if diff.get("exist_rows") else ""

    card_html = f"""
    <div class="data-card {sel_class}">
        <div class="card-title">{icon} {label}</div>
        <div class="card-meta">{rows_text} {exist_text}</div>
        <div class="card-badges">{badges}</div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    col_btn, col_chk = st.columns([4, 1])
    with col_btn:
        clicked = st.button("상세 보기", key=f"card_btn_{key}",
                            use_container_width=True)
    with col_chk:
        new_checked = st.checkbox("저장", value=checked, key=f"card_chk_{key}",
                                  disabled=(status == "empty"))

    return clicked, new_checked


# ── 신호등 + 미리보기 상세 뷰 ──────────────────────────────────────────────────

def render_diff_detail(
    key: str,
    new_df: pd.DataFrame,
    existing_path: Path,
    pjt_col: str = "PJT",
) -> None:
    """신호등 KPI + 미리보기 + 기존 parquet 뷰어"""
    label = SHEET_LABELS.get(key, key)

    # 기존 parquet 로드
    existing_df = pd.DataFrame()
    if existing_path.exists():
        try:
            existing_df = pd.read_parquet(existing_path)
            # 호선→PJT 정규화
            if "호선" in existing_df.columns and pjt_col not in existing_df.columns:
                existing_df = existing_df.rename(columns={"호선": pjt_col})
        except Exception:
            existing_df = pd.DataFrame()

    diff = analyze_diff(new_df, existing_df, pjt_col) if not new_df.empty else {}

    # ── 신호등 KPI ──────────────────────────────────────────────────────────
    st.markdown(f'<div class="section-title">📊 {label} — 변경 분석</div>',
                unsafe_allow_html=True)

    added   = diff.get("added",   0)
    changed = diff.get("changed", 0)
    kept    = diff.get("kept",    0)

    kpi_html = f"""
    <div class="signal-kpi-row">
      <div class="signal-kpi green" title="기존 parquet에 없는 신규 호선입니다. 새로 추가됩니다.">
        <div class="kpi-num">{added}</div>
        <div class="kpi-label">🟢 신규 PJT</div>
        <div class="kpi-tooltip">기존에 없음 → 추가</div>
      </div>
      <div class="signal-kpi amber" title="기존 parquet에 있고 이번 업로드에도 있는 호선입니다. 기존 데이터를 덮어씁니다.">
        <div class="kpi-num">{changed}</div>
        <div class="kpi-label">🟡 교체 PJT</div>
        <div class="kpi-tooltip">기존+신규 모두 있음 → 덮어씀</div>
      </div>
      <div class="signal-kpi gray" title="이번 업로드에 없는 기존 호선입니다. 변경 없이 그대로 유지됩니다.">
        <div class="kpi-num">{kept}</div>
        <div class="kpi-label">⚪ 유지 PJT</div>
        <div class="kpi-tooltip">이번 업로드에 없음 → 유지</div>
      </div>
    </div>
    """
    st.markdown(kpi_html, unsafe_allow_html=True)

    # ── 미리보기 테이블 (신규/교체 행 하이라이트) ─────────────────────────────
    if not new_df.empty:
        st.markdown('<div class="section-title">📋 미리보기 (업로드 데이터)</div>',
                    unsafe_allow_html=True)
        preview = mask_df_for_display(new_df.head(10))   # 개인정보 컬럼 표시 마스킹

        if pjt_col in new_df.columns and not existing_df.empty and pjt_col in existing_df.columns:
            exist_pjts = set(existing_df[pjt_col].astype(str).unique())
            new_pjts_set = set(new_df[pjt_col].astype(str).unique())

            def _row_color(row):
                pjt = str(row.get(pjt_col, ""))
                if pjt not in exist_pjts:
                    return ["background-color: #D1FAE5"] * len(row)  # 신규 🟢
                elif pjt in exist_pjts:
                    return ["background-color: #FEF9C3"] * len(row)  # 교체 🟡
                return [""] * len(row)

            try:
                styled = preview.style.apply(_row_color, axis=1)
                st.dataframe(styled, use_container_width=True, height=280)
            except Exception:
                st.dataframe(preview, use_container_width=True, height=280)
        else:
            st.dataframe(preview, use_container_width=True, height=280)

        st.caption(f"🟢 연두: 신규 PJT  🟡 노랑: 교체 PJT  (상위 10행 표시)")
    else:
        st.info("업로드된 데이터가 없습니다.")

    # ── 기존 parquet 뷰어 (토글) ─────────────────────────────────────────────
    st.markdown("")
    with st.expander(f"📂 현재 parquet 보기 — {key}.parquet", expanded=False):
        if existing_df.empty:
            st.info("저장된 parquet 없음")
        else:
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("전체 행수", f"{len(existing_df):,}")
            col_b.metric("컬럼 수", len(existing_df.columns))
            if pjt_col in existing_df.columns:
                col_c.metric("PJT 수", existing_df[pjt_col].nunique())
            st.dataframe(mask_df_for_display(existing_df.head(20)), use_container_width=True, height=300)


# ── 저장 결과 표시 ────────────────────────────────────────────────────────────

def render_save_result(results: list[dict]) -> None:
    """저장 결과 리스트 표시"""
    for r in results:
        if r.get("ok"):
            msg = r.get("msg", "") or f'{r.get("rows", 0):,}행'
            st.success(f"✅ **{r['name']}** — {msg}")
        else:
            st.error(f"❌ **{r['name']}** — {r.get('msg', '오류')}")


# ── 컬럼 매핑 뷰 (신규) ────────────────────────────────────────────────────────

def render_save_results_inline(save_results: dict, keys: list[str]) -> None:
    """저장 결과를 업로드 바 아래에 즉시 표시."""
    for key in keys:
        r = save_results.get(key)
        if not r:
            continue
        label = SHEET_LABELS.get(key, key)
        if r.get("simulated"):
            st.info(f"[시뮬] **{label}** — {r.get('msg', '')}")
        elif r.get("ok"):
            st.success(f"✅ **{label}** — {r.get('msg', '')}")
        else:
            st.error(f"❌ **{label}** — {r.get('msg', '저장 실패')}")


def render_esg_column_mapping(parsed: dict) -> None:
    """ESG 6개 시트를 st.tabs()로 표시. 좌: Excel 원본 컬럼 / 우: parquet 변환 컬럼."""
    if not parsed:
        st.info("파일을 업로드하면 컬럼 매핑이 여기에 표시됩니다.")
        return
    if parsed.get("_error"):
        st.error(f"파일 파싱 실패: {parsed['_error']}")
        return

    tab_labels = []
    for key in ESG_KEYS:
        df  = parsed.get(key, pd.DataFrame())
        err = (parsed.get("_sheet_errors") or {}).get(key, "")
        is_optional_missing = err and "선택적" in err
        if err and not is_optional_missing:
            icon = "❌"
        elif not df.empty:
            icon = "✅"
        else:
            icon = "—"
        rows_str = f" ({len(df):,}행)" if not df.empty else ""
        tab_labels.append(f"{icon} {SHEET_LABELS.get(key, key)}{rows_str}")

    tabs = st.tabs(tab_labels)

    for tab_ui, key in zip(tabs, ESG_KEYS):
        with tab_ui:
            df        = parsed.get(key, pd.DataFrame())
            err       = (parsed.get("_sheet_errors") or {}).get(key, "")
            raw_cols  = (parsed.get("_raw_cols") or {}).get(key, [])
            norm_cols = (parsed.get("_normalized_cols") or {}).get(key, [])
            is_optional_missing = err and "선택적" in err

            if err and not is_optional_missing:
                st.error(f"파싱 오류: {err}")
                continue
            if df.empty:
                msg = " (선택적 시트 — 없어도 무방)" if is_optional_missing else ""
                st.warning(f"데이터 없음{msg}")
                continue

            col_l, col_r = st.columns(2)
            with col_l:
                st.caption(f"**Excel 원본 컬럼** ({len(raw_cols)}개)")
                if raw_cols:
                    st.dataframe(
                        pd.DataFrame({"컬럼명": [str(c) for c in raw_cols]}),
                        use_container_width=True, hide_index=True,
                        height=min(len(raw_cols) * 35 + 38, 430),
                    )
            with col_r:
                st.caption(f"**parquet 저장 컬럼** ({len(norm_cols)}개)")
                if norm_cols:
                    rows = [{"컬럼명": c, "dtype": str(df[c].dtype)} for c in df.columns]
                    st.dataframe(
                        pd.DataFrame(rows),
                        use_container_width=True, hide_index=True,
                        height=min(len(rows) * 35 + 38, 430),
                    )


def render_tbm_column_mapping(parsed: dict) -> None:
    """TBM 컬럼 매핑 테이블 (표준컬럼 ↔ Excel컬럼) + DATE 확장 KPI."""
    if not parsed:
        st.info("파일을 업로드하면 컬럼 매핑이 여기에 표시됩니다.")
        return
    if parsed.get("_error"):
        st.error(f"파일 파싱 실패: {parsed['_error']}")
        return

    col_map = parsed.get("_col_map", {})
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.caption("**컬럼 매핑 현황**")
        rows = []
        for std_col, excel_col in col_map.items():
            rows.append({
                "표준 컬럼": std_col,
                "Excel 컬럼": excel_col if excel_col else "(없음)",
                "상태": "✅ 매핑됨" if excel_col else "⚠️ None 채움",
            })
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True, hide_index=True,
            height=min(len(rows) * 35 + 38, 540),
        )

        raw_df = parsed.get("_raw_df", pd.DataFrame())
        if not raw_df.empty:
            mapped_vals = {v for v in col_map.values() if v}
            unmapped = [c for c in raw_df.columns if c not in mapped_vals]
            if unmapped:
                with st.expander(f"❌ 매핑 안 된 Excel 컬럼 ({len(unmapped)}개) — 저장 시 제외"):
                    st.write(unmapped)

    with col_r:
        src      = parsed.get("_src_rows",      0)
        expanded = parsed.get("_expanded_rows",  0)
        active   = parsed.get("_active_rows",    0)
        archive  = parsed.get("_archive_rows",   0)

        st.caption("**DATE 확장 결과**")
        st.metric("원본 행수", f"{src:,}")
        st.metric("DATE 확장 후", f"{expanded:,}",
                  delta=f"+{expanded - src:,}" if expanded > src else None)
        if archive:
            st.metric("활성 (최근 14일)", f"{active:,}")
            st.metric("아카이브 (14일 초과)", f"{archive:,}")
        else:
            st.metric("활성", f"{active:,}")


def render_out_column_mapping(parsed: dict) -> None:
    """Out 컬럼 매핑 (Excel 한국어 → 영문) + RA 파생 요약."""
    if not parsed:
        st.info("파일을 업로드하면 컬럼 매핑이 여기에 표시됩니다.")
        return
    if parsed.get("_error"):
        st.error(f"파일 파싱 실패: {parsed['_error']}")
        return

    import out_converter as _oc

    col_l, col_r = st.columns(2)

    with col_l:
        st.caption("**out.parquet 컬럼 매핑**")
        map_rows = [{"Excel (한국어)": k, "parquet (영문)": v} for k, v in _oc.OUT_COL_MAP.items()]
        st.dataframe(pd.DataFrame(map_rows), use_container_width=True, hide_index=True)
        out_df = parsed.get("out", pd.DataFrame())
        if not out_df.empty:
            st.caption(f"결과: {len(out_df):,}행 / {len(out_df.columns)}컬럼")

    with col_r:
        st.caption("**ra.parquet 파생 규칙**")
        st.markdown(
            "- 방문기간 최대 **7일 단위** 분할\n"
            "- 최소 **2일** 미만 제외\n"
            "- **3년** 이내 데이터만 포함\n"
            "- 시운전/해운 관련 부서만 추출"
        )
        ra_df = parsed.get("ra", pd.DataFrame())
        if not ra_df.empty:
            comm = int(ra_df["is_commissioning"].sum()) if "is_commissioning" in ra_df.columns else 0
            st.metric("ra.parquet", f"{len(ra_df):,}행")
            st.metric("시운전 대상", f"{comm} / {len(ra_df)}")


def render_data_preview(
    tab: str,
    parsed: dict,
    keys: list[str] | None = None,
    labels: dict[str, str] | None = None,
) -> None:
    """하단 expander: 원본 5행 vs 변환 후 5행."""
    if not parsed:
        return
    _keys   = keys   or ESG_KEYS
    _labels = labels or SHEET_LABELS

    with st.expander("DATA PREVIEW — 원본 vs 변환 후 (각 5행)", expanded=False):
        if tab == "ESG":
            available = [k for k in _keys if not parsed.get(k, pd.DataFrame()).empty]
            if not available:
                st.info("파싱된 시트 없음")
                return
            sel = st.selectbox(
                "시트 선택", available,
                format_func=lambda k: _labels.get(k, k),
                key="preview_esg_sheet_sel",
            )
            col_l, col_r = st.columns(2)
            with col_l:
                st.caption("Excel 원본 (5행)")
                raw_prev = (parsed.get("_raw_preview") or {}).get(sel, pd.DataFrame())
                st.dataframe(mask_df_for_display(raw_prev.head(5)), use_container_width=True)
            with col_r:
                st.caption("변환 후 parquet (5행)")
                st.dataframe(mask_df_for_display(parsed.get(sel, pd.DataFrame()).head(5)), use_container_width=True)

        elif tab == "TBM":
            col_l, col_r = st.columns(2)
            with col_l:
                st.caption("Excel 원본 (5행)")
                st.dataframe(mask_df_for_display(parsed.get("_raw_df", pd.DataFrame()).head(5)), use_container_width=True)
            with col_r:
                st.caption("DATE 확장 후 (5행)")
                st.dataframe(mask_df_for_display(parsed.get("ptwlist", pd.DataFrame()).head(5)), use_container_width=True)

        elif tab == "Out":
            col_l, col_r = st.columns(2)
            with col_l:
                st.caption("out.parquet (5행)")
                st.dataframe(mask_df_for_display(parsed.get("out", pd.DataFrame()).head(5)), use_container_width=True)
            with col_r:
                st.caption("ra.parquet (5행)")
                st.dataframe(mask_df_for_display(parsed.get("ra", pd.DataFrame()).head(5)), use_container_width=True)
