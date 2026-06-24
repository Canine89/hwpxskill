#!/usr/bin/env python3
"""Edit an existing HWPX while preserving its package and layout structure.

This script is intended for reference-form workflows: keep the original HWPX
archive, header.xml, content.hpf, BinData, table geometry, and style references
intact, then change only requested text nodes in Contents/section0.xml.

Examples:
    python3 scripts/edit_hwpx.py form.hwpx -o filled.hwpx \
      --replace "성명=홍길동" \
      --cell "0,2,1=서울특별시"

    python3 scripts/edit_hwpx.py form.hwpx -o filled.hwpx \
      --replace-json values.json
"""

from __future__ import annotations

import argparse
import binascii
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from lxml import etree

from page_guard import collect_text_budget_profile

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
}

SECTION_PATH = "Contents/section0.xml"
MIMETYPE = "mimetype"
EXPECTED_MIMETYPE = "application/hwp+zip"

LOCAL_SIG = 0x04034B50
CENTRAL_SIG = 0x02014B50
EOCD_SIG = 0x06054B50


@dataclass(frozen=True)
class CellTarget:
    table_index: int
    row: int
    col: int
    text: str


@dataclass(frozen=True)
class ParagraphTarget:
    index: int
    text: str


@dataclass(frozen=True)
class CharStyle:
    height: int
    text_color: str
    shade_color: str
    bold: bool
    underline: bool
    strikeout: bool


def clone_zip_info(info):
    cloned = type(info)(info.filename, info.date_time)
    cloned.compress_type = info.compress_type
    cloned.comment = info.comment
    cloned.extra = info.extra
    cloned.internal_attr = info.internal_attr
    cloned.external_attr = info.external_attr
    cloned.create_system = info.create_system
    cloned.create_version = info.create_version
    cloned.extract_version = info.extract_version
    cloned.flag_bits = info.flag_bits
    cloned.volume = info.volume
    return cloned


@dataclass
class RawZipEntry:
    name: str
    local_start: int
    data_start: int
    data_end: int
    local_header: bytes
    compressed_data: bytes
    central_header: bytes
    central_name: bytes
    central_extra: bytes
    central_comment: bytes
    version_made: int
    version_needed: int
    flag: int
    compress_type: int
    mod_time: int
    mod_date: int
    crc: int
    compress_size: int
    file_size: int
    disk_start: int
    internal_attr: int
    external_attr: int


def _find_eocd(data: bytes) -> int:
    start = max(0, len(data) - 65557)
    idx = data.rfind(struct.pack("<I", EOCD_SIG), start)
    if idx < 0:
        raise SystemExit("ZIP EOCD를 찾을 수 없습니다.")
    return idx


def _parse_raw_zip(path: Path) -> tuple[bytes, list[RawZipEntry], bytes]:
    data = path.read_bytes()
    eocd_offset = _find_eocd(data)
    eocd = data[eocd_offset:]
    (
        sig,
        disk_no,
        cd_disk,
        disk_entries,
        total_entries,
        cd_size,
        cd_offset,
        comment_len,
    ) = struct.unpack_from("<IHHHHIIH", eocd, 0)
    if sig != EOCD_SIG:
        raise SystemExit("ZIP EOCD 시그니처가 올바르지 않습니다.")
    if disk_no != 0 or cd_disk != 0 or disk_entries != total_entries:
        raise SystemExit("분할 ZIP은 지원하지 않습니다.")

    entries: list[RawZipEntry] = []
    pos = cd_offset
    for _ in range(total_entries):
        fields = struct.unpack_from("<IHHHHHHIIIHHHHHII", data, pos)
        (
            sig,
            version_made,
            version_needed,
            flag,
            compress_type,
            mod_time,
            mod_date,
            crc,
            compress_size,
            file_size,
            name_len,
            extra_len,
            comment_len,
            disk_start,
            internal_attr,
            external_attr,
            local_offset,
        ) = fields
        if sig != CENTRAL_SIG:
            raise SystemExit("중앙 디렉터리 시그니처가 올바르지 않습니다.")
        central_fixed = data[pos : pos + 46]
        central_name = data[pos + 46 : pos + 46 + name_len]
        central_extra = data[pos + 46 + name_len : pos + 46 + name_len + extra_len]
        central_comment = data[
            pos + 46 + name_len + extra_len : pos + 46 + name_len + extra_len + comment_len
        ]
        name = central_name.decode("utf-8")

        local = struct.unpack_from("<IHHHHHIIIHH", data, local_offset)
        (
            local_sig,
            _version_needed,
            _flag,
            _compress_type,
            _mod_time,
            _mod_date,
            _crc,
            _compress_size,
            _file_size,
            local_name_len,
            local_extra_len,
        ) = local
        if local_sig != LOCAL_SIG:
            raise SystemExit(f"로컬 헤더 시그니처가 올바르지 않습니다: {name}")
        local_header_len = 30 + local_name_len + local_extra_len
        data_start = local_offset + local_header_len
        data_end = data_start + compress_size
        local_header = data[local_offset:data_start]
        compressed_data = data[data_start:data_end]

        entries.append(
            RawZipEntry(
                name=name,
                local_start=local_offset,
                data_start=data_start,
                data_end=data_end,
                local_header=local_header,
                compressed_data=compressed_data,
                central_header=central_fixed,
                central_name=central_name,
                central_extra=central_extra,
                central_comment=central_comment,
                version_made=version_made,
                version_needed=version_needed,
                flag=flag,
                compress_type=compress_type,
                mod_time=mod_time,
                mod_date=mod_date,
                crc=crc,
                compress_size=compress_size,
                file_size=file_size,
                disk_start=disk_start,
                internal_attr=internal_attr,
                external_attr=external_attr,
            )
        )
        pos += 46 + name_len + extra_len + comment_len

    return data, entries, eocd


def _compress_like_original(data: bytes, compress_type: int) -> bytes:
    if compress_type == ZIP_STORED:
        return data
    if compress_type == ZIP_DEFLATED:
        compressor = zlib.compressobj(level=9, wbits=-15)
        return compressor.compress(data) + compressor.flush()
    raise SystemExit(f"지원하지 않는 압축 방식입니다: {compress_type}")


def _patch_local_header(
    header: bytes,
    crc: int,
    compress_size: int,
    file_size: int,
) -> bytes:
    out = bytearray(header)
    struct.pack_into("<III", out, 14, crc, compress_size, file_size)
    return bytes(out)


def _build_central_header(
    entry: RawZipEntry,
    crc: int,
    compress_size: int,
    file_size: int,
    local_offset: int,
) -> bytes:
    return (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            CENTRAL_SIG,
            entry.version_made,
            entry.version_needed,
            entry.flag,
            entry.compress_type,
            entry.mod_time,
            entry.mod_date,
            crc,
            compress_size,
            file_size,
            len(entry.central_name),
            len(entry.central_extra),
            len(entry.central_comment),
            entry.disk_start,
            entry.internal_attr,
            entry.external_attr,
            local_offset,
        )
        + entry.central_name
        + entry.central_extra
        + entry.central_comment
    )


def _build_eocd(original_eocd: bytes, entry_count: int, cd_size: int, cd_offset: int) -> bytes:
    out = bytearray(original_eocd)
    struct.pack_into("<HHII", out, 8, entry_count, entry_count, cd_size, cd_offset)
    return bytes(out)


def write_raw_preserving_zip(
    input_path: Path,
    output_path: Path,
    replacements: dict[str, bytes],
) -> None:
    _, entries, eocd = _parse_raw_zip(input_path)
    body = bytearray()
    central_parts: list[bytes] = []

    for entry in entries:
        local_offset = len(body)
        if entry.name in replacements:
            raw = replacements[entry.name]
            compressed = _compress_like_original(raw, entry.compress_type)
            crc = binascii.crc32(raw) & 0xFFFFFFFF
            compress_size = len(compressed)
            file_size = len(raw)
            local_header = _patch_local_header(
                entry.local_header,
                crc,
                compress_size,
                file_size,
            )
        else:
            compressed = entry.compressed_data
            crc = entry.crc
            compress_size = entry.compress_size
            file_size = entry.file_size
            local_header = entry.local_header

        body.extend(local_header)
        body.extend(compressed)
        central_parts.append(
            _build_central_header(
                entry,
                crc,
                compress_size,
                file_size,
                local_offset,
            )
        )

    cd_offset = len(body)
    central = b"".join(central_parts)
    body.extend(central)
    body.extend(_build_eocd(eocd, len(entries), len(central), cd_offset))
    output_path.write_bytes(bytes(body))


def _parse_xml(data: bytes) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    return etree.parse(BytesIO(data), parser)


def _parse_char_styles(header_bytes: bytes | None) -> dict[str, CharStyle]:
    if not header_bytes:
        return {}

    try:
        header_root = _parse_xml(header_bytes).getroot()
    except etree.XMLSyntaxError:
        return {}

    styles: dict[str, CharStyle] = {}
    for char_pr in header_root.xpath(".//hh:charPr", namespaces=NS):
        cid = char_pr.get("id")
        if not cid:
            continue
        try:
            height = int(char_pr.get("height", "1000"))
        except ValueError:
            height = 1000
        underline = False
        underline_el = char_pr.find("hh:underline", namespaces=NS)
        if underline_el is not None:
            underline = underline_el.get("type", "NONE").upper() != "NONE"
        strikeout = False
        strikeout_el = char_pr.find("hh:strikeout", namespaces=NS)
        if strikeout_el is not None:
            strikeout = strikeout_el.get("shape", "NONE").upper() != "NONE"
        styles[cid] = CharStyle(
            height=height,
            text_color=char_pr.get("textColor", "#000000").upper(),
            shade_color=char_pr.get("shadeColor", "none").lower(),
            bold=char_pr.find("hh:bold", namespaces=NS) is not None,
            underline=underline,
            strikeout=strikeout,
        )
    return styles


def _serialize_xml_like_source(tree: etree._ElementTree, source_bytes: bytes) -> bytes:
    """Serialize XML while keeping the source file's byte-level conventions.

    HWPX readers can be stricter than a normal XML parser. In particular, Hancom
    generated section XML uses a declaration with double quotes, a space before
    `?>`, no newline before the root element, and CRLF inside some text nodes.
    lxml normalizes those details unless we restore them.
    """

    data = etree.tostring(
        tree,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )

    if source_bytes.startswith(b"<?xml"):
        src_decl_end = source_bytes.find(b"?>")
        out_decl_end = data.find(b"?>")
        if src_decl_end >= 0 and out_decl_end >= 0:
            source_decl = source_bytes[: src_decl_end + 2]
            body = data[out_decl_end + 2 :]
            if body.startswith(b"\n") and not source_bytes[src_decl_end + 2 :].startswith(
                b"\n"
            ):
                body = body[1:]
            data = source_decl + body

    if b"\r\n" in source_bytes:
        data = data.replace(b"\n", b"\r\n")

    return data


def _parse_key_value(raw: str, option: str) -> tuple[str, str]:
    if "=" not in raw:
        raise SystemExit(f"{option} 값은 OLD=NEW 형식이어야 합니다: {raw}")
    key, value = raw.split("=", 1)
    if not key:
        raise SystemExit(f"{option}의 OLD 값이 비어 있습니다: {raw}")
    return key, value


def _load_replacements(items: Iterable[str], json_path: Path | None) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for item in items:
        old, new = _parse_key_value(item, "--replace")
        replacements[old] = new

    if json_path:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit("--replace-json 파일은 JSON 객체여야 합니다.")
        for old, new in data.items():
            replacements[str(old)] = "" if new is None else str(new)

    return replacements


def _parse_cell_target(raw: str) -> CellTarget:
    coords, text = _parse_key_value(raw, "--cell")
    parts = [p.strip() for p in coords.split(",")]
    if len(parts) == 2:
        table_index = 0
        row_s, col_s = parts
    elif len(parts) == 3:
        table_s, row_s, col_s = parts
        table_index = int(table_s)
    else:
        raise SystemExit(
            "--cell은 row,col=TEXT 또는 table,row,col=TEXT 형식이어야 합니다."
        )

    row = int(row_s)
    col = int(col_s)
    if table_index < 0 or row < 0 or col < 0:
        raise SystemExit("--cell 좌표는 0 이상의 정수여야 합니다.")
    return CellTarget(table_index=table_index, row=row, col=col, text=text)


def _parse_paragraph_target(raw: str) -> ParagraphTarget:
    index_s, text = _parse_key_value(raw, "--paragraph")
    try:
        index = int(index_s)
    except ValueError:
        raise SystemExit(f"--paragraph 인덱스는 정수여야 합니다: {index_s}")
    if index < 0:
        raise SystemExit("--paragraph 인덱스는 0 이상의 정수여야 합니다.")
    return ParagraphTarget(index=index, text=text)


def _load_paragraph_targets(
    items: Iterable[str],
    json_path: Path | None,
) -> list[ParagraphTarget]:
    targets = [_parse_paragraph_target(item) for item in items]

    if json_path:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            iterable = data.items()
        elif isinstance(data, list):
            iterable = []
            parsed: list[ParagraphTarget] = []
            for item in data:
                if not isinstance(item, dict) or "index" not in item or "text" not in item:
                    raise SystemExit(
                        "--paragraph-json 배열 항목은 {\"index\": 0, \"text\": \"...\"} 형식이어야 합니다."
                    )
                parsed.append(ParagraphTarget(int(item["index"]), str(item["text"])))
            targets.extend(parsed)
            iterable = []
        else:
            raise SystemExit("--paragraph-json 파일은 JSON 객체 또는 배열이어야 합니다.")

        for index_s, text in iterable:
            targets.append(ParagraphTarget(int(index_s), "" if text is None else str(text)))

    seen: set[int] = set()
    for target in targets:
        if target.index in seen:
            raise SystemExit(f"중복 문단 입력: {target.index}")
        seen.add(target.index)
    return targets


def _parse_slot_key_value(raw: str) -> tuple[str, str]:
    return _parse_key_value(raw, "--slot")


def _load_slot_values(items: Iterable[str], json_path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        key, value = _parse_slot_key_value(item)
        values[key] = value

    if json_path:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit("--slot-json 파일은 JSON 객체여야 합니다.")
        for key, value in data.items():
            values[str(key)] = "" if value is None else str(value)

    return values


def _targets_from_slots(slot_values: dict[str, str]) -> tuple[list[ParagraphTarget], list[CellTarget]]:
    paragraphs: list[ParagraphTarget] = []
    cells: list[CellTarget] = []
    for key, text in slot_values.items():
        parts = key.split(":")
        if len(parts) == 2 and parts[0] == "p":
            paragraphs.append(ParagraphTarget(int(parts[1]), text))
        elif len(parts) in (4, 5) and parts[0] == "cell":
            table = int(parts[1])
            row = int(parts[2])
            col = int(parts[3])
            occurrence = int(parts[4]) if len(parts) == 5 else 0
            if occurrence != 0:
                raise SystemExit(f"중복 좌표 셀 occurrence=0만 --slot으로 채울 수 있습니다: {key}")
            cells.append(CellTarget(table, row, col, text))
        else:
            raise SystemExit(f"지원하지 않는 슬롯 키입니다: {key}")
    return paragraphs, cells


def _normalized_len(text: str) -> int:
    return len("".join(text.split()))


def _quality_errors(label: str, text: str, max_hangul_run: int) -> list[str]:
    errors: list[str] = []
    if max_hangul_run <= 0:
        return errors
    for match in re.finditer(r"[가-힣]{" + str(max_hangul_run + 1) + r",}", text):
        sample = match.group(0)
        errors.append(
            f"{label}: 띄어쓰기 없는 한글 문자열이 너무 깁니다"
            f"({len(sample)}자): {sample[:40]!r}"
        )
    return errors


def _text_nodes(scope: etree._Element) -> list[etree._Element]:
    return scope.xpath(".//hp:t", namespaces=NS)


def _node_text(node: etree._Element) -> str:
    return "".join(node.itertext())


def _scope_text(scope: etree._Element) -> str:
    return "".join(_node_text(t) for t in _text_nodes(scope))


def _direct_run_text_of(paragraph: etree._Element) -> str:
    return "".join(
        _node_text(t)
        for t in paragraph.xpath("./hp:run/hp:t", namespaces=NS)
    )


def _ancestor_paragraph(el: etree._Element) -> etree._Element | None:
    current: etree._Element | None = el
    while current is not None:
        qname = etree.QName(current)
        if qname.namespace == NS["hp"] and qname.localname == "p":
            return current
        current = current.getparent()
    return None


def _remove_linesegarrays(scope: etree._Element) -> int:
    removed = 0
    for node in list(scope.xpath(".//hp:linesegarray", namespaces=NS)):
        parent = node.getparent()
        if parent is None:
            continue
        parent.remove(node)
        removed += 1
    return removed


def _run_for_text_node(text_node: etree._Element) -> etree._Element | None:
    parent = text_node.getparent()
    if parent is None:
        return None
    qname = etree.QName(parent)
    if qname.namespace == NS["hp"] and qname.localname == "run":
        return parent
    return None


def _char_style_for_text_node(
    text_node: etree._Element,
    char_styles: dict[str, CharStyle],
) -> CharStyle | None:
    run = _run_for_text_node(text_node)
    if run is None:
        return None
    return char_styles.get(run.get("charPrIDRef", "0"))


def _style_emphasis_score(style: CharStyle | None, base_height: int | None = None) -> int:
    if style is None:
        return 100

    score = 0
    if style.bold:
        score += 1000
    if style.text_color not in ("#000000", "NONE", ""):
        score += 1000
    if style.shade_color not in ("none", "#ffffff", "#FFFFFF".lower(), ""):
        score += 600
    if style.underline:
        score += 500
    if base_height is not None:
        score += min(abs(style.height - base_height) // 10, 300)
    elif style.height < 850 or style.height > 1300:
        score += min(abs(style.height - 1000) // 10, 300)
    return score


def _is_plain_body_style(style: CharStyle | None) -> bool:
    if style is None:
        return False
    return (
        not style.bold
        and style.text_color in ("#000000", "NONE", "")
        and style.shade_color in ("none", "#ffffff", "")
        and not style.underline
    )


def _dominant_plain_height(
    nodes: Iterable[etree._Element],
    char_styles: dict[str, CharStyle],
) -> int | None:
    weights: dict[int, int] = {}
    for node in nodes:
        style = _char_style_for_text_node(node, char_styles)
        if not _is_plain_body_style(style):
            continue
        text_len = len(_node_text(node).strip())
        if text_len <= 0:
            continue
        weights[style.height] = weights.get(style.height, 0) + text_len
    if not weights:
        return None
    return max(weights.items(), key=lambda item: (item[1], item[0]))[0]


def _best_text_node_for_plain_rewrite(
    scope: etree._Element,
    char_styles: dict[str, CharStyle],
) -> etree._Element | None:
    nodes = _text_nodes(scope)
    if not nodes:
        return None

    nonempty = [node for node in nodes if _node_text(node).strip()]
    candidates = nonempty or nodes
    max_len = max((len(_node_text(node).strip()) for node in candidates), default=0)
    meaningful_min = max(2, int(max_len * 0.2))
    meaningful = [
        node for node in candidates if len(_node_text(node).strip()) >= meaningful_min
    ]
    if meaningful:
        candidates = meaningful
    base_height = _dominant_plain_height(candidates, char_styles)

    def sort_key(node: etree._Element) -> tuple[int, int]:
        style = _char_style_for_text_node(node, char_styles)
        return (_style_emphasis_score(style, base_height), -len(_node_text(node).strip()))

    return min(candidates, key=sort_key)


def _put_text_preserving_first_run(
    scope: etree._Element,
    text: str,
    char_styles: dict[str, CharStyle] | None = None,
) -> bool:
    """Put text into the first hp:t in scope and clear the rest.

    This preserves paragraph/run/cell/table elements and the first run's
    charPrIDRef, while avoiding new structure. It is the safest fallback when a
    replacement spans multiple runs.
    """

    nodes = _text_nodes(scope)
    if not nodes:
        runs = scope.xpath(".//hp:run", namespaces=NS)
        if not runs:
            return False
        t_node = etree.SubElement(
            runs[0],
            f"{{{NS['hp']}}}t",
        )
        t_node.text = text
        _remove_linesegarrays(scope)
        return True

    if char_styles:
        target = _best_text_node_for_plain_rewrite(scope, char_styles) or nodes[0]
    else:
        target = next((n for n in nodes if _node_text(n)), nodes[0])
    target.text = text
    for child in target:
        child.tail = ""
    for n in nodes:
        if n is not target:
            n.text = ""
            for child in n:
                child.tail = ""
    _remove_linesegarrays(scope)
    return True


def _direct_text_nodes(paragraph: etree._Element) -> list[etree._Element]:
    return paragraph.xpath("./hp:run/hp:t", namespaces=NS)


def _is_plain_editable_paragraph(paragraph: etree._Element) -> bool:
    if paragraph.xpath(".//hp:p", namespaces=NS):
        return False
    return bool(_direct_text_nodes(paragraph))


def _paragraph_primary_text_node(
    paragraph: etree._Element,
    char_styles: dict[str, CharStyle],
) -> etree._Element | None:
    if char_styles:
        return _best_text_node_for_plain_rewrite(paragraph, char_styles)
    nodes = _direct_text_nodes(paragraph)
    if not nodes:
        return None
    return max(nodes, key=lambda node: len(_node_text(node)))


def _set_paragraph_plain_text(
    paragraph: etree._Element,
    text: str,
    char_styles: dict[str, CharStyle],
) -> bool:
    """Replace a paragraph with readable plain text using one dominant run.

    This avoids slicing a sentence across pre-existing bold/italic runs. It is
    intended for real document rewrites, not small placeholder filling.
    """

    if not _is_plain_editable_paragraph(paragraph):
        return False

    target = _paragraph_primary_text_node(paragraph, char_styles)
    if target is None:
        return False

    for node in _text_nodes(paragraph):
        node.text = ""
        for child in node:
            child.tail = ""

    target.text = text
    _remove_linesegarrays(paragraph)
    return True


def set_paragraphs(
    root: etree._Element,
    targets: Iterable[ParagraphTarget],
    char_styles: dict[str, CharStyle],
) -> int:
    paragraphs = root.xpath(".//hp:p", namespaces=NS)
    changed = 0
    for target in targets:
        if target.index >= len(paragraphs):
            raise SystemExit(
                f"문단 인덱스 범위 초과: {target.index} "
                f"(문서 문단 수: {len(paragraphs)})"
            )
        if not _set_paragraph_plain_text(paragraphs[target.index], target.text, char_styles):
            raise SystemExit(
                f"문단을 직접 편집할 수 없습니다: {target.index}. "
                "표/그림/텍스트상자를 품은 컨테이너 문단이거나 직접 hp:t가 없습니다. "
                "내부 실제 텍스트 문단 인덱스를 사용하세요."
            )
        changed += 1
    return changed


def replace_text(
    root: etree._Element,
    replacements: dict[str, str],
    char_styles: dict[str, CharStyle],
) -> int:
    """Replace text while keeping existing paragraph and run structure."""

    changed = 0

    # Fast path: replace inside individual hp:t nodes. This keeps mixed styling
    # intact when the target text is not split across runs.
    remaining = dict(replacements)
    for t in root.xpath(".//hp:t", namespaces=NS):
        if t.text is None:
            continue
        new_text = t.text
        for old, new in replacements.items():
            if old in new_text:
                new_text = new_text.replace(old, new)
        if new_text != t.text:
            t.text = new_text
            paragraph = _ancestor_paragraph(t)
            if paragraph is not None:
                _remove_linesegarrays(paragraph)
            changed += 1

    # Slow path: if a placeholder is split across runs inside a paragraph, edit
    # the paragraph-level text and clear extra text nodes. Structural elements
    # are still preserved.
    for old, new in remaining.items():
        for p in root.xpath(".//hp:p", namespaces=NS):
            text = _scope_text(p)
            if old not in text:
                continue
            replaced = text.replace(old, new)
            if replaced != text and _put_text_preserving_first_run(p, replaced, char_styles):
                changed += 1

    return changed


def _find_cell(root: etree._Element, target: CellTarget) -> etree._Element:
    tables = root.xpath(".//hp:tbl", namespaces=NS)
    if target.table_index >= len(tables):
        raise SystemExit(
            f"표 인덱스 범위 초과: {target.table_index} "
            f"(문서 표 수: {len(tables)})"
        )

    table = tables[target.table_index]
    for cell in table.xpath(".//hp:tc", namespaces=NS):
        addr = cell.find("hp:cellAddr", namespaces=NS)
        if addr is None:
            continue
        if (
            int(addr.get("rowAddr", "-1")) == target.row
            and int(addr.get("colAddr", "-1")) == target.col
        ):
            return cell

    raise SystemExit(
        f"셀을 찾을 수 없습니다: table={target.table_index}, "
        f"row={target.row}, col={target.col}"
    )


def set_cells(root: etree._Element, targets: Iterable[CellTarget]) -> int:
    changed = 0
    for target in targets:
        cell = _find_cell(root, target)
        if not _put_text_preserving_first_run(cell, target.text):
            raise SystemExit(
                f"셀에 hp:t 텍스트 노드가 없습니다: "
                f"table={target.table_index}, row={target.row}, col={target.col}"
            )
        changed += 1
    return changed


def _budget_cells_by_coord(input_path: Path) -> dict[tuple[int, int, int, int], int]:
    profile = collect_text_budget_profile(input_path)
    budgets: dict[tuple[int, int, int, int], int] = {}
    for slot in profile.get("slots", []):
        if slot.get("kind") != "cell":
            continue
        meta = slot.get("meta", {})
        try:
            key = (
                int(meta.get("table", 0)),
                int(meta.get("row", 0)),
                int(meta.get("col", 0)),
                int(meta.get("occurrence", 0)),
            )
            budgets[key] = int(slot.get("max_chars", 0))
        except (TypeError, ValueError):
            continue
    return budgets


def _paragraph_budgets_by_index(input_path: Path) -> dict[int, int]:
    with ZipFile(input_path, "r") as zf:
        section_tree = _parse_xml(zf.read(SECTION_PATH))
    budgets: dict[int, int] = {}
    for idx, paragraph in enumerate(section_tree.getroot().xpath(".//hp:p", namespaces=NS)):
        budgets[idx] = _normalized_len(_direct_run_text_of(paragraph))
    return budgets


def preflight_text_budget(
    input_path: Path,
    replacements: dict[str, str],
    cells: Iterable[CellTarget],
    paragraphs: Iterable[ParagraphTarget],
    max_hangul_run: int,
) -> None:
    errors: list[str] = []

    for old, new in replacements.items():
        errors.extend(_quality_errors(f"--replace {old!r}", new, max_hangul_run))
        old_len = _normalized_len(old)
        new_len = _normalized_len(new)
        if new_len > old_len:
            errors.append(
                f"치환값 글자 수 초과: {old!r}({old_len}) -> {new!r}({new_len})"
            )

    cell_budgets = _budget_cells_by_coord(input_path)
    for cell in cells:
        errors.extend(
            _quality_errors(
                f"--cell table={cell.table_index}, row={cell.row}, col={cell.col}",
                cell.text,
                max_hangul_run,
            )
        )
        key = (cell.table_index, cell.row, cell.col, 0)
        max_chars = cell_budgets.get(key)
        new_len = _normalized_len(cell.text)
        if max_chars is None:
            errors.append(
                f"셀 예산을 찾을 수 없습니다: table={cell.table_index}, "
                f"row={cell.row}, col={cell.col}"
            )
            continue
        if new_len > max_chars:
            errors.append(
                f"셀 입력값 글자 수 초과: table={cell.table_index}, "
                f"row={cell.row}, col={cell.col}, max={max_chars}, "
                f"input={new_len}, text={cell.text!r}"
            )

    paragraph_budgets = _paragraph_budgets_by_index(input_path)
    for paragraph in paragraphs:
        with ZipFile(input_path, "r") as zf:
            section_tree = _parse_xml(zf.read(SECTION_PATH))
        all_paragraphs = section_tree.getroot().xpath(".//hp:p", namespaces=NS)
        if paragraph.index >= len(all_paragraphs) or not _is_plain_editable_paragraph(
            all_paragraphs[paragraph.index]
        ):
            errors.append(
                f"문단을 직접 편집할 수 없습니다: index={paragraph.index}. "
                "표/그림/텍스트상자 컨테이너가 아닌 내부 실제 텍스트 문단을 선택하세요."
            )
            continue
        errors.extend(
            _quality_errors(
                f"--paragraph {paragraph.index}",
                paragraph.text,
                max_hangul_run,
            )
        )
        max_chars = paragraph_budgets.get(paragraph.index)
        new_len = _normalized_len(paragraph.text)
        if max_chars is None:
            errors.append(f"문단 예산을 찾을 수 없습니다: index={paragraph.index}")
            continue
        if new_len > max_chars:
            errors.append(
                f"문단 입력값 글자 수 초과: index={paragraph.index}, "
                f"max={max_chars}, input={new_len}, text={paragraph.text!r}"
            )

    if errors:
        raise SystemExit("입력 전 글자수 예산 검사 실패:\n - " + "\n - ".join(errors))


def _validate_input(zf: ZipFile) -> None:
    names = zf.namelist()
    if MIMETYPE not in names:
        raise SystemExit("mimetype 파일이 없습니다.")
    if SECTION_PATH not in names:
        raise SystemExit(f"{SECTION_PATH} 파일이 없습니다.")
    mimetype = zf.read(MIMETYPE).decode("utf-8").strip()
    if mimetype != EXPECTED_MIMETYPE:
        raise SystemExit(f"지원하지 않는 mimetype입니다: {mimetype}")


def _pack_from_original(
    input_path: Path,
    output_path: Path,
    replacements: dict[str, str],
    cells: list[CellTarget],
    paragraphs: list[ParagraphTarget],
) -> tuple[int, int, int]:
    try:
        source = ZipFile(input_path, "r")
    except BadZipFile:
        raise SystemExit(f"올바른 ZIP/HWPX 파일이 아닙니다: {input_path}")

    with source as src:
        _validate_input(src)
        names = src.namelist()
        source_section = src.read(SECTION_PATH)
        header_bytes = src.read("Contents/header.xml") if "Contents/header.xml" in names else None
        char_styles = _parse_char_styles(header_bytes)
        section_tree = _parse_xml(source_section)
        section_root = section_tree.getroot()

        text_changes = replace_text(section_root, replacements, char_styles)
        cell_changes = set_cells(section_root, cells)
        paragraph_changes = set_paragraphs(section_root, paragraphs, char_styles)
        section_bytes = _serialize_xml_like_source(section_tree, source_section)

        write_raw_preserving_zip(
            input_path,
            output_path,
            {SECTION_PATH: section_bytes},
        )

    return text_changes, cell_changes, paragraph_changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="원본 HWPX 양식을 보존하면서 텍스트/표 셀 내용만 수정"
    )
    parser.add_argument("input", type=Path, help="기준/원본 .hwpx 파일")
    parser.add_argument("--output", "-o", type=Path, required=True, help="결과 .hwpx")
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="문서 전체 텍스트 치환. 여러 번 지정 가능",
    )
    parser.add_argument(
        "--replace-json",
        type=Path,
        help="치환 매핑 JSON 파일. 예: {\"{{name}}\": \"홍길동\"}",
    )
    parser.add_argument(
        "--cell",
        action="append",
        default=[],
        metavar="[TABLE,]ROW,COL=TEXT",
        help="표 셀 텍스트 설정. 좌표는 0부터 시작",
    )
    parser.add_argument(
        "--paragraph",
        action="append",
        default=[],
        metavar="INDEX=TEXT",
        help="문단 인덱스에 자연문 텍스트 입력. 기존 문단의 주 서식을 사용하고 문장 쪼개기를 피함",
    )
    parser.add_argument(
        "--paragraph-json",
        type=Path,
        help='문단 입력 JSON. 예: {"12": "본문..."} 또는 [{"index": 12, "text": "본문..."}]',
    )
    parser.add_argument(
        "--slot",
        action="append",
        default=[],
        metavar="SLOT=TEXT",
        help="hwpx_slots.py가 출력한 슬롯 키로 값 입력. 예: p:12=본문, cell:0:2:1=값",
    )
    parser.add_argument(
        "--slot-json",
        type=Path,
        help='슬롯 입력 JSON. 예: {"p:12": "본문", "cell:0:2:1": "값"}',
    )
    parser.add_argument(
        "--max-hangul-run",
        type=int,
        default=24,
        help="띄어쓰기 없이 이어진 한글 최대 허용 길이. 0이면 품질 검사 비활성화",
    )
    parser.add_argument(
        "--allow-over-budget",
        action="store_true",
        help="입력 전 글자수 예산 검사를 우회",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Error: input not found: {args.input}", file=sys.stderr)
        return 2

    replacements = _load_replacements(args.replace, args.replace_json)
    cells = [_parse_cell_target(item) for item in args.cell]
    paragraphs = _load_paragraph_targets(args.paragraph, args.paragraph_json)
    slot_paragraphs, slot_cells = _targets_from_slots(
        _load_slot_values(args.slot, args.slot_json)
    )
    paragraphs.extend(slot_paragraphs)
    cells.extend(slot_cells)
    if not replacements and not cells and not paragraphs:
        print(
            "Error: --replace, --replace-json, --cell, --paragraph, --paragraph-json, --slot, --slot-json 중 하나가 필요합니다.",
            file=sys.stderr,
        )
        return 2

    if not args.allow_over_budget:
        preflight_text_budget(
            args.input,
            replacements,
            cells,
            paragraphs,
            args.max_hangul_run,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    text_changes, cell_changes, paragraph_changes = _pack_from_original(
        args.input,
        args.output,
        replacements,
        cells,
        paragraphs,
    )

    print(f"EDITED: {args.output}")
    print(f"  text replacements applied: {text_changes}")
    print(f"  cells updated: {cell_changes}")
    print(f"  paragraphs updated: {paragraph_changes}")
    print("  original package entries, header.xml, content.hpf, and BinData were preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
