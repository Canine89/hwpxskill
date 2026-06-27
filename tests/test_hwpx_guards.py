#!/usr/bin/env python3
"""Regression tests for HWPX form-preservation guards."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import edit_hwpx  # noqa: E402
import content_guard  # noqa: E402
import finalize_hwpx  # noqa: E402
import fix_namespaces  # noqa: E402
import gonmun_lint  # noqa: E402
import page_guard  # noqa: E402
from office import pack as office_pack  # noqa: E402
from office import unpack as office_unpack  # noqa: E402

NS = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph"}
FIXTURE = ROOT / "251211_2026년_고용노동부_업무보고_보도자료(수정).hwpx"


def hp_t_child_count(hwpx_path: Path) -> int:
    with ZipFile(hwpx_path, "r") as zf:
        root = etree.fromstring(zf.read("Contents/section0.xml"))
    return len(root.xpath(".//hp:t/*", namespaces=NS))


def linesegarray_count(hwpx_path: Path) -> int:
    with ZipFile(hwpx_path, "r") as zf:
        root = etree.fromstring(zf.read("Contents/section0.xml"))
    return len(root.xpath(".//hp:linesegarray", namespaces=NS))


class HwpxGuardTests(unittest.TestCase):
    def test_office_unpack_default_preserves_xml_mixed_content_bytes(self) -> None:
        section = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            b'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b'<hp:p><hp:run><hp:t>Item<hp:fwSpace/>text</hp:t></hp:run></hp:p>'
            b'</hs:sec>'
        )

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.hwpx"
            unpacked = Path(tmp) / "unpacked"
            repacked = Path(tmp) / "repacked.hwpx"

            with ZipFile(source, "w") as zf:
                zf.writestr("mimetype", b"application/hwp+zip")
                zf.writestr("Contents/section0.xml", section)

            office_unpack.unpack(str(source), str(unpacked))
            self.assertEqual(section, (unpacked / "Contents/section0.xml").read_bytes())

            office_pack.pack(str(unpacked), str(repacked))
            with ZipFile(repacked, "r") as zf:
                self.assertEqual(section, zf.read("Contents/section0.xml"))

    def test_finalize_strips_linesegarray_preserving_unmodified_entries(self) -> None:
        section = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            b'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b'<hp:p><hp:run><hp:t>text</hp:t></hp:run>'
            b'<hp:linesegarray><hp:lineseg textpos="0"/></hp:linesegarray></hp:p>'
            b'</hs:sec>'
        )
        header = b'<?xml version="1.0" encoding="UTF-8"?><hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"/>'

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.hwpx"
            out = Path(tmp) / "out.hwpx"
            with ZipFile(source, "w") as zf:
                zf.writestr("mimetype", b"application/hwp+zip")
                zf.writestr("Contents/header.xml", header)
                zf.writestr("Contents/section0.xml", section)

            before = page_guard.collect_structure_profile(source)
            removed = finalize_hwpx.strip_linesegarray(source, out)
            after = page_guard.collect_structure_profile(out)

            self.assertEqual(1, removed)
            with ZipFile(out, "r") as zf:
                self.assertNotIn(b"linesegarray", zf.read("Contents/section0.xml"))
                self.assertEqual(header, zf.read("Contents/header.xml"))
            self.assertEqual([], page_guard.compare_structure_profile(before, after))

    def test_fix_namespaces_updates_prefixes_and_header_counts(self) -> None:
        header = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<ns0:head xmlns:ns0="http://www.hancom.co.kr/hwpml/2011/head">'
            b'<ns0:charProperties itemCnt="1">'
            b'<ns0:charPr id="0"/><ns0:charPr id="1"/>'
            b'</ns0:charProperties>'
            b'</ns0:head>'
        )
        section = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<ns1:sec xmlns:ns1="http://www.hancom.co.kr/hwpml/2011/section" '
            b'xmlns:ns2="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b'<ns2:p><ns2:run><ns2:t>text</ns2:t></ns2:run></ns2:p>'
            b'</ns1:sec>'
        )

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.hwpx"
            out = Path(tmp) / "out.hwpx"
            with ZipFile(source, "w") as zf:
                zf.writestr("mimetype", b"application/hwp+zip")
                zf.writestr("Contents/header.xml", header)
                zf.writestr("Contents/section0.xml", section)

            changed = fix_namespaces.fix_hwpx_namespaces(source, out)

            self.assertEqual(2, changed)
            with ZipFile(out, "r") as zf:
                fixed_header = zf.read("Contents/header.xml").decode("utf-8")
                fixed_section = zf.read("Contents/section0.xml").decode("utf-8")
            self.assertIn("xmlns:hh=", fixed_header)
            self.assertIn('itemCnt="2"', fixed_header)
            self.assertIn("xmlns:hs=", fixed_section)
            self.assertIn("<hp:p>", fixed_section)

    def test_gonmun_lint_detects_common_style_errors(self) -> None:
        result = gonmun_lint.lint_text("2025.01.06 오후 3시 붙임: 계획 1부")
        summary = result["summary"]
        self.assertIsInstance(summary, dict)
        self.assertFalse(summary["ok"])
        rules = {finding["rule"] for finding in result["findings"]}
        self.assertIn("DATE_NO_SPACE", rules)
        self.assertIn("TIME_AMPM", rules)
        self.assertIn("BUNIM_COLON", rules)

    def test_section_xml_noop_roundtrip_is_byte_identical(self) -> None:
        with ZipFile(FIXTURE, "r") as zf:
            source = zf.read("Contents/section0.xml")

        tree = edit_hwpx._parse_xml(source)
        serialized = edit_hwpx._serialize_xml_like_source(tree, source)

        self.assertEqual(source, serialized)

    def test_edit_preserves_hp_t_child_controls(self) -> None:
        self.assertTrue(FIXTURE.is_file(), f"missing fixture: {FIXTURE}")
        before = hp_t_child_count(FIXTURE)
        self.assertGreater(before, 0, "fixture must contain hp:t child controls")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "edited.hwpx"
            edit_hwpx._pack_from_original(
                FIXTURE,
                out,
                {"고용노동부": "미국노동부"},
                [],
                [],
            )
            after = hp_t_child_count(out)

            self.assertEqual(before, after)

    def test_edit_removes_invalidated_line_layout_cache(self) -> None:
        before = linesegarray_count(FIXTURE)
        self.assertGreater(before, 0, "fixture must contain hp:linesegarray caches")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "edited.hwpx"
            edit_hwpx._pack_from_original(
                FIXTURE,
                out,
                {"고용노동부": "미국노동부"},
                [],
                [],
            )
            after = linesegarray_count(out)

            self.assertLess(after, before)

    def test_structure_profile_allows_removed_line_layout_cache(self) -> None:
        profile = page_guard.collect_structure_profile(FIXTURE)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "without-linesegarray.hwpx"
            with ZipFile(FIXTURE, "r") as zf:
                source = zf.read("Contents/section0.xml")
            tree = edit_hwpx._parse_xml(source)
            root = tree.getroot()
            removed = edit_hwpx._remove_linesegarrays(root)
            self.assertGreater(removed, 0)
            section = edit_hwpx._serialize_xml_like_source(tree, source)
            edit_hwpx.write_raw_preserving_zip(
                FIXTURE,
                out,
                {"Contents/section0.xml": section},
            )

            output_profile = page_guard.collect_structure_profile(out)
            errors = page_guard.compare_structure_profile(profile, output_profile)

            self.assertEqual([], errors)

    def test_edit_preserves_stable_zip_metadata(self) -> None:
        stable_fields = [
            "CRC",
            "compress_size",
            "file_size",
            "flag_bits",
            "date_time",
            "create_system",
            "create_version",
            "extract_version",
            "external_attr",
            "internal_attr",
            "compress_type",
        ]

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "edited.hwpx"
            edit_hwpx._pack_from_original(
                FIXTURE,
                out,
                {"고용노동부": "미국노동부"},
                [],
                [],
            )

            with ZipFile(FIXTURE, "r") as ref_zip, ZipFile(out, "r") as out_zip:
                self.assertEqual(ref_zip.namelist(), out_zip.namelist())
                ref_infos = {info.filename: info for info in ref_zip.infolist()}
                out_infos = {info.filename: info for info in out_zip.infolist()}
                for name, ref_info in ref_infos.items():
                    if name == "Contents/section0.xml":
                        continue
                    out_info = out_infos[name]
                    for field in stable_fields:
                        self.assertEqual(
                            getattr(ref_info, field),
                            getattr(out_info, field),
                            f"{name} {field}",
                        )

    def test_paragraph_rewrite_uses_single_plain_run_and_budget_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "paragraph.hwpx"
            edit_hwpx.preflight_text_budget(
                FIXTURE,
                {},
                [],
                [
                    edit_hwpx.ParagraphTarget(
                        18,
                        "미국노동부는 현장 중심의 노동정책을 추진한다.",
                    )
                ],
                24,
            )
            edit_hwpx._pack_from_original(
                FIXTURE,
                out,
                {},
                [],
                [
                    edit_hwpx.ParagraphTarget(
                        18,
                        "미국노동부는 현장 중심의 노동정책을 추진한다.",
                    )
                ],
            )

            with ZipFile(out, "r") as zf:
                root = etree.fromstring(zf.read("Contents/section0.xml"))
            paragraph = root.xpath(".//hp:p", namespaces=NS)[18]
            filled_nodes = [
                node
                for node in paragraph.xpath(".//hp:t", namespaces=NS)
                if "".join(node.itertext())
            ]
            self.assertEqual(1, len(filled_nodes))
            filled_run = filled_nodes[0].getparent()
            self.assertEqual(
                "미국노동부는 현장 중심의 노동정책을 추진한다.",
                "".join(paragraph.xpath(".//hp:t//text()", namespaces=NS)),
            )
            self.assertEqual("58", filled_run.get("charPrIDRef"))

    def test_paragraph_rewrite_prefers_dominant_body_height(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "paragraph-body-height.hwpx"
            edit_hwpx._pack_from_original(
                FIXTURE,
                out,
                {},
                [],
                [
                    edit_hwpx.ParagraphTarget(
                        12,
                        "미국노동부는 워싱턴에서 새 노동정책 계획을 발표했다.",
                    )
                ],
            )

            with ZipFile(out, "r") as zf:
                root = etree.fromstring(zf.read("Contents/section0.xml"))
            paragraph = root.xpath(".//hp:p", namespaces=NS)[12]
            filled_nodes = [
                node
                for node in paragraph.xpath(".//hp:t", namespaces=NS)
                if "".join(node.itertext())
            ]
            self.assertEqual(1, len(filled_nodes))
            filled_run = filled_nodes[0].getparent()
            self.assertEqual("128", filled_run.get("charPrIDRef"))

    def test_quality_guard_rejects_glued_korean_text(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            edit_hwpx.preflight_text_budget(
                FIXTURE,
                {},
                [],
                [
                    edit_hwpx.ParagraphTarget(
                        18,
                        "미국노동부는현장점검과공개보고를통해이행상황을지속관리한다",
                    )
                ],
                12,
            )

        self.assertIn("띄어쓰기 없는 한글 문자열", str(cm.exception))

    def test_paragraph_rewrite_rejects_container_paragraph(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            edit_hwpx.preflight_text_budget(
                FIXTURE,
                {},
                [],
                [edit_hwpx.ParagraphTarget(0, "미국노동부 보도자료")],
                24,
            )

        self.assertIn("문단을 직접 편집할 수 없습니다", str(cm.exception))

    def test_structure_profile_detects_deleted_hp_t_controls(self) -> None:
        self.assertTrue(FIXTURE.is_file(), f"missing fixture: {FIXTURE}")
        profile = page_guard.collect_structure_profile(FIXTURE)

        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.hwpx"
            with ZipFile(FIXTURE, "r") as src, ZipFile(broken, "w") as dst:
                names = src.namelist()
                section = etree.fromstring(src.read("Contents/section0.xml"))
                target = section.xpath(".//hp:t[*]", namespaces=NS)[0]
                for child in list(target):
                    target.remove(child)
                section_bytes = etree.tostring(
                    section,
                    xml_declaration=True,
                    encoding="UTF-8",
                    standalone=True,
                )

                for name in names:
                    data = (
                        section_bytes
                        if name == "Contents/section0.xml"
                        else src.read(name)
                    )
                    dst.writestr(
                        name,
                        data,
                        compress_type=src.getinfo(name).compress_type,
                    )

            broken_profile = page_guard.collect_structure_profile(broken)
            errors = page_guard.compare_structure_profile(profile, broken_profile)

            self.assertTrue(
                any("Contents/section0.xml XML 구조 fingerprint 불일치" in e for e in errors),
                errors,
            )

    def test_content_guard_detects_residual_source_terms_and_placeholders(self) -> None:
        findings = content_guard.scan_text(
            "미국노동부 보도자료\n고용노동부 잔여 문장\n법인 ○○○○\n",
            forbid=["고용노동부"],
            forbid_regex=[],
            require=["미국노동부"],
            require_regex=[],
            placeholder_regex=content_guard.DEFAULT_PLACEHOLDER_PATTERNS,
        )

        kinds = {finding.kind for finding in findings}
        self.assertIn("forbidden", kinds)
        self.assertIn("placeholder", kinds)

    def test_content_guard_detects_missing_required_term(self) -> None:
        findings = content_guard.scan_text(
            "기존 문서만 남음\n",
            forbid=[],
            forbid_regex=[],
            require=["미국노동부"],
            require_regex=[],
            placeholder_regex=[],
        )

        self.assertEqual("missing-required", findings[0].kind)

    def test_content_guard_computes_unchanged_long_line_ratio(self) -> None:
        ratio, unchanged, total = content_guard.unchanged_line_ratio(
            "원본에서 오래 남으면 안 되는 긴 문장입니다\n다른 긴 문장입니다\n",
            "원본에서 오래 남으면 안 되는 긴 문장입니다\n새로 바뀐 충분히 긴 문장입니다\n",
        )

        self.assertEqual(1, unchanged)
        self.assertEqual(2, total)
        self.assertAlmostEqual(0.5, ratio)


if __name__ == "__main__":
    unittest.main()
