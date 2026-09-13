# 공개 회귀시험 범위

공개 시험은 소스와 합성자료만으로 실행합니다. 과거 내부 실행 결과나 원본 매체를 가져오지 않습니다.

## 이전 실패 5건의 처리

| 이전 시험 | 분류 | 공개 후보의 처리 |
|---|---|---|
| test_finalist_five_care_planning_fixture_contract | 합성 데이터 계약 시험 + 과거 결과 확인 혼합 | 데이터 400건·근거·상태·승인 경계 검사를 유지. README 존재와 validation_result.json의 과거 성공값 확인만 제거. 첨부 형식은 실행 시 만든 합성 파일로 검사 |
| test_fixture_links_fourteen_coded_synthetic_attachments_to_existing_messages | 제품 fixture 구성 시험 | 14개 연결·대화방·MIME·합성 안전계약 검사를 유지. 입력 경로만 임시 합성 매체로 주입 |
| test_export_is_byte_deterministic_and_records_every_asset_hash | 제품 내보내기 회귀시험 | 실제 파일 읽기·복사·직렬화·해시 계산 코드를 그대로 실행. 임시 합성 매체를 사용하며 두 출력과 원본 바이트를 비교 |
| test_actual_internal_result_keeps_unapproved_final_and_privacy_boundaries | 과거 내부 실행 결과 증거 시험 | 공개 시험에서 제외. 특정 시점 내부 결과 JSON의 수치를 확인하는 시험이지 현재 소스의 생성 동작 시험이 아님 |
| test_consolidated_result_preserves_all_stages_without_auto_acceptance | 과거 내부 통합 결과 증거 시험 | 공개 시험에서 제외. 비공개 결과를 복사하거나 가짜 성공 결과를 만들지 않음 |

제외한 2개 함수와 결과 경로 상수만 공개 사본에서 제거했습니다. 같은 모듈의 비식별 처리·사실 보존·수정 프롬프트·승인 대기 계약 시험 5개는 유지합니다. 원본 저장소의 시험은 변경하지 않습니다. skip/xfail로 감추지 않습니다.

`public_synthetic_media.py`는 임시 폴더에 숫자 코드가 든 기하학적 이미지/PDF와 수학적 사인파 WAV를 만듭니다. 직원·어르신·실제 손글씨·녹음·비공개 결과를 사용하지 않습니다. 이 자료로 OCR/STT 인식 정확도나 실제 음성 전달을 주장하지 않습니다. 검증 대상은 첨부 연결·파일 전달·보존·해시 계약입니다.

전체 시험 수는 원래 1,144개에서 내부 증거 전용 2개를 제외한 1,142개입니다. 새 개인정보 없는 설치 DB에서 실행하며 비밀 설정은 환경변수로 별도 제공해야 합니다.
