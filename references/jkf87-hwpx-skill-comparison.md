# jkf87/hwpx-skill 비교 메모

비교 대상: `https://github.com/jkf87/hwpx-skill` main, `389dd7b`

## 상대 레포 강점

- 문서 유형별 생성기가 많다: 기안문, 보도자료, 계획서, 문제지/답안지.
- 참고 문서가 세분화되어 있다: 공문서 작성법, 템플릿 스타일, 문제 해결, XML 내부 구조.
- 최종 제출 전 도구가 있다: 네임스페이스 보정, 줄 배치 캐시 제거, 레이아웃 위험 경고, 공문 표기 lint.
- HWP 바이너리에서 HWPX로 변환하는 진입점이 있다.

## 현재 레포 강점

- 기존 HWPX 양식 보존 편집이 더 보수적이다.
- 일반 ZIP 재압축 대신 원본 로컬 헤더와 압축 데이터를 보존하고 변경 엔트리만 교체한다.
- `hwpx_slots.py`, `page_guard.py`, `content_guard.py`로 슬롯 편집, 글자 예산, 구조 fingerprint, 원문 잔재 검사를 함께 수행한다.
- `hp:t` 내부 컨트롤과 `hp:linesegarray` 처리 규칙이 회귀 테스트로 고정되어 있다.

## 반영한 보강

- `scripts/finalize_hwpx.py`: `hp:linesegarray` 제거, 표 셀 밀도, 제목 다음 본문 들여쓰기 위험 경고.
- `scripts/fix_namespaces.py`: `ns0`류 프리픽스를 `hh/hc/hp/hs`로 정리하고 `header.xml` itemCnt 보정.
- `scripts/gonmun_lint.py`: 날짜, 시간, 금액, 붙임, 외국어 병기 같은 공문서 표기 검사.
- `scripts/validate.py --layout`: 구조 검증 뒤 레이아웃 위험 경고를 함께 출력.

## 의도적으로 보류한 항목

- `assets/*.hwpx`: 외부 샘플 양식은 라이선스와 출처가 명확하지 않아 가져오지 않았다.
- 대형 생성기(`bodojaryo.py`, `gyehoek.py`, `build_problem_answer_sheet.py`): 레퍼런스 asset 의존성이 커서 현재 레포의 보존 편집 중심 구조와 바로 합치지 않았다.
- `convert_hwp.py`: 외부 변환 레포 자동 클론/설치 흐름이 있어 별도 설계가 필요하다.
- `fill_hwpx.py`: 기능은 유용하지만 현재 `hwpx_slots.py`/`edit_hwpx.py`와 겹친다. 라벨-값 필드 자동 탐지는 추후 별도 모듈로 좁게 흡수하는 편이 낫다.
