#!/usr/bin/env python3
"""Normalize HWPX namespace prefixes and header itemCnt values.

Some generators serialize OWPML XML with automatic prefixes such as ns0/ns1.
Hancom usually accepts namespace URIs, but standard hh/hc/hp/hs prefixes are
more compatible with viewers and make diffs easier to inspect.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from edit_hwpx import write_raw_preserving_zip  # noqa: E402

NS_MAP = {
    "http://www.hancom.co.kr/hwpml/2011/head": "hh",
    "http://www.hancom.co.kr/hwpml/2011/core": "hc",
    "http://www.hancom.co.kr/hwpml/2011/paragraph": "hp",
    "http://www.hancom.co.kr/hwpml/2011/section": "hs",
}


def _fix_item_counts(header_xml: str) -> str:
    count_map = {
        "charProperties": r"<hh:charPr\b",
        "borderFills": r"<hh:borderFill\b",
        "paraProperties": r"<hh:paraPr\b",
        "styles": r"<hh:style\b",
    }
    for container, child_re in count_map.items():
        actual = len(re.findall(child_re, header_xml))
        if actual > 0:
            header_xml = re.sub(
                rf"(<hh:{container}\s+[^>]*itemCnt=\")\d+(\")",
                rf"\g<1>{actual}\2",
                header_xml,
            )
    return header_xml


def fix_xml_bytes(name: str, data: bytes) -> bytes:
    text = data.decode("utf-8")
    ns_aliases: dict[str, str] = {}
    for match in re.finditer(r'xmlns:(ns\d+)="([^"]+)"', text):
        alias, uri = match.group(1), match.group(2)
        if uri in NS_MAP:
            ns_aliases[alias] = NS_MAP[uri]

    for old_prefix, new_prefix in ns_aliases.items():
        text = text.replace(f"xmlns:{old_prefix}=", f"xmlns:{new_prefix}=")
        text = text.replace(f"<{old_prefix}:", f"<{new_prefix}:")
        text = text.replace(f"</{old_prefix}:", f"</{new_prefix}:")

    if name == "Contents/header.xml":
        text = _fix_item_counts(text)

    return text.encode("utf-8")


def fix_hwpx_namespaces(hwpx_path: str | Path, output_path: str | Path | None = None) -> int:
    src = Path(hwpx_path)
    dst = Path(output_path) if output_path else src
    replacements: dict[str, bytes] = {}

    with zipfile.ZipFile(src, "r") as zf:
        for name in zf.namelist():
            if not (name.startswith("Contents/") and name.endswith(".xml")):
                continue
            original = zf.read(name)
            changed = fix_xml_bytes(name, original)
            if changed != original:
                replacements[name] = changed

    if replacements:
        write_raw_preserving_zip(src, dst, replacements)
    elif src != dst:
        dst.write_bytes(src.read_bytes())

    return len(replacements)


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize HWPX XML namespace prefixes")
    parser.add_argument("input", help="Path to .hwpx file")
    parser.add_argument("-o", "--output", help="Output path. Defaults to in-place update.")
    args = parser.parse_args()

    count = fix_hwpx_namespaces(args.input, args.output)
    target = args.output or args.input
    print(f"Fixed namespaces: {target} ({count} XML entries changed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
