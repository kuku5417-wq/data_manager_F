"""_cleanup_uploads.py — 업로드 폴더에 잘못 쌓인 백업 사본 일회성 정리 (개발 도구).

배경: 폴더 전체 변환 경로가 컨버터의 backup_dir 인자에 `_backup/` 이 아니라 **업로드
폴더 자신**을 넘기던 버그가 있었다. 그 결과 변환할 때마다 `{YYYYMMDD}_{원본명}` 사본이
업로드 폴더에 생겼고, ESG 는 스캔 패턴이 `*.xlsx` 라 그 사본이 다음 회차에 다시 변환돼
`20260731_20260730_foo.xlsx` 처럼 접두가 중첩되며 회차마다 파일이 불어났다.

이 스크립트는 그때 쌓인 잔여물을 걷어낸다:
  - `upload/<섹션>/` 에서 이름이 `YYYYMMDD_` 로 시작하는 파일을 `_backup/` 으로 이동
  - 같은 이름이 이미 `_backup/` 에 있으면 중복이므로 삭제
  - 중첩 접두(`20260731_20260730_foo.xlsx`)는 접두를 모두 벗겨 원래 이름으로 판단

**기본은 dry-run** — 무엇을 하는지 출력만 한다. 실제 반영은 `--apply`.

실행:
    python _cleanup_uploads.py                 # 미리보기
    python _cleanup_uploads.py --apply         # 실제 정리
    python _cleanup_uploads.py --apply --section esg
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import path_config as pc

# "20260730_" 형태의 날짜 접두 (중첩분은 반복 매칭으로 전부 제거)
_DATE_PREFIX = re.compile(r"^\d{8}_")

SECTIONS = ("esg", "ptw", "out")
EXTS = (".xlsx", ".xls", ".csv")


def _strip_date_prefixes(name: str) -> tuple[str, int]:
    """중첩된 날짜 접두를 모두 벗기고 (원래이름, 벗긴 횟수) 반환."""
    n = 0
    while _DATE_PREFIX.match(name):
        name = _DATE_PREFIX.sub("", name, count=1)
        n += 1
    return name, n


def cleanup_section(section: str, apply: bool) -> tuple[int, int]:
    """한 섹션 정리. Returns: (이동 건수, 중복삭제 건수)."""
    up = pc.get_upload_dir(section)
    bk = pc.get_backup_dir(section)
    moved = dropped = 0

    try:
        entries = sorted(p for p in up.iterdir() if p.is_file())
    except Exception as e:   # noqa: BLE001 — 폴더 접근 불가(네트워크·권한)면 스킵
        print(f"[{section}] 폴더 접근 실패 — 스킵: {e}")
        return 0, 0

    for fp in entries:
        if fp.suffix.lower() not in EXTS:
            continue
        base, depth = _strip_date_prefixes(fp.name)
        if depth == 0:
            continue        # 날짜 접두 없음 = 정상 원본, 건드리지 않는다

        target = bk / fp.name
        nested = f" (접두 {depth}중첩 → 원본 '{base}')" if depth > 1 else ""
        if target.exists():
            print(f"[{section}] 중복 삭제: {fp.name}{nested}")
            if apply:
                try:
                    fp.unlink()
                    dropped += 1
                except Exception as e:   # noqa: BLE001 — 삭제 실패는 건너뛴다
                    print(f"    └ 삭제 실패: {e}")
            else:
                dropped += 1
        else:
            print(f"[{section}] _backup 이동: {fp.name}{nested}")
            if apply:
                try:
                    pc.move_file(fp, bk)
                    moved += 1
                except Exception as e:   # noqa: BLE001 — 이동 실패는 건너뛴다
                    print(f"    └ 이동 실패: {e}")
            else:
                moved += 1

    return moved, dropped


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    apply = "--apply" in argv
    sections = SECTIONS
    if "--section" in argv:
        i = argv.index("--section")
        if i + 1 < len(argv):
            sections = (argv[i + 1],)

    print(f"환경: {pc.get_env_label()}")
    print(f"모드: {'실제 정리(--apply)' if apply else '미리보기(dry-run)'}\n")

    total_m = total_d = 0
    for sec in sections:
        m, d = cleanup_section(sec, apply)
        total_m += m
        total_d += d
        print(f"  → {sec}: 이동 {m}건 · 중복삭제 {d}건\n")

    print(f"합계: 이동 {total_m}건 · 중복삭제 {total_d}건")
    if not apply and (total_m or total_d):
        print("\n실제 반영하려면: python _cleanup_uploads.py --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
