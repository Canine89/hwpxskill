#!/usr/bin/env python3
"""Finalize and quality-check an HWPX file after XML-level editing.

This tool intentionally reuses edit_hwpx.write_raw_preserving_zip() so unchanged
ZIP entries keep their original local headers and compressed payloads. That is
important for form-preserving edits where a normal ZipFile rewrite can change
metadata that Hancom is sensitive to.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from edit_hwpx import write_raw_preserving_zip  # noqa: E402

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}

LINESEG_RE = re.compile(
    rb"<hp:linesegarray\b[^>]*/>|<hp:linesegarray\b[^>]*>.*?</hp:linesegarray>",
    re.DOTALL,
)


def strip_linesegarray_from_bytes(data: bytes) -> tuple[bytes, int]:
    """Remove cached line-layout arrays from one XML payload."""

    return LINESEG_RE.subn(b"", data)


def strip_linesegarray(hwpx_path: str | Path, output_path: str | Path | None = None) -> int:
    """Strip hp:linesegarray elements from Contents/*.xml entries.

    Only XML entries whose bytes actually change are replaced. All other package
    entries are copied byte-for-byte through the raw ZIP writer.
    """

    src = Path(hwpx_path)
    dst = Path(output_path) if output_path else src
    replacements: dict[str, bytes] = {}
    total_removed = 0

    with zipfile.ZipFile(src, "r") as zf:
        for name in zf.namelist():
            if not (name.startswith("Contents/") and name.endswith(".xml")):
                continue
            original = zf.read(name)
            changed, removed = strip_linesegarray_from_bytes(original)
            if removed:
                replacements[name] = changed
                total_removed += removed

    if replacements:
        write_raw_preserving_zip(src, dst, replacements)
    elif src != dst:
        dst.write_bytes(src.read_bytes())

    return total_removed


def _text_of(elem: etree._Element) -> str:
    return "".join(elem.xpath(".//hp:t/text()", namespaces=NS))


def _paragraph_texts(elem: etree._Element) -> list[str]:
    return [_text_of(p).strip() for p in elem.xpath("./hp:p", namespaces=NS)]


def _cell_addr(tc: etree._Element) -> tuple[int, int]:
    addr = tc.find("hp:cellAddr", NS)
    if addr is None:
        return (-1, -1)
    return int(addr.get("rowAddr", "-1")), int(addr.get("colAddr", "-1"))


def _cell_size(tc: etree._Element) -> tuple[int, int]:
    size = tc.find("hp:cellSz", NS)
    if size is None:
        return (0, 0)
    return int(size.get("width", "0")), int(size.get("height", "0"))


def _weighted_len(text: str) -> int:
    total = 0
    for ch in text:
        total += 2 if ord(ch) > 127 else 1
    return total


def _is_heading(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    return bool(
        re.match(r"^(\[|【|▶|\d+[.)]\s|[가-힣][.)]\s|[A-Z][.)]\s|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[.)]\s)", text)
        or (len(text) <= 18 and text.endswith(":"))
    )


def _has_visible_indent(text: str) -> bool:
    return bool(re.match(r"^(\s+|[-*]\s+|[▶※]\s*|\d+[.)]\s+|[가-힣][.)]\s+)", text))


def find_layout_warnings(
    hwpx_path: str | Path,
    *,
    max_cell_paragraph_chars: int = 90,
    min_long_cell_height: int = 6000,
) -> list[dict[str, Any]]:
    """Find likely text-format and table-layout risks."""

    warnings: list[dict[str, Any]] = []

    with zipfile.ZipFile(hwpx_path, "r") as zf:
        section_names = [
            name for name in zf.namelist()
            if name.startswith("Contents/section") and name.endswith(".xml")
        ]

        for section_name in section_names:
            root = etree.fromstring(zf.read(section_name))

            for table_index, tbl in enumerate(root.xpath(".//hp:tbl", namespaces=NS), start=1):
                for tc in tbl.xpath(".//hp:tc", namespaces=NS):
                    row, col = _cell_addr(tc)
                    _, height = _cell_size(tc)
                    paras = [p for p in _paragraph_texts(tc) if p]
                    if not paras:
                        continue

                    text = " ".join(paras)
                    longest_para = max((_weighted_len(p) for p in paras), default=0)
                    estimated_lines = max(1, math.ceil(_weighted_len(text) / 80))

                    if len(paras) == 1 and longest_para > max_cell_paragraph_chars:
                        warnings.append({
                            "type": "long_single_paragraph_cell",
                            "section": section_name,
                            "table": table_index,
                            "row": row,
                            "col": col,
                            "height": height,
                            "message": "표 셀의 긴 텍스트가 한 문단에 몰려 있습니다. 문단/목록으로 나누는 것이 안전합니다.",
                            "sample": text[:120],
                        })

                    if (longest_para > max_cell_paragraph_chars or estimated_lines >= 3) and height < min_long_cell_height:
                        warnings.append({
                            "type": "short_row_for_long_cell",
                            "section": section_name,
                            "table": table_index,
                            "row": row,
                            "col": col,
                            "height": height,
                            "message": "텍스트 양에 비해 표 행 높이가 낮아 렌더링 밀림 위험이 있습니다.",
                            "sample": text[:120],
                        })

                    if len(paras) >= 3 and height < min_long_cell_height:
                        warnings.append({
                            "type": "multi_paragraph_short_cell",
                            "section": section_name,
                            "table": table_index,
                            "row": row,
                            "col": col,
                            "height": height,
                            "message": "여러 문단이 들어간 셀의 행 높이가 낮습니다.",
                            "sample": text[:120],
                        })

            top_paras = root.xpath("./hp:p[not(.//hp:tbl)]", namespaces=NS)
            prev_text = ""
            for p in top_paras:
                text = _text_of(p)
                if not text:
                    continue
                if _is_heading(prev_text) and not _is_heading(text) and not _has_visible_indent(text):
                    warnings.append({
                        "type": "body_paragraph_without_visible_indent",
                        "section": section_name,
                        "message": "제목 다음 본문 문단에 눈에 보이는 들여쓰기나 목록 표지가 없습니다.",
                        "heading": prev_text[:80],
                        "sample": text[:120],
                    })
                prev_text = text

    return warnings


def hancom_open_check(hwpx_path: str | Path, *, visible: bool = False) -> tuple[bool, str]:
    """Try to open the HWPX file with Hancom Office through Windows COM."""

    if os.name != "nt":
        return False, "Hancom COM validation is only available on Windows."

    try:
        import win32com.client  # type: ignore
    except ImportError:
        return False, "pywin32 is not installed; install pywin32 to use --hancom."

    hwp = None
    try:
        hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        try:
            hwp.XHwpWindows.Item(0).Visible = visible
        except Exception:
            pass
        try:
            hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        except Exception:
            pass
        ok = bool(hwp.Open(str(Path(hwpx_path).resolve()), "", ""))
        return ok, "Hancom Open returned True." if ok else "Hancom Open returned False."
    except Exception as exc:  # pragma: no cover - requires Hancom/COM
        return False, f"Hancom COM open failed: {exc}"
    finally:
        if hwp is not None:
            try:
                hwp.Quit()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize and quality-check an HWPX file")
    parser.add_argument("input", help="Path to .hwpx file")
    parser.add_argument("-o", "--output", help="Output path when stripping line layout caches")
    parser.add_argument(
        "--strip-linesegarray",
        action="store_true",
        help="Remove hp:linesegarray layout caches so Hancom recalculates layout",
    )
    parser.add_argument("--layout", action="store_true", help="Run table density and text indentation warnings")
    parser.add_argument("--hancom", action="store_true", help="Open the file with Hancom Office through Windows COM")
    parser.add_argument("--visible", action="store_true", help="Show the Hancom window during --hancom validation")
    parser.add_argument("--json", dest="json_path", help="Write machine-readable report JSON")
    args = parser.parse_args()

    report: dict[str, Any] = {
        "input": str(Path(args.input).resolve()),
        "status": "PASS",
        "actions": [],
        "warnings": [],
        "errors": [],
    }

    target = Path(args.output) if args.output else Path(args.input)

    if args.strip_linesegarray:
        removed = strip_linesegarray(args.input, target)
        report["actions"].append({"strip_linesegarray_removed": removed, "output": str(target.resolve())})

    check_path = target if args.strip_linesegarray else Path(args.input)

    if args.layout:
        report["warnings"].extend(find_layout_warnings(check_path))

    if args.hancom:
        ok, message = hancom_open_check(check_path, visible=args.visible)
        report["hancom"] = {"ok": ok, "message": message}
        if not ok:
            report["errors"].append(message)

    if report["errors"]:
        report["status"] = "FAIL"
    elif report["warnings"]:
        report["status"] = "WARN"

    print(f"HWPX FINALIZE: {report['status']}")
    for action in report["actions"]:
        print(f"  action: {action}")
    for warning in report["warnings"][:30]:
        location = ""
        if "table" in warning:
            location = f" table={warning['table']} row={warning['row']} col={warning['col']}"
        print(f"  warning: {warning['type']}{location}: {warning['message']}")
    if len(report["warnings"]) > 30:
        print(f"  warning: ... {len(report['warnings']) - 30} more")
    for error in report["errors"]:
        print(f"  error: {error}", file=sys.stderr)

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
