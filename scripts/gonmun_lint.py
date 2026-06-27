#!/usr/bin/env python3
"""공문서 작성법 자동 검수기.

날짜, 시간, 금액, 붙임, 외국어 병기처럼 오탐이 비교적 적은 표기 규칙만
정규식으로 점검한다. .hwpx 입력은 XML에서 텍스트를 추출해 검사한다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import zipfile
from pathlib import Path

_RULES: list[tuple[str, str, re.Pattern[str], str, str | None]] = []


def rule(code: str, severity: str, pattern: str, message: str, suggest: str | None = None, flags: int = 0) -> None:
    _RULES.append((code, severity, re.compile(pattern, flags), message, suggest))


rule("DATE_NO_SPACE", "error", r"\b\d{4}\.\d{1,2}\.\d{1,2}\.?", "날짜 온점 뒤에 한 칸씩 띄워야 합니다.", "예) 2025. 1. 6.")
rule("DATE_ZERO_PAD", "error", r"\b\d{4}\.\s*0\d\.|\.\s*0\d\.", "월/일 앞의 0은 표기하지 않습니다.", "예) 2025. 1. 6.")
rule("DATE_2DIGIT_YR", "error", r"(?<!\d)'\d{2}\.\s*\d", "연도는 네 자리로 표기합니다.", "예) 2025. 1. 6.")
rule("DATE_NO_END_DOT", "warning", r"\b\d{4}\.\s\d{1,2}\.\s\d{1,2}(?!\s*[.\d(])", "날짜의 일 다음에 마침표를 찍어야 합니다.", "예) 2025. 1. 6.")
rule("TIME_AMPM", "error", r"(오전|오후|아침|밤|낮)\s*\d{1,2}\s*시", "24시각제 숫자로 표기합니다.", "예) 09:00, 15:30")
rule("TIME_24H", "warning", r"(?<!\d)24\s*시(?!각)", "'24시'보다 익일 00:00 또는 24:00 지양 표현을 검토합니다.", "예) 18:00")
rule("TIME_COLON_SP", "error", r"\b\d{1,2}\s+:\s*\d{2}\b|\b\d{1,2}:\s+\d{2}\b", "시와 분 사이 쌍점은 양쪽을 붙여 씁니다.", "예) 13:20")
rule("MONEY_CHEONWON", "error", r"\d+\s*천\s*원", "금액은 '천원'으로 줄이지 않고 아라비아 숫자로 씁니다.", "예) 345,000원")
rule("MONEY_GEUM_SP", "warning", r"금\s+\d", "'금'과 숫자 사이는 붙여 쓰는 것이 원칙입니다.", "예) 금113,560원")
rule("BUNIM_COLON", "error", r"붙\s*임\s*:", "'붙임' 다음에 쌍점을 붙이지 않습니다.", "예) 붙임  계획서 1부.")
rule("KKAJI_DUP", "error", r"[∼~][^\n]{0,20}?까지", "물결표와 '까지'를 함께 쓰지 않습니다.", "예) 2. 20.∼2. 24.")
rule("FOREIGN_FIRST", "warning", r"\b[A-Z]{2,5}\s*\([가-힣]", "한글을 먼저 쓰고 괄호 안에 외국어를 병기합니다.", "예) 업무 협약(MOU)")
rule("COLON_SPACE", "warning", r"\S\s+:\S|\S:[^\s\d]", "쌍점은 앞말에 붙이고 뒤는 한 칸 띄웁니다.", "예) 원장: 김갑동")


def extract_text(hwpx_path: str | Path) -> str:
    with zipfile.ZipFile(hwpx_path) as zf:
        names = [name for name in zf.namelist() if re.match(r"Contents/section\d+\.xml", name)]
        out: list[str] = []
        for name in sorted(names):
            xml = zf.read(name).decode("utf-8", "replace")
            for raw in re.findall(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>", xml, re.DOTALL):
                out.append(html.unescape(re.sub(r"<[^>]+>", "", raw)))
        return "\n".join(out)


def lint_text(text: str) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for code, severity, rx, message, suggest in _RULES:
            for match in rx.finditer(line):
                findings.append({
                    "line": line_no,
                    "match": match.group(0).strip(),
                    "rule": code,
                    "severity": severity,
                    "message": message,
                    "suggest": suggest,
                })

    severity_counts: dict[str, int] = {}
    for finding in findings:
        severity = str(finding["severity"])
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    return {
        "findings": findings,
        "summary": {
            "total": len(findings),
            **severity_counts,
            "ok": severity_counts.get("error", 0) == 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="공문서 작성법 자동 검수")
    parser.add_argument("--hwpx", help=".hwpx 파일에서 본문 추출 후 검수")
    parser.add_argument("--file", help="텍스트 파일 검수")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    args = parser.parse_args()

    if args.hwpx:
        text = extract_text(args.hwpx)
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    result = lint_text(text)
    if args.format == "text":
        summary = result["summary"]
        assert isinstance(summary, dict)
        print(
            f"검수 결과: 위반 {summary['total']}건 "
            f"(error {summary.get('error', 0)}, warning {summary.get('warning', 0)})"
        )
        for finding in result["findings"]:
            assert isinstance(finding, dict)
            suggest = f" -> {finding['suggest']}" if finding.get("suggest") else ""
            print(
                f"  L{finding['line']} [{finding['severity']}] {finding['rule']}: "
                f"\"{finding['match']}\" - {finding['message']}{suggest}"
            )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    summary = result["summary"]
    assert isinstance(summary, dict)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
