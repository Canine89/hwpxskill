#!/usr/bin/env python3
"""Extract text from an HWPX document by reading OWPML XML directly.

Usage:
    python3 scripts/text_extract.py document.hwpx
    python3 scripts/text_extract.py document.hwpx --format markdown
    python3 scripts/text_extract.py document.hwpx --include-tables
"""

from __future__ import annotations

import argparse
import sys
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}


def _parse_xml(data: bytes) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False)
    return etree.parse(BytesIO(data), parser).getroot()


def _text_of(scope: etree._Element) -> str:
    return "".join(
        "".join(t.itertext())
        for t in scope.xpath(".//hp:t", namespaces=NS)
    )


def _direct_run_text_of(paragraph: etree._Element) -> str:
    return "".join(
        "".join(t.itertext())
        for t in paragraph.xpath("./hp:run/hp:t", namespaces=NS)
    )


def _section_names(zf: ZipFile) -> list[str]:
    return sorted(
        name
        for name in zf.namelist()
        if name.startswith("Contents/section") and name.endswith(".xml")
    )


def _top_level_paragraphs(section: etree._Element) -> list[etree._Element]:
    paragraphs = section.xpath("./hp:p", namespaces=NS)
    if paragraphs:
        return paragraphs
    return section.xpath(".//hs:sec/hp:p", namespaces=NS)


def _paragraph_is_inside_table(p: etree._Element) -> bool:
    parent = p.getparent()
    while parent is not None:
        if etree.QName(parent).localname == "tc":
            return True
        parent = parent.getparent()
    return False


def extract_plain(hwpx_path: Path, *, include_tables: bool = False) -> str:
    lines: list[str] = []
    try:
        zf = ZipFile(hwpx_path, "r")
    except BadZipFile:
        raise SystemExit(f"올바른 ZIP/HWPX 파일이 아닙니다: {hwpx_path}")

    with zf:
        for section_name in _section_names(zf):
            root = _parse_xml(zf.read(section_name))
            query = ".//hp:p" if include_tables else "./hp:p"
            paragraphs = root.xpath(query, namespaces=NS)
            for p in paragraphs:
                if not include_tables and _paragraph_is_inside_table(p):
                    continue
                text = (
                    _text_of(p) if include_tables else _direct_run_text_of(p)
                ).strip()
                if text:
                    lines.append(text)
    return "\n".join(lines)


def extract_markdown(hwpx_path: Path) -> str:
    lines: list[str] = []
    try:
        zf = ZipFile(hwpx_path, "r")
    except BadZipFile:
        raise SystemExit(f"올바른 ZIP/HWPX 파일이 아닙니다: {hwpx_path}")

    with zf:
        for section_idx, section_name in enumerate(_section_names(zf)):
            if section_idx:
                lines.extend(["", "---", ""])
            root = _parse_xml(zf.read(section_name))

            for p in _top_level_paragraphs(root):
                own_text = _direct_run_text_of(p).strip()
                if own_text:
                    lines.append(own_text)

                for cell in p.xpath(".//hp:tc", namespaces=NS):
                    cell_text = _text_of(cell).strip()
                    if cell_text:
                        lines.append(f"  {cell_text}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract text from an HWPX document"
    )
    parser.add_argument("input", type=Path, help="Path to .hwpx file")
    parser.add_argument(
        "--format", "-f",
        choices=["plain", "markdown"],
        default="plain",
        help="Output format (default: plain)",
    )
    parser.add_argument(
        "--include-tables",
        action="store_true",
        help="Include text from tables and nested objects in plain mode",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        return 2

    if args.format == "markdown":
        result = extract_markdown(args.input)
    else:
        result = extract_plain(args.input, include_tables=args.include_tables)

    if args.output:
        args.output.write_text(result, encoding="utf-8")
        print(f"Extracted to: {args.output}", file=sys.stderr)
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
