"""doc_parser.py — PDF 파싱 (data_manager 이식본).

원본: tbm_system_v6/modules/doc_parser.py (생성부만 이식)
변경: 경로 path_config(upload/{guide,accident}), llm_client 임포트, 저장 원자적.
의존: pdfplumber.
raw 위치: upload/guide/*.pdf → guide.parquet, upload/accident/*.pdf → accident.parquet
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

import path_config as pc
from parquet_io import save_parquet_atomic


def _pq(key: str) -> Path:
    return pc.get_parquet_dir() / f"{key}.parquet"


def extract_text_from_pdf(pdf_path: Path) -> str:
    import pdfplumber
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages).strip()
    except Exception:
        return ""


def parse_guide_pdfs(progress_cb=None, skip_existing: bool = False) -> pd.DataFrame:
    """upload/guide/*.pdf → guide.parquet (LLM으로 title/keywords 파생).

    skip_existing=True: 기존 guide.parquet의 pdf_filename에 이미 있는 PDF는 재파싱(재LLM)
    하지 않고 신규만 처리 후 append. 무인 잡(doc_job)에서 LLM 비용·중복 방지에 사용.
    """
    from llm_client import call_llm

    guide_cols = ["id","standard_id","seq","title","text","keywords","pdf_filename","link"]
    existing = None
    done: set[str] = set()
    if skip_existing:
        existing = load_guide()
        if existing is not None and not existing.empty and "pdf_filename" in existing.columns:
            done = set(existing["pdf_filename"].astype(str))

    guide_dir = pc.get_upload_dir("guide")
    pdf_files = [fp for fp in sorted(guide_dir.glob("*.pdf")) if fp.name not in done]
    if not pdf_files:
        # 신규 없음: 기존 그대로 반환(증분), 또는 빈 DF
        return existing if existing is not None else pd.DataFrame(columns=guide_cols)

    rows = []
    for i, fp in enumerate(pdf_files):
        if progress_cb:
            progress_cb(i + 1, len(pdf_files), fp.name)
        text = extract_text_from_pdf(fp)
        stem = fp.stem
        items: list[dict] = []
        if text:
            prompt = f"""다음은 조선소 안전가이드 문서입니다. 한 문서에 여러 표준(표준ID)이 포함될 수 있으니
표준ID별로 분리해 추출하세요.

문서 내용 (첫 3000자):
{text[:3000]}

추출 규칙:
- standard_id: 문서에 표기된 표준/규격 번호(예: SG-001, GD-12). 없으면 빈 문자열.
- keywords: 작업유형·위험요인·사고유형 중심 5~10개 (예: 밀폐공간, 산소결핍, 추락, 용접, 화재)
JSON 형식으로만 응답(표준이 1개면 항목 1개):
{{"guides": [
  {{"standard_id": "표준ID", "title": "표준 제목", "keywords": "키워드1, 키워드2, 키워드3"}}
]}}"""
            result = call_llm(prompt, max_tokens=700)
            if isinstance(result, dict):
                items = result.get("guides") or ([result] if result.get("title") else [])
        if not items:   # LLM 실패/빈 텍스트 → 파일 1행 폴백
            items = [{"standard_id": "", "title": stem, "keywords": ""}]
        for n, it in enumerate(items, 1):
            it = it if isinstance(it, dict) else {}
            sid = str(it.get("standard_id", "") or "").strip()
            rows.append({"id": sid or f"{stem}#{n}", "standard_id": sid, "seq": n,
                         "title": it.get("title", "") or stem, "text": text,
                         "keywords": it.get("keywords", ""), "pdf_filename": fp.name, "link": str(fp)})

    new_df = pd.DataFrame(rows)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, new_df], ignore_index=True)
    else:
        df = new_df
    if "standard_id" in df.columns:        # 표준ID 기준 정리(정렬)
        df = df.sort_values(["standard_id", "pdf_filename", "seq"], na_position="last").reset_index(drop=True)
    save_parquet_atomic(df, _pq("guide"))
    return df


def parse_accident_pdfs(progress_cb=None, skip_existing: bool = False) -> pd.DataFrame:
    """upload/accident/*.pdf → accident.parquet (LLM으로 사고정보 파생).

    skip_existing=True: 기존 accident.parquet의 pdf_filename에 있는 PDF는 스킵하고 신규만
    처리 후 append. 무인 잡(doc_job)에서 LLM 비용·중복 방지에 사용.
    """
    from llm_client import call_llm

    acc_cols = ["id","seq","date","summary","cause","result","countermeasure",
                "accident_type","keywords","text","source","pdf_filename"]
    existing = None
    done: set[str] = set()
    if skip_existing:
        existing = load_accident()
        if existing is not None and not existing.empty and "pdf_filename" in existing.columns:
            done = set(existing["pdf_filename"].astype(str))

    acc_dir   = pc.get_upload_dir("accident")
    pdf_files = [fp for fp in sorted(acc_dir.glob("*.pdf")) if fp.name not in done]
    if not pdf_files:
        return existing if existing is not None else pd.DataFrame(columns=acc_cols)

    today = datetime.today().strftime("%Y-%m-%d")
    rows = []
    for i, fp in enumerate(pdf_files):
        if progress_cb:
            progress_cb(i + 1, len(pdf_files), fp.name)
        text = extract_text_from_pdf(fp)
        stem = fp.stem
        items: list[dict] = []
        if text:
            prompt = f"""다음은 조선소 사고보고서입니다. 한 문서에 여러 건의 사고가 있을 수 있으니
각 사고를 개별 항목으로 분리해 추출하세요.

문서 내용 (첫 3000자):
{text[:3000]}

JSON 형식으로만 응답(사고가 1건이면 항목 1개):
{{"accidents": [
  {{"summary": "사고개요 1문장", "cause": "사고원인 1문장", "result": "사고결과/피해",
    "countermeasure": "재발방지 대책", "accident_type": "사고유형(추락/화재/감전 등)",
    "keywords": "위험키워드1, 키워드2"}}
]}}"""
            res = call_llm(prompt, max_tokens=900)
            if isinstance(res, dict):
                items = res.get("accidents") or ([res] if res.get("summary") else [])
        if not items:   # LLM 실패/빈 텍스트 → 파일 1행 폴백(누락 방지)
            items = [{"summary": (text.split("\n")[0][:100] if text else "")}]
        for n, it in enumerate(items, 1):
            it = it if isinstance(it, dict) else {}
            rows.append({"id": f"{stem}#{n}", "seq": n, "date": today,
                         "summary": it.get("summary", ""), "cause": it.get("cause", ""),
                         "result": it.get("result", ""), "countermeasure": it.get("countermeasure", ""),
                         "accident_type": it.get("accident_type", ""), "keywords": it.get("keywords", ""),
                         "text": text, "source": fp.name, "pdf_filename": fp.name})

    new_df = pd.DataFrame(rows)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, new_df], ignore_index=True)
    else:
        df = new_df
    save_parquet_atomic(df, _pq("accident"))
    return df


def load_guide() -> pd.DataFrame | None:
    p = _pq("guide")
    return pd.read_parquet(p) if p.exists() else None


def load_accident() -> pd.DataFrame | None:
    p = _pq("accident")
    return pd.read_parquet(p) if p.exists() else None
