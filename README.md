# hwpxskill

한컴오피스 HWPX 문서를 AI 코딩 에이전트에서 다룰 수 있게 해주는 스킬입니다.

서식 보존이 중요한 편집에서는 OWPML XML을 직접 다루는 방식을 택했습니다. 덕분에 기존 문서의 서식이나 구조를 거의 그대로 유지하면서 내용만 갈아끼울 수 있습니다.

## 뭘 할 수 있나

원본 HWPX 파일을 넣으면 스타일, 표 구조, 셀 병합, 여백까지 분석해서 구조를 보존한 채 내용만 바꿔줍니다. 원본이 없으면 공문, 보고서 같은 내장 템플릿으로 새 문서를 만들 수도 있고요. 다 만들고 나면 `page_guard.py`가 원본 대비 페이지 수가 달라졌는지 자동으로 잡아냅니다.

OWPML 표준 XML을 직접 다루기 때문에 charPr, paraPr 단위로 서식을 제어할 수 있습니다. Claude Code, Cursor, Codex CLI에서 모두 동작합니다.

## 주요 업데이트

- 기존 HWPX 양식을 그대로 쓰는 `edit_hwpx.py` 편집 경로를 강화했습니다. 일반 ZIP 재압축 대신 원본 로컬 헤더와 압축 데이터를 보존하고, 변경된 `Contents/section0.xml`만 교체합니다.
- `hwpx_slots.py`를 추가해 편집 가능한 문단/표 셀 슬롯을 먼저 추출하고 `--slot-json`으로 채우는 흐름을 지원합니다. 표, 그림, 텍스트상자 컨테이너 문단은 직접 수정하지 않습니다.
- 텍스트 수정 문단의 `hp:linesegarray` 줄 배치 캐시는 제거해 한컴의 손상/변조 경고 가능성을 줄입니다.
- `finalize_hwpx.py`를 추가해 줄 배치 캐시 제거, 표 셀 밀도, 제목 다음 본문 들여쓰기 같은 레이아웃 위험을 점검합니다. 이 도구도 원본 ZIP 메타데이터를 최대한 보존하며 필요한 XML 엔트리만 교체합니다.
- `fix_namespaces.py`를 추가해 `ns0` 같은 자동 네임스페이스 프리픽스를 `hh/hc/hp/hs` 표준 프리픽스로 정리하고 `header.xml`의 `itemCnt`를 보정합니다.
- `gonmun_lint.py`를 추가해 공문서 날짜, 시간, 금액, 붙임, 외국어 병기 표기 오류를 빠르게 검사합니다.
- 문단 전체 재작성 시 원본의 볼드/색상 강조가 새 문장에 섞이지 않도록 `header.xml`의 `charPr`를 분석합니다. 첫 run이나 10pt에 가까운 run을 무조건 고르지 않고, 해당 문단에서 가장 많이 쓰인 본문 글자 높이에 가까운 비강조 run을 선택합니다.
- `content_guard.py`를 추가해 구조 검증만으로 잡히지 않는 원문 잔재, placeholder, 필수 키워드 누락, 전면 재작성 시 원본 문장 과다 잔존을 검사합니다.
- `page_guard.py`는 문단/셀별 글자 예산과 XML 구조 fingerprint를 함께 비교합니다. `hp:t` 내부 컨트롤은 보존하고, `hp:linesegarray` 제거는 허용합니다.

## 참고한 프로젝트

이 저장소는 [`jkf87/hwpx-skill`](https://github.com/jkf87/hwpx-skill)의 최종화/검수 흐름과 문서화 방식을 참고해 보완했습니다. 특히 네임스페이스 보정, 줄 배치 캐시 제거, 레이아웃 위험 경고, 공문서 표기 검수 아이디어를 참고했고, 구현은 이 저장소의 원본 ZIP 메타데이터 보존 원칙에 맞춰 재구성했습니다. 자세한 비교 메모는 [`references/jkf87-hwpx-skill-comparison.md`](references/jkf87-hwpx-skill-comparison.md)에 정리했습니다.

## 설치

Agent Skills 표준을 따르고 있어서, 어떤 도구든 스킬 디렉토리에 넣기만 하면 됩니다.

```bash
git clone https://github.com/Canine89/hwpxskill.git
```

### Claude Code

```bash
# 이 프로젝트에서만 쓸 때
cp -r hwpxskill .claude/skills/hwpxskill

# 어디서든 쓸 때
cp -r hwpxskill ~/.claude/skills/hwpxskill
```

넣어두면 HWPX 관련 작업할 때 알아서 불러옵니다.

### Cursor

```bash
# 이 프로젝트에서만 쓸 때
cp -r hwpxskill .cursor/skills/hwpxskill

# 어디서든 쓸 때
cp -r hwpxskill ~/.cursor/skills/hwpxskill
```

`.hwpx` 파일을 열 때 자동으로 활성화되게 하려면 rule 파일을 하나 추가하면 됩니다.

```yaml
# .cursor/rules/hwpx.mdc
---
description: "HWPX 문서 작업 시 hwpxskill 사용"
globs: ["*.hwpx"]
---
```

### Codex CLI

```bash
# 이 프로젝트에서만 쓸 때
cp -r hwpxskill .agents/skills/hwpxskill

# 어디서든 쓸 때
cp -r hwpxskill ~/.agents/skills/hwpxskill
```

Codex 세션 안에서 `$skill-installer`로 설치할 수도 있습니다.

## 빠른 시작

### 1. 새 문서 만들기

템플릿 골라서 바로 생성. 원본 파일 없을 때 씁니다.

```bash
python3 scripts/build_hwpx.py --template gonmun --output result.hwpx
```

### 2. 기존 문서 편집

양식을 유지하면서 텍스트나 표 셀만 바꿀 때는 `edit_hwpx.py`를 씁니다. 원본 ZIP 패키지, `header.xml`, `content.hpf`, `BinData`, 표 크기, 셀 병합, 서식 참조를 그대로 두고 `Contents/section0.xml`의 텍스트 노드만 최소 수정합니다.

```bash
python3 scripts/edit_hwpx.py reference.hwpx \
  --output filled.hwpx \
  --replace "{{기관명}}=고용노동부" \
  --cell "0,2,1=테스트 법인"

python3 scripts/hwpx_slots.py reference.hwpx \
  --output reference.slots.json

python3 scripts/edit_hwpx.py reference.hwpx \
  --output rewritten.hwpx \
  --slot-json values.json

python3 scripts/validate.py filled.hwpx
python3 scripts/fix_namespaces.py filled.hwpx
python3 scripts/finalize_hwpx.py filled.hwpx --strip-linesegarray --layout
python3 scripts/validate.py filled.hwpx --layout
python3 scripts/page_guard.py \
  --reference reference.hwpx \
  --output filled.hwpx \
  --no-strict-paragraph-budget \
  --skip-text-drift \
  --allow-empty-fill

python3 scripts/content_guard.py filled.hwpx \
  --forbid "고용노동부" \
  --forbid "044-" \
  --require "미국노동부"

python3 scripts/content_guard.py rewritten.hwpx \
  --reference reference.hwpx \
  --rules content.rules.json \
  --max-unchanged-ratio 0.35
```

좌표는 0부터 시작합니다. `--cell "row,col=값"`은 첫 번째 표를 대상으로 하고, `--cell "table,row,col=값"`은 특정 표를 대상으로 합니다.

본문을 실무적으로 고쳐 쓰는 경우에는 먼저 `hwpx_slots.py`로 편집 가능한 슬롯을 뽑고, `--slot-json`으로 값을 넣습니다. 이 경로는 표/그림/텍스트상자 컨테이너 문단을 직접 건드리지 않아 새 텍스트가 기존 텍스트와 겹치는 문제를 줄입니다. 문단 전체를 바꿀 때는 `header.xml`의 `charPr`를 보고 볼드/색상 강조가 적고, 해당 문단에서 가장 많이 쓰인 본문 글자 높이에 가까운 run을 골라 새 문장을 넣습니다. 본문은 원본 문단 예산 이하로 짧게 다시 써야 하며, 글자 수를 정확히 맞추려고 띄어쓰기를 제거하거나 문장을 중간에서 자르면 안 됩니다.

글자 수까지 더 엄격히 맞추려면 먼저 원본 양식의 예산 프로파일을 만듭니다. 이 파일에는 문단/셀별 기존 글자 수와 셀 폭 기반 입력 가능 글자 수가 저장됩니다.

```bash
python3 scripts/page_guard.py \
  --reference reference.hwpx \
  --write-budget reference.budget.json \
  --write-structure reference.structure.json

python3 scripts/page_guard.py \
  --reference reference.hwpx \
  --output filled.hwpx \
  --budget-profile reference.budget.json \
  --structure-profile reference.structure.json \
  --no-strict-paragraph-budget \
  --skip-text-drift \
  --allow-empty-fill
```

이 검사는 표 구조가 같더라도 특정 셀의 글자 수가 원본 셀 예산을 넘으면 실패합니다. 본문 문단은 `--no-strict-paragraph-budget --skip-text-drift`를 사용해 정확 일치가 아니라 예산 이하와 구조 보존 여부를 봅니다. `--write-structure`는 패키지 파일 목록/순서, 압축 방식, 날짜, 생성 시스템, 파일 속성, 바이너리 해시, XML 태그/속성/순서를 저장하고 결과 문서와 대조합니다. `hp:t`의 텍스트 값은 내용 입력 대상으로 제외하지만, `hp:t` 안의 `hp:fwSpace`, `hp:lineBreak` 같은 자식 컨트롤 태그와 순서는 반드시 보존해야 합니다. 반대로 문단 하위 `hp:linesegarray`는 한컴이 저장한 줄 배치 캐시이므로 텍스트를 바꾼 문단에서는 제거합니다.

구조 검사를 통과해도 원문 기관명, 담당자, 전화번호, `○○` placeholder가 남아 있으면 실무 결과물이 아닙니다. 그런 경우 `content_guard.py`로 금지어와 필수어를 검사합니다. 예를 들어 보도자료를 미국노동부 문서로 바꿨다면 `고용노동부`, 기존 담당자명, `044-` 연락처를 금지하고 `미국노동부`를 필수어로 둡니다.

문서 전체 관점 전환이나 전면 재작성이라면 `--reference`와 `--max-unchanged-ratio`도 같이 씁니다. 원본의 긴 문장이 많이 남은 결과물은 구조가 정상이더라도 실패로 봅니다.

공문서 표기 자체를 점검해야 하면 `gonmun_lint.py`를 추가로 실행합니다.

```bash
python3 scripts/gonmun_lint.py --hwpx filled.hwpx --format text
```

`edit_hwpx.py`는 새 ZIP을 일반 재압축하지 않고 원본 ZIP의 로컬 헤더와 압축 데이터를 복사합니다. 변경 대상인 `Contents/section0.xml`만 교체하고, 나머지 엔트리는 CRC, compressed size, flag bits, 날짜, 속성까지 원본과 같게 유지합니다. `section0.xml`도 원본 XML 선언과 줄바꿈 관습을 최대한 유지합니다.

직접 XML을 확인해야 할 때만 HWPX를 풀고 다시 묶습니다. 기본 `unpack.py`는 XML을 원본 바이트 그대로 추출합니다. `hp:t`처럼 텍스트와 `hp:fwSpace`, `hp:lineBreak` 같은 자식 컨트롤이 섞인 mixed content 안에 들여쓰기 공백이 들어가면 한컴에서 실제 텍스트처럼 렌더링될 수 있기 때문입니다.

```bash
python3 scripts/office/unpack.py document.hwpx ./unpacked/
# XML 수정
python3 scripts/office/pack.py ./unpacked/ edited.hwpx
```

XML을 사람이 읽기 좋게 확인해야 할 때만 `--pretty`를 사용합니다. 이 결과물은 검사 전용이며 다시 `pack.py` 입력으로 쓰지 않습니다.

```bash
python3 scripts/office/unpack.py document.hwpx ./inspect/ --pretty
```

### 3. 텍스트 추출

문서에서 텍스트만 뽑습니다. 표도 포함되고, 마크다운으로도 뽑을 수 있습니다.

```bash
python3 scripts/text_extract.py document.hwpx --format markdown
```

### 4. 문서 검증

ZIP 구조, XML 유효성, mimetype 위치 같은 걸 점검합니다.

```bash
python3 scripts/validate.py result.hwpx
```

### 5. 레퍼런스 기반 복원

이게 핵심입니다. 원본 문서를 분석해서 스타일과 구조를 통째로 가져온 뒤, 내용만 갈아끼웁니다. HWPX 파일을 첨부하면 이 흐름이 자동으로 돌아갑니다.

```bash
# 분석
python3 scripts/analyze_template.py reference.hwpx \
  --extract-header /tmp/ref_header.xml \
  --extract-section /tmp/ref_section.xml

# 빌드
python3 scripts/build_hwpx.py \
  --header /tmp/ref_header.xml \
  --section /tmp/new_section0.xml \
  --output result.hwpx

# 검증 + 페이지 가드
python3 scripts/validate.py result.hwpx
python3 scripts/page_guard.py --reference reference.hwpx --output result.hwpx
```

### 6. 회귀 테스트

`hp:t` 내부 컨트롤 삭제처럼 한컴에서 손상으로 이어지는 변경을 막는 테스트입니다.

```bash
python3 -m unittest tests/test_hwpx_guards.py
```

## 템플릿

| 템플릿 | 용도 | 특징 |
|--------|------|------|
| base | 기본 골격 | 최소 스타일, 빈 문서 시작점 |
| gonmun | 공문서 | 기관명, 수신처, 시행일자, 연락처 |
| report | 보고서 | 섹션 헤더, 들여쓰기, 체크박스 |
| minutes | 회의록 | 섹션 라벨, 테두리 구분 |
| proposal | 제안서 | 색상 헤더, 번호 뱃지 |

## 요구사항

- Python 3.6 이상
- lxml (`pip install lxml`)
- 가상환경 권장

## 스크립트

| 스크립트 | 하는 일 |
|----------|---------|
| `build_hwpx.py` | 템플릿 + XML 조합해서 HWPX 생성 |
| `edit_hwpx.py` | 원본 양식을 보존하며 텍스트/표 셀만 수정 |
| `analyze_template.py` | 레퍼런스 HWPX 분석 |
| `office/unpack.py` | HWPX를 디렉토리로 풀기, 기본값은 XML 바이트 보존 |
| `office/pack.py` | 디렉토리를 HWPX로 묶기 |
| `validate.py` | HWPX 구조, manifest, 이미지 참조 검증 |
| `fix_namespaces.py` | 표준 네임스페이스 프리픽스와 header itemCnt 보정 |
| `finalize_hwpx.py` | 줄 배치 캐시 제거, 레이아웃 위험 경고, Windows Hancom 열림 검사 |
| `page_guard.py` | 원본 대비 페이지 드리프트 위험 감지, 문단/셀별 글자 예산 검증 |
| `content_guard.py` | 원문 잔재, placeholder, 필수 키워드 누락 검사 |
| `gonmun_lint.py` | 공문서 날짜/시간/금액/붙임 표기 검수 |
| `text_extract.py` | XML 직접 파싱 기반 텍스트 추출 |

## 자세한 사용법

스타일 ID 체계, XML 구조 규칙, 템플릿별 charPr/paraPr 매핑 같은 건 [SKILL.md](./SKILL.md)에 다 정리되어 있습니다.
