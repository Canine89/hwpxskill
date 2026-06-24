#!/usr/bin/env python3
"""Unpack an HWPX file into a directory.

Usage:
    python unpack.py input.hwpx output_dir/
"""

import argparse
import os
import sys
from pathlib import Path
from zipfile import ZipFile

from lxml import etree


def _pretty_print_xml(data: bytes) -> bytes:
    tree = etree.fromstring(data)
    etree.indent(tree, space="  ")
    return etree.tostring(
        tree,
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
    )


def unpack(hwpx_path: str, output_dir: str, *, pretty: bool = False) -> None:
    """Extract HWPX archive.

    By default this writes archive entries byte-for-byte. HWPX XML can contain
    mixed content such as hp:t text with child controls, so inserting pretty
    print whitespace can change rendered document text.
    """

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    with ZipFile(hwpx_path, "r") as zf:
        for entry in zf.namelist():
            data = zf.read(entry)
            dest = output / entry
            dest.parent.mkdir(parents=True, exist_ok=True)

            if pretty and (entry.endswith(".xml") or entry.endswith(".hpf")):
                try:
                    dest.write_bytes(_pretty_print_xml(data))
                    continue
                except etree.XMLSyntaxError:
                    pass  # Fall through to raw write

            dest.write_bytes(data)

    print(f"Unpacked: {hwpx_path} -> {output_dir}")
    print(f"  Files: {len(list(output.rglob('*')))} entries")
    if pretty:
        print("  Warning: --pretty output is for inspection only; do not pack it back into HWPX.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unpack HWPX file into a directory"
    )
    parser.add_argument("input", help="Path to .hwpx file")
    parser.add_argument("output", help="Output directory path")
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print XML for inspection only. This can alter HWPX mixed-content whitespace.",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    unpack(args.input, args.output, pretty=args.pretty)


if __name__ == "__main__":
    main()
