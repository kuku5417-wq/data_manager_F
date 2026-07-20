"""pii.py — 개인정보 마스킹 유틸 (표시 계층 전용).

개인정보보호법 최소수집·안전조치 원칙에 따라, parquet 원본에는 업무상 필요한
개인정보(name/phone/greeter 등)를 보관하되 **화면 출력 시에만 마스킹**한다.
(tbm/modules/pii.py 의 검증된 mask_name/mask_phone 을 이식 + DataFrame 헬퍼 추가)

- 미사용 개인정보(PTW 담당자·shipbbs 작성자 등)는 애초에 수집·저장하지 않는다(최소수집).
- 이 모듈은 표시 직전에만 호출한다. 저장·전송 데이터는 원문을 유지한다.
"""
from __future__ import annotations

import pandas as pd

# 컬럼명(소문자 기준) → 마스킹 종류. 정형 컬럼명 + 한글 라벨 모두 대응.
_NAME_COLS = {
    "name", "greeter", "greeter_actual", "hname", "hse_manage", "insertby",
    "성명", "이름", "접견자", "담당자", "작성자", "신청자", "작업자",
}
_PHONE_COLS = {"phone", "연락처", "전화", "전화번호", "hp", "tel", "mobile"}


def mask_name(name: str) -> str:
    """이름 마스킹: 첫·끝 글자 유지, 중간 '*'. 콤마 그룹은 각각 마스킹.

    홍길동 → 홍*동 | 이순신 → 이*신 | Kim → K*m | 이순 → 이*
    """
    if not name or not isinstance(name, str):
        return name
    parts = [n.strip() for n in name.split(",")]
    masked = []
    for n in parts:
        if len(n) <= 1:
            masked.append(n)
        elif len(n) == 2:
            masked.append(n[0] + "*")
        else:
            masked.append(n[0] + "*" * (len(n) - 2) + n[-1])
    return ", ".join(masked)


def mask_phone(phone: str) -> str:
    """전화번호 마스킹: 가운데 4자리 → '****'. 콤마 그룹은 전체 생략."""
    if not phone or not isinstance(phone, str):
        return phone
    if "," in phone:
        return "****"
    parts = str(phone).replace(" ", "").split("-")
    if len(parts) == 3:
        return f"{parts[0]}-****-{parts[2]}"
    if len(phone) >= 10:
        return phone[:3] + "****" + phone[7:]
    return "***-****-****"


def mask_df_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """표시용 복사본 — 컬럼명 규칙으로 개인정보 컬럼만 마스킹. 원본 df 불변.

    이름류 → mask_name, 전화류 → mask_phone. 그 외 컬럼은 그대로 둔다.
    민감 컬럼이 없으면 원본을 그대로 반환(불필요 복사 방지).
    """
    if df is None or df.empty:
        return df
    targets = []  # (컬럼, 함수)
    for col in df.columns:
        low = str(col).strip().lower()
        if low in _NAME_COLS:
            targets.append((col, mask_name))
        elif low in _PHONE_COLS:
            targets.append((col, mask_phone))
    if not targets:
        return df
    out = df.copy()
    for col, fn in targets:
        out[col] = out[col].map(lambda v: fn(str(v)) if pd.notna(v) and str(v) != "" else v)
    return out
