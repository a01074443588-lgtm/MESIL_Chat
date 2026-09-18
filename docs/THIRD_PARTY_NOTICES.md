# 최종 공개 소스의 출처·라이선스 고지

2026-09-18 최종 누적본 갱신에서는 기존 공개 커밋 `f846f261329119dbf411cac9d0d338a4cdeb0a22` 대비 Backend·Frontend 의존성 선언과 잠금 파일을 그대로 유지했습니다. 추가 외부 라이브러리·폰트·모델 가중치는 없으며 기존 고지와 라이선스를 유지합니다. README 대표 UI 캡처의 출처·공개 범위는 [자산 고지](PUBLIC_ASSETS_AND_LICENSES.md)에 추가했습니다. 기존 라이선스 대조 범위를 과거 이력 전체나 서버 모델 가중치까지 확대했다는 의미는 아닙니다.

## 이번 대조 범위

기준은 `submission-baseline-20260918`, 커밋 `954c47e037d371aa4fbafecc098ea8e9da3b36f5`의 **605개 공개 파일**입니다. DEV 전체나 과거 브랜치를 기준으로 삼지 않았습니다. 이 문서와 목록·고지 추가는 문서 보완이며 제품 코드·의존성·서비스에는 변경이 없습니다. 공개 반영 이력은 이 문서를 포함한 후속 커밋에서 확인할 수 있습니다.

- Frontend `package-lock.json`: 설치 위치 기준 641개 항목, 이름·버전 기준 632개. 선택적 플랫폼 패키지와 개발용 간접 의존성도 포함합니다. 각 버전의 npm 공식 배포 메타데이터와 잠금 파일의 라이선스가 모두 일치했습니다.
- Backend `uv.lock`: 자체 프로젝트를 제외한 외부 패키지 81개. STT의 `requirements.txt`·`requirements-test.txt`까지 합쳐 Python 선언 88개, 중복 제거한 이름·버전 86개를 PyPI의 해당 버전 정보로 확인했습니다.
- Backend Dockerfile의 설치 도구 `uv==0.12.5`도 별도로 확인했습니다. 위 두 생태계와 이 도구의 **719개 고유 버전 메타데이터**를 확인했습니다. 메타데이터에서 조건이 불충분한 항목은 아래 버전 고정 원문으로 보완했습니다.
- 전체 목록의 위치·버전·라이선스·공식 출처·확인 방식은 [THIRD_PARTY_DEPENDENCIES.csv](THIRD_PARTY_DEPENDENCIES.csv), 파일별 범위와 결과는 [THIRD_PARTY_REVIEW.json](THIRD_PARTY_REVIEW.json)에 있습니다.

## 포함 파일과 별도 설치물을 구분합니다

최초 대조 패키지에는 제품·시험 소스, 의존성 선언/잠금 파일, 문서, 합성 PDF, 승인 브랜드 3개와 합성 차임 1개가 있었습니다. 이후 사용자가 승인한 합성 데모 영상 1개와 README 대표 UI 캡처 1개를 추가했으며 [별도 자산 고지](PUBLIC_ASSETS_AND_LICENSES.md)에 구분했습니다. `node_modules`, Python 설치 패키지, 빌드 번들, Docker 이미지, 모델 가중치, 독립 폰트 파일은 포함하지 않습니다. 코드의 저작권·출처 헤더 대조에서 별도로 복사해 넣은 제3자 라이브러리는 식별되지 않았습니다. 이는 원저작권을 법적으로 보증하거나 알려지지 않은 코드 유래까지 증명한다는 뜻은 아닙니다.

설치자가 내려받는 의존성의 라이선스는 MESIL Chat의 라이선스로 바뀌지 않습니다. 이 목록은 해당 패키지의 LICENSE/COPYING/NOTICE 전문을 대신하지 않습니다. 이후 번들·이미지·바이너리를 재배포할 때는 실제 포함물의 고지와 소스 제공 조건을 함께 충족해야 합니다. 현재 실행 서비스의 배포 고지를 새로 감사한 결과는 아닙니다.

## 코드·의존성 고지

| 범위 / 실제 선언 버전 | 출처·조건 | 필요한 고지와 구분 |
|---|---|---|
| MESIL Chat 자체 코드 | [Apache-2.0](../LICENSE), [프로젝트 고지](../NOTICE) | 자체 코드에 적용합니다. 제3자 패키지·브랜드·폰트에 일괄 적용하지 않습니다. |
| README와 자체 문서 | [CC BY 4.0](../LICENSE-DOCS) | 문서의 저자·라이선스 링크와 변경 사실을 표시합니다. 인용한 제3자 원문은 원래 조건을 유지합니다. |
| React 19.2.6, Next.js 16.2.6, Vinext 0.0.50, Vite 8.0.13 등 | 버전별 공식 출처는 CSV; MIT 및 각 패키지 표기 | 재배포물에 원 저작권과 허가문을 보존합니다. |
| `pdfjs-dist` 6.2.108 | [해당 버전 LICENSE](https://github.com/mozilla/pdf.js/blob/v6.2.108/LICENSE), Apache-2.0 | PDF.js 패키지의 NOTICE와 내장 자원별 고지도 보존합니다. 이번 공개 파일에 PDF.js 바이너리·표준 폰트를 복사하지 않았습니다. |
| `pypdfium2` 4.30.0 | [버전 정보](https://pypi.org/pypi/pypdfium2/4.30.0/json), `(Apache-2.0 OR BSD-3-Clause) AND LicenseRef-PdfiumThirdParty` | PDFium과 묶음 구성요소의 별도 고지를 단순 BSD/MIT로 축약하지 않습니다. 설치 wheel/바이너리 재배포 시 그 안의 licenses를 함께 보존합니다. |
| `psycopg`·`psycopg-binary` 3.3.4 | [해당 버전 LICENSE](https://github.com/psycopg/psycopg/blob/3.3.4/LICENSE.txt), LGPL-3.0-only | 라이브러리·수정본·결합 바이너리의 배포 형태에 맞는 소스 제공과 이용자 교체/재링크 조건을 확인해야 합니다. 현재 ZIP에는 해당 라이브러리 파일이 없습니다. |
| `@img/sharp-libvips-*` 1.2.4, `@img/sharp-*` 0.34.5 중 복합 조건 패키지 | CSV의 정확한 SPDX 표기, [sharp 배포 안내](https://sharp.pixelplumbing.com/install/#licensing) | LGPL-3.0-or-later 및 Apache/MIT 복합 조건입니다. libvips와 포함 네이티브 라이브러리 고지·대응 소스 의무를 유지해야 합니다. |
| `lightningcss`, `@resvg/resvg-wasm`, `@vercel/og`, `satori`, `axe-core`; Python `certifi`, `py-vapid`, `pywebpush` | CSV의 잠금 버전, MPL-2.0; [공식 안내](https://www.mozilla.org/en-US/MPL/2.0/FAQ/) | 해당 파일과 수정분의 라이선스·소스 접근 안내를 보존합니다. 브라우저로 전달되는 코드와 서버에서만 실행되는 코드를 혼동하지 않습니다. |
| `caniuse-lite` 1.0.30001793 | [버전 정보](https://registry.npmjs.org/caniuse-lite/1.0.30001793), CC-BY-4.0 | 데이터를 재배포하면 저작자·출처·라이선스와 변경 사실 표시가 필요합니다. |
| `av` 18.0.0 / faster-whisper 1.2.1 | [PyAV LICENSE](https://github.com/PyAV-Org/PyAV/blob/v18.0.0/LICENSE.txt), BSD-3-Clause / [faster-whisper](https://pypi.org/pypi/faster-whisper/1.2.1/json), MIT | PyAV의 BSD가 함께 설치되는 FFmpeg·코덱의 이용조건을 대체하지 않습니다. 해당 빌드의 LGPL/GPL 구성은 실행 바이너리를 배포할 때 별도로 확인합니다. |
| 기타 MIT·ISC·BSD·Apache·PSF·CC0·BlueOak 등 | 전체 버전별 CSV | 원문에 정한 저작권·허가·면책 문구를 보존합니다. `AND`는 함께 충족, `OR`는 선택 조건으로 기록하며 임의로 MIT로 바꾸지 않습니다. |

Apache 배포 시 라이선스 사본, 해당 저작권·출처 및 upstream NOTICE를 보존하고 수정 사실을 표시해야 합니다. [Apache 공식 조건](https://www.apache.org/licenses/LICENSE-2.0)을 따릅니다. 위 표는 구체적인 배포물별 법률 판단을 대신하지 않습니다.

공식 메타데이터의 빈 값·축약 표현은 다음 원문으로 보완했습니다. `google-crc32c` 1.8.0은 [Apache-2.0](https://github.com/googleapis/python-crc32c/blob/v1.8.0/LICENSE), `colorama` 0.4.6은 [BSD-3-Clause](https://github.com/tartley/colorama/blob/0.4.6/LICENSE.txt), `pyasn1-modules` 0.4.2는 [BSD-2-Clause](https://github.com/pyasn1/pyasn1-modules/blob/v0.4.2/LICENSE.txt)입니다. `uvloop`는 MIT 표기만 보고 포함 부분의 Apache 조건을 삭제하지 않고, 해당 버전의 두 라이선스 고지를 함께 참조합니다.

## 이미지·음성·PDF·폰트

| 공개 파일 | 출처·조건과 확인 범위 |
|---|---|
| `frontend/public/brand/silvermedical-logo.jpg`, `frontend/public/icons/mesil-chat-192-v3.png`, `mesil-chat-512-v3.png` | [ASSET_LICENSES.md](../ASSET_LICENSES.md)의 대표자 권리 진술·공개 승인 및 세 SHA-256과 일치합니다. MESIL Chat 복제·빌드·실행·배포 화면 표시 범위이며 타 서비스 상표 재사용은 승인하지 않습니다. 계약서 독립 검토로 표현하지 않습니다. |
| `frontend/public/sounds/mesil-medic-voice-v2.wav` | 기존 공개용 제작 기록의 수학적 합성 차임입니다. 파일명과 달리 사람 음성·외부 녹음 샘플이 아닙니다. 기존 공개 파일의 지문을 유지하며, 별도 제3자 음원에 새로운 허가를 붙이지 않았습니다. |
| `backend/tests/fixtures/` 아래 PDF 69개 | 기존 합성 시험자료의 원본 파일을 유지합니다. 데이터와 도구 코드는 구분하며 실제 기관 기록으로 소개하지 않습니다. 글꼴 고지와 임베딩은 변경하지 않았습니다. |
| PDF에 포함된 맑은 고딕·맑은 고딕 Bold | 69개 PDF의 하위 폰트까지 확인한 121개 임베딩 참조 모두 `fsType=8`이었습니다. 이는 문서 임베딩 범위이며 독립 폰트 재배포 허가가 아닙니다. 폰트의 기존 저작권 정보를 보존합니다. [Microsoft 공식 문서 임베딩 안내](https://learn.microsoft.com/en-us/typography/fonts/font-faq#document-embedding)를 따릅니다. |
| Helvetica 이름 참조·웹 시스템 글꼴 | PDF 기본 글꼴 이름 참조와 CSS 시스템 글꼴 지정입니다. 별도 TTF/OTF/WOFF 파일을 공개 패키지에서 배포하지 않습니다. |
| 영상 | 공개 파일에 없습니다. 예정된 데모영상의 음원·화면·출연자 권리까지 확인됐다고 표시하지 않습니다. |

## 설치 계약 및 배포하지 않는 서버 모델

- Dockerfile·Compose에는 `python:3.12-slim`, `node:22-alpine`, `postgres:17-alpine`, `caddy:2.10-alpine`, `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`가 선언돼 있습니다. 이미지 자체는 공개 패키지에 없습니다. Python/Node/PostgreSQL/Caddy 및 이미지 내 OS 패키지 조건을 각각 유지하며 NVIDIA CUDA/cuDNN은 별도 SDK 이용조건입니다. 태그가 고정된 이미지 digest라는 뜻은 아닙니다.
- 근거: [Python 라이선스](https://docs.python.org/3/license.html), [Node 라이선스](https://github.com/nodejs/node/blob/v22.x/LICENSE), [PostgreSQL](https://www.postgresql.org/about/licence/), [Caddy v2.10.0](https://github.com/caddyserver/caddy/blob/v2.10.0/LICENSE), [CUDA 12.8.1 이용조건](https://docs.nvidia.com/cuda/archive/12.8.1/eula/index.html). 각 이미지의 모든 OS 패키지 버전을 확정한 대조가 아닙니다.
- `backend/pyproject.toml`의 빌드 도구 `hatchling`에는 버전이 없고, STT의 간접 의존성에는 별도 잠금 파일이 없습니다. 이번에는 선언된 버전과 잠금 파일에서 확인 가능한 항목까지 대조했습니다. 새 설치에서 결정되는 전체 버전이나 현재 서버 설치 전체가 확인됐다고 주장하지 않습니다. 해당 패키지·바이너리도 공개 ZIP에 포함되지 않습니다.
- 모바일 Java 4개 파일은 참조 코드입니다. Capacitor·Firebase의 API 이름이 등장하지만 Android SDK/AAR/APK는 배포하지 않습니다. 완성된 앱과 그 전체 의존성 라이선스 검토로 표현하지 않습니다.
- Ollama 실행 도구는 MIT이지만, `qwen3.8-27b:q5_k_m` 같은 로컬 태그만으로 원본 모델이나 변환 권리를 확인할 수 없습니다. 실제 텍스트·VL·STT 가중치의 upstream, revision, 변환 계보와 이용조건은 **미확인**으로 유지합니다. 가중치는 이번 605개 파일에 없으므로 공개 소스의 누락 고지와 섞지 않습니다. [Whisper 원본](https://huggingface.co/openai/whisper-large-v3-turbo), [Qwen3-VL 원본 프로젝트](https://github.com/QwenLM/Qwen3-VL)는 확인 출발점이지 서버 변환본의 허가 증명이 아닙니다.

## 판정

현재 공개 소스 목록과 이번에 추가한 고지 문서의 대조를 완료했습니다. 식별된 파일·잠금 버전에 관한 출처/라이선스 고지 누락은 보완했으며, 현재 소스 목록에서 확인된 이용조건 충돌은 없습니다. **미배포 모델·설치 시 결정되는 패키지·향후 바이너리·영상의 권리까지 승인하거나 모든 법적 위험이 없다고 보증하지 않습니다.** 이 경계를 유지한 상태로 README의 공개 파일 대조 항목을 완료 표시합니다.
