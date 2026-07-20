"""ui_styles.py — 산업형 디자인 CSS"""

_CSS = """
<style>
/* ── 전역 배경 ─────────────────────────────────────────────── */
.stApp { background: #F1F5F9; }

/* ── 사이드바 ────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: #1E3A5F !important;
    padding-top: 0 !important;
}
[data-testid="stSidebar"] * { color: #CBD5E1 !important; }
[data-testid="stSidebar"] .stMarkdown h3 {
    color: #FFFFFF !important;
    font-size: 15px;
    margin: 0;
    padding: 20px 16px 8px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.15) !important;
    margin: 8px 0;
}
/* 사이드바 라디오 */
[data-testid="stSidebar"] [data-testid="stRadio"] label {
    color: #CBD5E1 !important;
    font-size: 14px;
    padding: 6px 8px;
    border-radius: 6px;
    transition: all 0.15s;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(255,255,255,0.1) !important;
    color: #FFFFFF !important;
}
/* 선택된 라디오 */
[data-testid="stSidebar"] [data-testid="stRadio"] [aria-checked="true"] + label {
    background: rgba(0,86,210,0.3) !important;
    color: #FFFFFF !important;
}
/* 비활성 버튼 */
[data-testid="stSidebar"] .stButton button:disabled {
    background: rgba(255,255,255,0.05) !important;
    color: #64748B !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    font-size: 13px;
}
[data-testid="stSidebar"] .stButton button {
    width: 100%;
    text-align: left;
    background: transparent !important;
    border: none !important;
    color: #CBD5E1 !important;
    font-size: 14px;
    padding: 6px 8px;
}

/* ── 데이터 카드 ────────────────────────────────────────────── */
.data-card {
    background: #FFFFFF;
    border: 1.5px solid #E2E8F0;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 0;
    cursor: pointer;
    transition: all 0.15s ease;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.data-card:hover {
    border-color: #93C5FD;
    box-shadow: 0 3px 10px rgba(0,86,210,0.12);
    transform: translateY(-1px);
}
.data-card.selected {
    border-color: #0056D2;
    border-width: 2px;
    background: #EFF6FF;
    box-shadow: 0 3px 10px rgba(0,86,210,0.18);
}
.card-title {
    font-size: 13px;
    font-weight: 700;
    color: #1E293B;
    margin-bottom: 4px;
}
.card-meta {
    font-size: 11px;
    color: #64748B;
    margin-bottom: 8px;
}
.card-badges { display: flex; gap: 6px; flex-wrap: wrap; }

/* ── 신호등 배지 ────────────────────────────────────────────── */
.badge {
    font-size: 10.5px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 12px;
    display: inline-flex;
    align-items: center;
    gap: 3px;
    cursor: default;
}
.badge-green  { background: #DCFCE7; color: #059669; }
.badge-amber  { background: #FEF9C3; color: #B45309; }
.badge-gray   { background: #F1F5F9; color: #64748B; }
.badge-saved  { background: #EFF6FF; color: #2563EB; }
.badge-error  { background: #FEF2F2; color: #DC2626; }

/* ── 신호등 KPI 카드 ────────────────────────────────────────── */
.signal-kpi-row {
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
}
.signal-kpi {
    flex: 1;
    background: #FFFFFF;
    border-radius: 10px;
    padding: 14px 16px;
    border: 1.5px solid #E2E8F0;
    text-align: center;
    position: relative;
}
.signal-kpi.green { border-color: #059669; background: #F0FDF4; }
.signal-kpi.amber { border-color: #D97706; background: #FFFBEB; }
.signal-kpi.gray  { border-color: #94A3B8; background: #F8FAFC; }
.signal-kpi .kpi-num {
    font-size: 28px;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 4px;
}
.signal-kpi.green .kpi-num { color: #059669; }
.signal-kpi.amber .kpi-num { color: #D97706; }
.signal-kpi.gray  .kpi-num { color: #64748B; }
.signal-kpi .kpi-label {
    font-size: 11px;
    font-weight: 600;
    color: #64748B;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.signal-kpi .kpi-tooltip {
    font-size: 11px;
    color: #94A3B8;
    margin-top: 4px;
    line-height: 1.4;
}

/* ── 섹션 헤더 ────────────────────────────────────────────── */
.section-title {
    font-size: 12px;
    font-weight: 700;
    color: #64748B;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid #E2E8F0;
}

/* ── Streamlit 기본 오버라이드 ─────────────────────────────── */
.stButton > button {
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    transition: all 0.15s;
}
.stButton > button[kind="primary"] {
    background: #0056D2;
    border-color: #0056D2;
}
.stButton > button[kind="primary"]:hover {
    background: #0041A8;
    border-color: #0041A8;
}
[data-testid="stExpander"] {
    border: 1px solid #E2E8F0 !important;
    border-radius: 8px !important;
}
/* 입력창 */
[data-testid="stTextInput"] > div > div {
    border-radius: 8px;
    border-color: #CBD5E1;
    font-size: 13px;
}
/* 파일 업로더 */
[data-testid="stFileUploader"] {
    background: #F8FAFC;
    border: 2px dashed #CBD5E1;
    border-radius: 10px;
    padding: 12px;
}
/* 데이터프레임 */
[data-testid="stDataFrame"] {
    border-radius: 8px;
    overflow: hidden;
}
/* 메인 패딩 조정 */
.main .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

/* ── 카드 우측 버튼 열 — 상세보기 / 저장 ───────────────────────── */
.data-card { margin-bottom: 6px; }

/* 카드 행 내 버튼 간격 제거 → 상세+저장 버튼이 카드 높이에 꽉 차도록 */
[data-testid="stHorizontalBlock"]:has(.data-card) [data-testid="stVerticalBlock"] .stButton {
    margin-bottom: 0 !important;
}
[data-testid="stHorizontalBlock"]:has(.data-card) [data-testid="stVerticalBlock"] .stButton + .stButton {
    margin-top: 4px !important;
}
</style>
"""


def inject_css() -> None:
    """산업형 디자인 CSS 전역 주입"""
    import streamlit as st
    st.markdown(_CSS, unsafe_allow_html=True)
