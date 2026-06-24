#!/usr/bin/env python3
"""
page_guard.py - HWPX 레퍼런스 대비 페이지 드리프트 위험 검사

목표:
- 실제 렌더러의 쪽수 계산을 대체할 수는 없지만,
  쪽수 변화로 이어지기 쉬운 구조 변경을 사전에 차단한다.
- 원본 양식의 문단/셀별 글자 예산을 저장하고 결과물이 예산을 넘지
  않는지 검사한다.

검사 항목:
- 문단 수 / 표 수 / 표 구조(rowCnt, colCnt, width, height) 동일성
- 명시적 pageBreak / columnBreak 수 동일성
- 전체 텍스트 길이 편차(기본 15%) 한도
- 문단별 텍스트 길이 급변(기본 25%) 감지
- 선택: 문단/셀별 글자 예산 프로파일 생성 및 엄격 검사
- 구조 fingerprint에서는 hp:linesegarray 줄 배치 캐시를 제외
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass, asdict, field
from io import BytesIO
from pathlib import Path
from typing import Any, List, Tuple

from lxml import etree

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}


@dataclass
class Metrics:
    paragraph_count: int
    page_break_count: int
    column_break_count: int
    table_count: int
    table_shapes: List[Tuple[str, str, str, str, str, str]]
    text_char_total: int
    text_char_total_nospace: int
    paragraph_text_lengths: List[int]


@dataclass
class TextSlot:
    kind: str
    key: str
    text_len: int
    text_len_nospace: int
    max_chars: int
    text_preview: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _read_section_xml_bytes(hwpx_path: Path) -> bytes:
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        return zf.read("Contents/section0.xml")


def _read_header_xml_bytes(hwpx_path: Path) -> bytes | None:
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        if "Contents/header.xml" not in zf.namelist():
            return None
        return zf.read("Contents/header.xml")


def _parse_section(hwpx_path: Path) -> etree._Element:
    section_bytes = _read_section_xml_bytes(hwpx_path)
    return etree.parse(BytesIO(section_bytes)).getroot()


def _parse_header(hwpx_path: Path) -> etree._Element | None:
    header_bytes = _read_header_xml_bytes(hwpx_path)
    if header_bytes is None:
        return None
    return etree.parse(BytesIO(header_bytes)).getroot()


def _text_of_t_node(t_node: etree._Element) -> str:
    return "".join(t_node.itertext())


def _text_of(scope: etree._Element) -> str:
    return "".join(
        _text_of_t_node(t)
        for t in scope.xpath(".//hp:t", namespaces=NS)
    )


def _direct_run_text_of(paragraph: etree._Element) -> str:
    return "".join(
        _text_of_t_node(t)
        for t in paragraph.xpath("./hp:run/hp:t", namespaces=NS)
    )


def _normalize_text_for_budget(text: str) -> str:
    return "".join(text.split())


def _preview(text: str, limit: int = 40) -> str:
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _is_inside_cell(el: etree._Element) -> bool:
    parent = el.getparent()
    while parent is not None:
        if etree.QName(parent).localname == "tc":
            return True
        parent = parent.getparent()
    return False


def _is_container_paragraph(el: etree._Element) -> bool:
    if etree.QName(el).localname != "p":
        return False
    if el.xpath(".//hp:p", namespaces=NS):
        return True
    return bool(el.xpath("./hp:run/*[not(self::hp:t)]", namespaces=NS))


def collect_metrics(hwpx_path: Path) -> Metrics:
    root = _parse_section(hwpx_path)

    paragraphs = root.xpath(".//hs:sec/hp:p", namespaces=NS)
    if not paragraphs:
        paragraphs = root.xpath(".//hp:p", namespaces=NS)

    page_break_count = sum(1 for p in paragraphs if p.get("pageBreak") == "1")
    column_break_count = sum(1 for p in paragraphs if p.get("columnBreak") == "1")

    tables = root.xpath(".//hp:tbl", namespaces=NS)
    table_shapes: List[Tuple[str, str, str, str, str, str]] = []
    for t in tables:
        sz = t.find("hp:sz", namespaces=NS)
        width = sz.get("width", "") if sz is not None else ""
        height = sz.get("height", "") if sz is not None else ""
        table_shapes.append(
            (
                t.get("rowCnt", ""),
                t.get("colCnt", ""),
                width,
                height,
                t.get("repeatHeader", ""),
                t.get("pageBreak", ""),
            )
        )

    t_nodes = root.xpath(".//hp:t", namespaces=NS)
    text_char_total = 0
    text_char_total_nospace = 0
    for t in t_nodes:
        s = _text_of_t_node(t)
        text_char_total += len(s)
        text_char_total_nospace += len("".join(s.split()))

    paragraph_text_lengths: List[int] = []
    for p in paragraphs:
        plen = 0
        for t in p.xpath(".//hp:t", namespaces=NS):
            plen += len(_text_of_t_node(t))
        paragraph_text_lengths.append(plen)

    return Metrics(
        paragraph_count=len(paragraphs),
        page_break_count=page_break_count,
        column_break_count=column_break_count,
        table_count=len(tables),
        table_shapes=table_shapes,
        text_char_total=text_char_total,
        text_char_total_nospace=text_char_total_nospace,
        paragraph_text_lengths=paragraph_text_lengths,
    )


def _char_heights_by_id(header_root: etree._Element | None) -> dict[str, int]:
    if header_root is None:
        return {}

    heights: dict[str, int] = {}
    for char_pr in header_root.xpath(".//hh:charPr", namespaces={**NS, "hh": "http://www.hancom.co.kr/hwpml/2011/head"}):
        cid = char_pr.get("id")
        if not cid:
            continue
        try:
            heights[cid] = int(char_pr.get("height", "1000"))
        except ValueError:
            heights[cid] = 1000
    return heights


def _first_run_char_height(scope: etree._Element, char_heights: dict[str, int]) -> int:
    run = scope.find(".//hp:run", namespaces=NS)
    if run is None:
        return 1000
    char_id = run.get("charPrIDRef", "0")
    return char_heights.get(char_id, 1000)


def _int_attr(el: etree._Element | None, name: str, default: int = 0) -> int:
    if el is None:
        return default
    try:
        return int(el.get(name, str(default)))
    except ValueError:
        return default


def _estimated_cell_capacity(cell: etree._Element, char_height: int) -> int:
    """Estimate a conservative single-cell text budget.

    HWPX uses HWPUNIT. char height is 100x point size. A Korean glyph is roughly
    square, so using 0.95 * char_height as average advance gives a practical
    guard without depending on a renderer.
    """

    cell_sz = cell.find("hp:cellSz", namespaces=NS)
    margin = cell.find("hp:cellMargin", namespaces=NS)
    width = _int_attr(cell_sz, "width")
    height = _int_attr(cell_sz, "height")
    margin_x = _int_attr(margin, "left") + _int_attr(margin, "right")
    margin_y = _int_attr(margin, "top") + _int_attr(margin, "bottom")
    inner_width = max(width - margin_x, 0)
    inner_height = max(height - margin_y, char_height)
    avg_advance = max(int(char_height * 0.95), 1)
    chars_per_line = max(inner_width // avg_advance, 1)
    line_count = max(inner_height // max(int(char_height * 1.55), 1), 1)
    return max(chars_per_line * line_count, 1)


def collect_text_budget_profile(hwpx_path: Path) -> dict[str, Any]:
    root = _parse_section(hwpx_path)
    header = _parse_header(hwpx_path)
    char_heights = _char_heights_by_id(header)
    slots: list[TextSlot] = []

    paragraphs = root.xpath(".//hp:p", namespaces=NS)
    for idx, p in enumerate(paragraphs):
        if _is_container_paragraph(p):
            continue
        text = _direct_run_text_of(p)
        if not text:
            text = _text_of(p)
        text_nospace = _normalize_text_for_budget(text)
        slots.append(
            TextSlot(
                kind="paragraph",
                key=f"p:{idx}",
                text_len=len(text),
                text_len_nospace=len(text_nospace),
                max_chars=len(text_nospace),
                text_preview=_preview(text),
                meta={
                    "paraPrIDRef": p.get("paraPrIDRef", ""),
                    "styleIDRef": p.get("styleIDRef", ""),
                    "pageBreak": p.get("pageBreak", ""),
                    "columnBreak": p.get("columnBreak", ""),
                    "inside_cell": _is_inside_cell(p),
                },
            )
        )

    tables = root.xpath(".//hp:tbl", namespaces=NS)
    for table_idx, table in enumerate(tables):
        seen_cells: dict[tuple[str, str], int] = {}
        for cell in table.xpath(".//hp:tc", namespaces=NS):
            addr = cell.find("hp:cellAddr", namespaces=NS)
            if addr is None:
                continue
            row = addr.get("rowAddr", "")
            col = addr.get("colAddr", "")
            coord = (row, col)
            occurrence = seen_cells.get(coord, 0)
            seen_cells[coord] = occurrence + 1
            text = _text_of(cell)
            text_nospace = _normalize_text_for_budget(text)
            char_height = _first_run_char_height(cell, char_heights)
            estimated = _estimated_cell_capacity(cell, char_height)
            slots.append(
                TextSlot(
                    kind="cell",
                    key=f"cell:{table_idx}:{row}:{col}:{occurrence}",
                    text_len=len(text),
                    text_len_nospace=len(text_nospace),
                    max_chars=max(len(text_nospace), estimated),
                    text_preview=_preview(text),
                    meta={
                        "table": table_idx,
                        "row": row,
                        "col": col,
                        "occurrence": occurrence,
                        "estimated_capacity": estimated,
                        "char_height": char_height,
                        "borderFillIDRef": cell.get("borderFillIDRef", ""),
                    },
                )
            )

    return {
        "version": 1,
        "source": str(hwpx_path),
        "budget_unit": "non_whitespace_characters",
        "slots": [asdict(slot) for slot in slots],
    }


def _slots_by_key(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(slot.get("key")): slot
        for slot in profile.get("slots", [])
        if slot.get("key")
    }


def compare_text_budget_profile(
    budget_profile: dict[str, Any],
    output_profile: dict[str, Any],
    strict_paragraphs: bool = True,
) -> list[str]:
    errors: list[str] = []
    ref_slots = _slots_by_key(budget_profile)
    out_slots = _slots_by_key(output_profile)

    missing = sorted(set(ref_slots) - set(out_slots))
    extra = sorted(set(out_slots) - set(ref_slots))
    if missing:
        errors.append(f"예산 슬롯 누락: {', '.join(missing[:10])}")
    if extra:
        errors.append(f"예산 외 슬롯 추가: {', '.join(extra[:10])}")

    for key, ref in ref_slots.items():
        out = out_slots.get(key)
        if out is None:
            continue
        if ref.get("kind") != out.get("kind"):
            errors.append(f"{key} 슬롯 종류 불일치: ref={ref.get('kind')}, out={out.get('kind')}")
            continue

        max_chars = int(ref.get("max_chars", 0))
        out_len = int(out.get("text_len_nospace", 0))
        ref_len = int(ref.get("text_len_nospace", 0))
        kind = ref.get("kind")

        inside_cell = bool(ref.get("meta", {}).get("inside_cell"))
        if kind == "paragraph" and inside_cell:
            continue

        if kind == "paragraph" and strict_paragraphs and ref_len != out_len:
            errors.append(
                f"{key} 문단 글자 수 불일치: ref={ref_len}, out={out_len}, "
                f"text={out.get('text_preview', '')!r}"
            )
        elif out_len > max_chars:
            errors.append(
                f"{key} 글자 예산 초과: max={max_chars}, out={out_len}, "
                f"text={out.get('text_preview', '')!r}"
            )

    return errors


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _xml_structure_node(el: etree._Element, inside_hp_text: bool = False) -> dict[str, Any]:
    qname = etree.QName(el)
    is_hp_text = qname.localname == "t" and qname.namespace == NS["hp"]
    ignore_text = inside_hp_text or is_hp_text
    return {
        "tag": el.tag,
        "attrs": sorted((str(k), str(v)) for k, v in el.attrib.items()),
        "text": "" if ignore_text else (el.text or ""),
        "tail": "" if ignore_text else (el.tail or ""),
        "children": [
            _xml_structure_node(child, inside_hp_text=inside_hp_text or is_hp_text)
            for child in el
            if not (
                etree.QName(child).namespace == NS["hp"]
                and etree.QName(child).localname == "linesegarray"
            )
            and not (
                etree.QName(child).namespace == NS["hp"]
                and etree.QName(child).localname == "t"
                and len(child) == 0
            )
        ],
    }


def _xml_structure_hash(data: bytes) -> str:
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    root = etree.parse(BytesIO(data), parser).getroot()
    payload = json.dumps(
        _xml_structure_node(root),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(payload)


def collect_structure_profile(hwpx_path: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    with zipfile.ZipFile(hwpx_path, "r") as zf:
        for index, info in enumerate(zf.infolist()):
            data = zf.read(info.filename)
            is_xml = info.filename.endswith(".xml") or info.filename.endswith(".hpf")
            entry: dict[str, Any] = {
                "path": info.filename,
                "index": index,
                "compress_type": info.compress_type,
                "date_time": list(info.date_time),
                "create_system": info.create_system,
                "create_version": info.create_version,
                "extract_version": info.extract_version,
                "flag_bits": info.flag_bits,
                "internal_attr": info.internal_attr,
                "external_attr": info.external_attr,
                "extra_hash": _sha256(info.extra),
                "comment_hash": _sha256(info.comment),
                "file_size": info.file_size,
                "kind": "xml" if is_xml else "binary",
            }
            if is_xml:
                entry["structure_hash"] = _xml_structure_hash(data)
                entry["content_hash"] = _sha256(data)
                entry["text_ignored"] = "hp:t"
            else:
                entry["content_hash"] = _sha256(data)
            entries.append(entry)

    return {
        "version": 1,
        "source": str(hwpx_path),
        "rule": "all package entries and ZIP metadata compared; XML structure includes tags, attributes, order, and hp:t child controls while ignoring hp:t subtree text/tail content and hp:linesegarray layout caches",
        "entries": entries,
    }


def _entries_by_path(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(entry.get("path")): entry
        for entry in profile.get("entries", [])
        if entry.get("path")
    }


def compare_structure_profile(
    reference_profile: dict[str, Any],
    output_profile: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    ref_entries = _entries_by_path(reference_profile)
    out_entries = _entries_by_path(output_profile)

    ref_paths = list(ref_entries)
    out_paths = list(out_entries)
    if ref_paths != out_paths:
        errors.append("패키지 파일 목록/순서 불일치")
        missing = sorted(set(ref_paths) - set(out_paths))
        extra = sorted(set(out_paths) - set(ref_paths))
        if missing:
            errors.append(f"누락 파일: {', '.join(missing[:20])}")
        if extra:
            errors.append(f"추가 파일: {', '.join(extra[:20])}")

    for path, ref in ref_entries.items():
        out = out_entries.get(path)
        if out is None:
            continue
        for attr in (
            "kind",
            "compress_type",
            "date_time",
            "create_system",
            "create_version",
            "extract_version",
            "flag_bits",
            "internal_attr",
            "external_attr",
            "extra_hash",
            "comment_hash",
        ):
            if ref.get(attr) != out.get(attr):
                errors.append(
                    f"{path} {attr} 불일치: ref={ref.get(attr)}, out={out.get(attr)}"
                )

        if ref.get("kind") == "xml":
            if ref.get("structure_hash") != out.get("structure_hash"):
                errors.append(f"{path} XML 구조 fingerprint 불일치")
        else:
            if ref.get("content_hash") != out.get("content_hash"):
                errors.append(f"{path} 바이너리/부속 파일 해시 불일치")

    return errors


def _ratio_delta(a: int, b: int) -> float:
    base = max(a, 1)
    return abs(b - a) / base


def compare_metrics(
    ref: Metrics,
    out: Metrics,
    max_text_delta_ratio: float,
    max_paragraph_delta_ratio: float,
    allow_empty_fill: bool = False,
    max_empty_fill_chars: int = 80,
) -> List[str]:
    errors: List[str] = []

    if ref.paragraph_count != out.paragraph_count:
        errors.append(
            f"문단 수 불일치: ref={ref.paragraph_count}, out={out.paragraph_count}"
        )
    if ref.page_break_count != out.page_break_count:
        errors.append(
            f"명시적 pageBreak 수 불일치: ref={ref.page_break_count}, out={out.page_break_count}"
        )
    if ref.column_break_count != out.column_break_count:
        errors.append(
            f"명시적 columnBreak 수 불일치: ref={ref.column_break_count}, out={out.column_break_count}"
        )
    if ref.table_count != out.table_count:
        errors.append(f"표 수 불일치: ref={ref.table_count}, out={out.table_count}")
    if ref.table_shapes != out.table_shapes:
        errors.append("표 구조(rowCnt/colCnt/width/height/pageBreak) 불일치")

    td = _ratio_delta(ref.text_char_total_nospace, out.text_char_total_nospace)
    if td > max_text_delta_ratio:
        errors.append(
            "전체 텍스트 길이 편차 초과: "
            f"ref={ref.text_char_total_nospace}, out={out.text_char_total_nospace}, "
            f"delta={td:.2%}, limit={max_text_delta_ratio:.2%}"
        )

    if len(ref.paragraph_text_lengths) == len(out.paragraph_text_lengths):
        for idx, (a, b) in enumerate(
            zip(ref.paragraph_text_lengths, out.paragraph_text_lengths), start=1
        ):
            if a == 0 and b == 0:
                continue
            if allow_empty_fill and a == 0 and b <= max_empty_fill_chars:
                continue
            pd = _ratio_delta(a, b)
            if pd > max_paragraph_delta_ratio:
                errors.append(
                    f"{idx}번째 문단 텍스트 길이 편차 초과: "
                    f"ref={a}, out={b}, delta={pd:.2%}, limit={max_paragraph_delta_ratio:.2%}"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HWPX 레퍼런스 대비 페이지 드리프트 위험 검사"
    )
    parser.add_argument("--reference", "-r", required=True, help="기준 HWPX 경로")
    parser.add_argument("--output", "-o", help="결과 HWPX 경로")
    parser.add_argument(
        "--max-text-delta-ratio",
        type=float,
        default=0.15,
        help="전체 텍스트 길이 허용 편차 비율 (기본: 0.15)",
    )
    parser.add_argument(
        "--max-paragraph-delta-ratio",
        type=float,
        default=0.25,
        help="문단별 텍스트 길이 허용 편차 비율 (기본: 0.25)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="metrics를 JSON으로 출력",
    )
    parser.add_argument(
        "--write-budget",
        type=Path,
        help="기준 HWPX의 문단/셀별 글자 예산 프로파일을 JSON으로 저장",
    )
    parser.add_argument(
        "--budget-profile",
        type=Path,
        help="저장된 글자 예산 프로파일로 결과 HWPX를 검사",
    )
    parser.add_argument(
        "--write-structure",
        type=Path,
        help="기준 HWPX의 전체 패키지/XML 구조 fingerprint를 JSON으로 저장",
    )
    parser.add_argument(
        "--structure-profile",
        type=Path,
        help="저장된 구조 fingerprint로 결과 HWPX를 검사",
    )
    parser.add_argument(
        "--no-strict-paragraph-budget",
        action="store_true",
        help="예산 검사 시 일반 문단은 정확한 글자 수 일치를 요구하지 않음",
    )
    parser.add_argument(
        "--skip-text-drift",
        action="store_true",
        help="본문 재작성 결과에서 전체/문단 텍스트 길이 편차 휴리스틱을 건너뜀",
    )
    parser.add_argument(
        "--allow-empty-fill",
        action="store_true",
        help="기존 빈 문단/셀에 짧은 값을 채우는 양식 입력을 허용",
    )
    parser.add_argument(
        "--max-empty-fill-chars",
        type=int,
        default=80,
        help="--allow-empty-fill 시 빈 칸에 입력 가능한 최대 글자 수 (기본: 80)",
    )
    args = parser.parse_args()

    ref_path = Path(args.reference)

    if not ref_path.exists():
        print(f"Error: reference not found: {ref_path}", file=sys.stderr)
        return 2

    if args.write_budget:
        profile = collect_text_budget_profile(ref_path)
        args.write_budget.parent.mkdir(parents=True, exist_ok=True)
        args.write_budget.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"WROTE: {args.write_budget}")
        print(f"  slots: {len(profile.get('slots', []))}")
        if not args.output and not args.write_structure:
            return 0

    if args.write_structure:
        profile = collect_structure_profile(ref_path)
        args.write_structure.parent.mkdir(parents=True, exist_ok=True)
        args.write_structure.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"WROTE: {args.write_structure}")
        print(f"  entries: {len(profile.get('entries', []))}")
        if not args.output:
            return 0

    if not args.output:
        print("Error: --output is required unless only --write-budget is used", file=sys.stderr)
        return 2

    out_path = Path(args.output)
    if not out_path.exists():
        print(f"Error: output not found: {out_path}", file=sys.stderr)
        return 2

    ref = collect_metrics(ref_path)
    out = collect_metrics(out_path)

    if args.json:
        print(
            json.dumps(
                {"reference": asdict(ref), "output": asdict(out)},
                ensure_ascii=False,
                indent=2,
            )
        )

    if args.skip_text_drift:
        errors = []
        if ref.paragraph_count != out.paragraph_count:
            errors.append(
                f"문단 수 불일치: ref={ref.paragraph_count}, out={out.paragraph_count}"
            )
        if ref.page_break_count != out.page_break_count:
            errors.append(
                f"명시적 pageBreak 수 불일치: ref={ref.page_break_count}, out={out.page_break_count}"
            )
        if ref.column_break_count != out.column_break_count:
            errors.append(
                f"명시적 columnBreak 수 불일치: ref={ref.column_break_count}, out={out.column_break_count}"
            )
        if ref.table_count != out.table_count:
            errors.append(f"표 수 불일치: ref={ref.table_count}, out={out.table_count}")
        if ref.table_shapes != out.table_shapes:
            errors.append("표 구조(rowCnt/colCnt/width/height/pageBreak) 불일치")
    else:
        errors = compare_metrics(
            ref,
            out,
            max_text_delta_ratio=args.max_text_delta_ratio,
            max_paragraph_delta_ratio=args.max_paragraph_delta_ratio,
            allow_empty_fill=args.allow_empty_fill,
            max_empty_fill_chars=args.max_empty_fill_chars,
        )

    if args.budget_profile:
        if not args.budget_profile.is_file():
            print(f"Error: budget profile not found: {args.budget_profile}", file=sys.stderr)
            return 2
        budget_profile = json.loads(args.budget_profile.read_text(encoding="utf-8"))
        output_profile = collect_text_budget_profile(out_path)
        errors.extend(
            compare_text_budget_profile(
                budget_profile,
                output_profile,
                strict_paragraphs=not args.no_strict_paragraph_budget,
            )
        )

    if args.structure_profile:
        if not args.structure_profile.is_file():
            print(f"Error: structure profile not found: {args.structure_profile}", file=sys.stderr)
            return 2
        structure_profile = json.loads(args.structure_profile.read_text(encoding="utf-8"))
        output_structure = collect_structure_profile(out_path)
        errors.extend(compare_structure_profile(structure_profile, output_structure))

    if errors:
        print("FAIL: page-guard")
        for e in errors:
            print(f" - {e}")
        return 1

    print("PASS: page-guard")
    if args.budget_profile or args.structure_profile:
        print("  구조 fingerprint와 문단/셀별 글자 예산 검사를 통과했습니다.")
    else:
        print(
            "  paragraph/table/pageBreak 구조와 텍스트 길이 편차가 허용 범위 내입니다."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
