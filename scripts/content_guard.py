#!/usr/bin/env python3
"""Check whether an edited HWPX is semantically complete enough to deliver.

Structural validation can pass while old organization names, placeholders, or
old contact blocks remain in the document. This guard scans extracted text for
that class of failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from text_extract import extract_plain


DEFAULT_PLACEHOLDER_PATTERNS = [
    r"○○+",
    r"\{\{[^}]+\}\}",
    r"<<[^>]+>>",
    r"\bTODO\b",
    r"\bTBD\b",
    r"\.\s*\.\s*\.",
]


@dataclass
class Finding:
    kind: str
    pattern: str
    line: int
    text: str


def _normalized_line(line: str) -> str:
    return re.sub(r"\s+", "", line)


def _line_preview(line: str, limit: int = 160) -> str:
    line = " ".join(line.split())
    if len(line) <= limit:
        return line
    return line[: limit - 3] + "..."


def _scan_literal(lines: list[str], needle: str, kind: str) -> list[Finding]:
    findings: list[Finding] = []
    for idx, line in enumerate(lines, start=1):
        if needle in line:
            findings.append(Finding(kind, needle, idx, _line_preview(line)))
    return findings


def _scan_regex(lines: list[str], pattern: str, kind: str) -> list[Finding]:
    regex = re.compile(pattern)
    findings: list[Finding] = []
    for idx, line in enumerate(lines, start=1):
        if regex.search(line):
            findings.append(Finding(kind, pattern, idx, _line_preview(line)))
    return findings


def scan_text(
    text: str,
    *,
    forbid: list[str],
    forbid_regex: list[str],
    require: list[str],
    require_regex: list[str],
    placeholder_regex: list[str],
) -> list[Finding]:
    lines = text.splitlines()
    findings: list[Finding] = []

    for needle in forbid:
        findings.extend(_scan_literal(lines, needle, "forbidden"))

    for pattern in forbid_regex:
        findings.extend(_scan_regex(lines, pattern, "forbidden-regex"))

    for pattern in placeholder_regex:
        findings.extend(_scan_regex(lines, pattern, "placeholder"))

    for needle in require:
        if needle not in text:
            findings.append(Finding("missing-required", needle, 0, ""))

    for pattern in require_regex:
        if not re.search(pattern, text, flags=re.MULTILINE):
            findings.append(Finding("missing-required-regex", pattern, 0, ""))

    return findings


def unchanged_line_ratio(reference_text: str, output_text: str) -> tuple[float, int, int]:
    reference_lines = {
        _normalized_line(line)
        for line in reference_text.splitlines()
        if len(_normalized_line(line)) >= 12
    }
    output_lines = [
        _normalized_line(line)
        for line in output_text.splitlines()
        if len(_normalized_line(line)) >= 12
    ]
    if not output_lines:
        return 0.0, 0, 0
    unchanged = sum(1 for line in output_lines if line in reference_lines)
    return unchanged / len(output_lines), unchanged, len(output_lines)


def load_rules(path: Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("--rules JSON은 객체여야 합니다.")
    rules: dict[str, list[str]] = {}
    for key in [
        "forbid",
        "forbid_regex",
        "require",
        "require_regex",
        "placeholder_regex",
    ]:
        value = data.get(key, [])
        if isinstance(value, str):
            rules[key] = [value]
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            rules[key] = value
        elif value:
            raise SystemExit(f"--rules의 {key} 값은 문자열 또는 문자열 배열이어야 합니다.")
    return rules


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HWPX 결과물의 원문 잔재, placeholder, 필수 키워드 누락 검사"
    )
    parser.add_argument("input", type=Path, help="검사할 .hwpx")
    parser.add_argument("--reference", type=Path, help="원본 .hwpx. 전체 재작성 시 잔존 문장 비율 검사에 사용")
    parser.add_argument(
        "--max-unchanged-ratio",
        type=float,
        help="원본과 동일한 긴 줄의 최대 허용 비율. 전체 재작성 작업에서만 사용",
    )
    parser.add_argument("--rules", type=Path, help="검사 규칙 JSON")
    parser.add_argument("--forbid", action="append", default=[], help="남으면 실패할 문자열")
    parser.add_argument("--forbid-regex", action="append", default=[], help="남으면 실패할 정규식")
    parser.add_argument("--require", action="append", default=[], help="반드시 포함되어야 할 문자열")
    parser.add_argument("--require-regex", action="append", default=[], help="반드시 매칭되어야 할 정규식")
    parser.add_argument(
        "--placeholder-regex",
        action="append",
        default=[],
        help="placeholder로 볼 정규식. 기본 placeholder 패턴에 추가됨",
    )
    parser.add_argument(
        "--no-default-placeholders",
        action="store_true",
        help="기본 placeholder 패턴 검사를 끔",
    )
    parser.add_argument("--json", action="store_true", help="JSON으로 결과 출력")
    args = parser.parse_args()

    rules = load_rules(args.rules)
    forbid = [*rules.get("forbid", []), *args.forbid]
    forbid_regex = [*rules.get("forbid_regex", []), *args.forbid_regex]
    require = [*rules.get("require", []), *args.require]
    require_regex = [*rules.get("require_regex", []), *args.require_regex]
    placeholder_regex = [
        *(DEFAULT_PLACEHOLDER_PATTERNS if not args.no_default_placeholders else []),
        *rules.get("placeholder_regex", []),
        *args.placeholder_regex,
    ]

    if not args.input.is_file():
        print(f"Error: input not found: {args.input}", file=sys.stderr)
        return 2

    text = extract_plain(args.input, include_tables=True)
    findings = scan_text(
        text,
        forbid=forbid,
        forbid_regex=forbid_regex,
        require=require,
        require_regex=require_regex,
        placeholder_regex=placeholder_regex,
    )

    unchanged_stats: tuple[float, int, int] | None = None
    if args.max_unchanged_ratio is not None:
        if args.reference is None:
            raise SystemExit("--max-unchanged-ratio를 쓰려면 --reference가 필요합니다.")
        reference_text = extract_plain(args.reference, include_tables=True)
        ratio, unchanged, total = unchanged_line_ratio(reference_text, text)
        unchanged_stats = (ratio, unchanged, total)
        if ratio > args.max_unchanged_ratio:
            findings.append(
                Finding(
                    "too-much-unchanged-text",
                    f"{ratio:.3f}>{args.max_unchanged_ratio:.3f}",
                    0,
                    f"unchanged_long_lines={unchanged}/{total}",
                )
            )

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not findings,
                    "input": str(args.input),
                    "unchanged_line_ratio": (
                        {
                            "ratio": unchanged_stats[0],
                            "unchanged": unchanged_stats[1],
                            "total": unchanged_stats[2],
                        }
                        if unchanged_stats
                        else None
                    ),
                    "findings": [asdict(finding) for finding in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif findings:
        print(f"FAIL: content-guard {args.input}", file=sys.stderr)
        for finding in findings:
            location = f"line {finding.line}" if finding.line else "document"
            print(
                f"  - {finding.kind} {finding.pattern!r} at {location}: {finding.text}",
                file=sys.stderr,
            )
    else:
        print(f"PASS: content-guard {args.input}")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
