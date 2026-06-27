#!/usr/bin/env python3
"""Validate the structural integrity of an HWPX file.

Checks:
  - Valid ZIP archive
  - Required files present (mimetype, content.hpf, header.xml, section0.xml)
  - mimetype content is correct
  - mimetype is the first ZIP entry and stored without compression
  - All XML files are well-formed
  - content.hpf manifest hrefs exist in the package
  - section image references point to manifest items

Usage:
    python validate.py document.hwpx
"""

import argparse
import sys
from pathlib import Path
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from lxml import etree

REQUIRED_FILES = [
    "mimetype",
    "Contents/content.hpf",
    "Contents/header.xml",
    "Contents/section0.xml",
]

EXPECTED_MIMETYPE = "application/hwp+zip"

NS = {
    "opf": "http://www.idpf.org/2007/opf/",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
}


def validate(hwpx_path: str) -> list[str]:
    """Validate HWPX file and return a list of error messages (empty = valid)."""

    errors: list[str] = []
    path = Path(hwpx_path)

    if not path.is_file():
        return [f"File not found: {hwpx_path}"]

    # Check valid ZIP
    try:
        zf = ZipFile(hwpx_path, "r")
    except BadZipFile:
        return [f"Not a valid ZIP archive: {hwpx_path}"]

    with zf:
        names = zf.namelist()

        # Check required files
        for required in REQUIRED_FILES:
            if required not in names:
                errors.append(f"Missing required file: {required}")

        # Check mimetype content
        if "mimetype" in names:
            mimetype_content = zf.read("mimetype").decode("utf-8").strip()
            if mimetype_content != EXPECTED_MIMETYPE:
                errors.append(
                    f"Invalid mimetype: expected '{EXPECTED_MIMETYPE}', "
                    f"got '{mimetype_content}'"
                )

            # Check mimetype is first entry
            if names[0] != "mimetype":
                errors.append(
                    f"mimetype is not the first ZIP entry (found at index "
                    f"{names.index('mimetype')})"
                )

            # Check mimetype is stored without compression
            info = zf.getinfo("mimetype")
            if info.compress_type != ZIP_STORED:
                errors.append(
                    f"mimetype should use ZIP_STORED (0), "
                    f"got compress_type={info.compress_type}"
                )

        parsed_xml: dict[str, etree._Element] = {}

        # Check XML well-formedness
        for name in names:
            if name.endswith(".xml") or name.endswith(".hpf"):
                try:
                    data = zf.read(name)
                    parsed_xml[name] = etree.fromstring(data)
                except etree.XMLSyntaxError as e:
                    errors.append(f"Malformed XML in {name}: {e}")

        content = parsed_xml.get("Contents/content.hpf")
        if content is not None:
            manifest_ids: set[str] = set()
            for item in content.xpath(".//opf:item", namespaces=NS):
                item_id = item.get("id", "")
                href = item.get("href", "")
                if item_id:
                    manifest_ids.add(item_id)
                if href and href not in names:
                    errors.append(f"content.hpf references missing file: {href}")

            for section_name, section_root in parsed_xml.items():
                if not section_name.startswith("Contents/section"):
                    continue
                for img in section_root.xpath(".//hc:img", namespaces=NS):
                    ref = img.get("binaryItemIDRef", "")
                    if ref and ref not in manifest_ids:
                        errors.append(
                            f"{section_name} image references missing manifest item: {ref}"
                        )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the structural integrity of an HWPX file"
    )
    parser.add_argument("input", help="Path to .hwpx file")
    parser.add_argument(
        "--layout",
        action="store_true",
        help="Also report layout risk warnings from finalize_hwpx.py",
    )
    args = parser.parse_args()

    errors = validate(args.input)

    if errors:
        print(f"INVALID: {args.input}", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"VALID: {args.input}")
        print(f"  All structural checks passed.")
        if args.layout:
            from finalize_hwpx import find_layout_warnings

            warnings = find_layout_warnings(args.input)
            if warnings:
                print(f"  Layout warnings: {len(warnings)}")
                for warning in warnings[:30]:
                    location = ""
                    if "table" in warning:
                        location = (
                            f" table={warning['table']}"
                            f" row={warning['row']}"
                            f" col={warning['col']}"
                        )
                    print(f"  - {warning['type']}{location}: {warning['message']}")
                if len(warnings) > 30:
                    print(f"  - ... {len(warnings) - 30} more")
            else:
                print("  Layout warnings: 0")


if __name__ == "__main__":
    main()
