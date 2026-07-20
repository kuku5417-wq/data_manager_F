"""drm_reader.py — DRM 걸린 엑셀을 win32 Excel COM으로 열어 '깨끗한 xlsx bytes'로 복원.

read_drm_excel(tbm_converter)은 첫 시트만 읽지만, ESG는 6개 시트가 필요하다.
여기서는 win32 Excel(DispatchEx, ReadOnly)로 **전 시트**의 셀 값을 읽어 openpyxl로 재구성한 뒤
DRM이 없는 평문 xlsx bytes로 돌려준다 → 호출부는 그 bytes를 pd.ExcelFile 로 그대로 파싱(기존 로직 재사용).

전제: 로컬에 Excel 설치 + pywin32. 실패(미설치/COM/DRM권한) 시 _DRM_LAST_ERR 기록 후 None.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

# win32(DRM) 마지막 실패 사유 — 호출부/진단이 진짜 원인을 표시하도록 보관
_DRM_LAST_ERR = ""


def _to_cell_value(v):
    """COM 반환값을 openpyxl 이 쓸 수 있는 형태로 정규화.
    pywintypes datetime → python datetime, 그 외(str/float/int/bool/None)는 통과."""
    if v is None:
        return None
    if hasattr(v, "year") and hasattr(v, "month"):   # pywintypes/datetime 류
        try:
            from datetime import datetime
            return datetime(v.year, v.month, v.day, getattr(v, "hour", 0),
                            getattr(v, "minute", 0), getattr(v, "second", 0))
        except Exception:
            return str(v)
    return v


def drm_to_xlsx_bytes(path: Path | str) -> bytes | None:
    """DRM 엑셀(경로) → 전 시트 값을 읽어 평문 xlsx bytes 로 복원. 실패 시 None(_DRM_LAST_ERR).

    Range(Cells(1,1), 마지막셀) 로 A1 고정해 선행 빈행/열 위치를 보존(헤더 행 검출 정합).
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
        import openpyxl
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
        wb = excel_app.Workbooks.Open(abs_path, UpdateLinks=0, ReadOnly=True)

        out = openpyxl.Workbook()
        out.remove(out.active)                    # 기본 시트 제거 — 원본 시트만 채움
        for sheet in wb.Sheets:
            used = sheet.UsedRange
            last_row = used.Row + used.Rows.Count - 1
            last_col = used.Column + used.Columns.Count - 1
            rng = sheet.Range(sheet.Cells(1, 1), sheet.Cells(last_row, last_col))
            values = rng.Value                    # 2D 튜플(또는 단일/None)
            ws = out.create_sheet(title=str(sheet.Name)[:31])   # 엑셀 시트명 31자 제한
            if values is None:
                continue
            if not isinstance(values, (list, tuple)):
                values = ((values,),)
            for row in values:
                if not isinstance(row, (list, tuple)):
                    row = (row,)
                ws.append([_to_cell_value(c) for c in row])

        buf = BytesIO()
        out.save(buf)
        return buf.getvalue()
    except Exception as e:
        _DRM_LAST_ERR = f"{type(e).__name__}: {e}"
        import logging
        logging.warning("drm_to_xlsx_bytes 실패: %s", _DRM_LAST_ERR)
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
