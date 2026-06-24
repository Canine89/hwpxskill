#!/usr/bin/env python3
"""List editable text slots in a HWPX template.

The output is intended to be filled back with scripts/edit_hwpx.py --slot-json.
It deliberately excludes container paragraphs that only wrap tables, pictures,
or text boxes, because editing them creates overlapping text.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from lxml import etree

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
}


def _parse_section(path: Path) -> etree._Element:
    with zipfile.ZipFile(path, "r") as zf:
        return etree.parse(BytesIO(zf.read("Contents/section0.xml"))).getroot()


def _node_text(node: etree._Element) -> str:
    return "".join(node.itertext())


def _direct_text(paragraph: etree._Element) -> str:
    return "".join(
        _node_text(t)
        for t in paragraph.xpath("./hp:run/hp:t", namespaces=NS)
    )


def _all_text(scope: etree._Element) -> str:
    return "".join(scope.xpath(".//hp:t//text()", namespaces=NS))


def _normalized_len(text: str) -> int:
    return len("".join(text.split()))


def _preview(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _has_nested_paragraph(paragraph: etree._Element) -> bool:
    return bool(paragraph.xpath(".//hp:p", namespaces=NS))


def _paragraph_slots(root: etree._Element, preview_len: int) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for index, paragraph in enumerate(root.xpath(".//hp:p", namespaces=NS)):
        text = _direct_text(paragraph)
        if not text.strip():
            continue
        if _has_nested_paragraph(paragraph):
            continue
        slots.append(
            {
                "key": f"p:{index}",
                "kind": "paragraph",
                "index": index,
                "max_chars": _normalized_len(text),
                "text_len": len(text),
                "text_len_nospace": _normalized_len(text),
                "preview": _preview(text, preview_len),
            }
        )
    return slots


def _cell_slots(
    root: etree._Element,
    preview_len: int,
    include_empty: bool,
) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for table_index, table in enumerate(root.xpath(".//hp:tbl", namespaces=NS)):
        seen: dict[tuple[str, str], int] = {}
        for cell in table.xpath(".//hp:tc", namespaces=NS):
            addr = cell.find("hp:cellAddr", namespaces=NS)
            if addr is None:
                continue
            row = addr.get("rowAddr", "0")
            col = addr.get("colAddr", "0")
            occurrence = seen.get((row, col), 0)
            seen[(row, col)] = occurrence + 1
            text = _all_text(cell)
            if not include_empty and not text.strip():
                continue
            key = f"cell:{table_index}:{row}:{col}"
            if occurrence:
                key += f":{occurrence}"
            slots.append(
                {
                    "key": key,
                    "kind": "cell",
                    "table": table_index,
                    "row": int(row),
                    "col": int(col),
                    "occurrence": occurrence,
                    "max_chars": _normalized_len(text),
                    "text_len": len(text),
                    "text_len_nospace": _normalized_len(text),
                    "empty": not bool(text.strip()),
                    "preview": _preview(text, preview_len),
                }
            )
    return slots


def collect_slots(
    path: Path,
    preview_len: int = 80,
    include_empty_cells: bool = True,
) -> dict[str, Any]:
    root = _parse_section(path)
    paragraph_slots = _paragraph_slots(root, preview_len)
    cell_slots = _cell_slots(root, preview_len, include_empty_cells)
    return {
        "source": str(path),
        "version": 1,
        "usage": "Fill with scripts/edit_hwpx.py template.hwpx -o out.hwpx --slot-json values.json",
        "slots": paragraph_slots + cell_slots,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="HWPX 편집 가능 텍스트 슬롯 추출")
    parser.add_argument("input", type=Path, help="템플릿 .hwpx")
    parser.add_argument("--output", "-o", type=Path, help="슬롯 JSON 저장 경로")
    parser.add_argument("--preview-len", type=int, default=80)
    parser.add_argument(
        "--no-empty-cells",
        action="store_true",
        help="빈 표 셀 슬롯을 출력하지 않음",
    )
    parser.add_argument("--pretty", action="store_true", help="표 형태 요약 출력")
    args = parser.parse_args()

    profile = collect_slots(
        args.input,
        args.preview_len,
        include_empty_cells=not args.no_empty_cells,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"WROTE: {args.output}")
        print(f"  slots: {len(profile['slots'])}")
        return 0

    if args.pretty:
        for slot in profile["slots"]:
            print(
                f"{slot['key']}\t{slot['kind']}\tmax={slot['max_chars']}\t"
                f"empty={slot.get('empty', False)}\t{slot['preview']}"
            )
    else:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
