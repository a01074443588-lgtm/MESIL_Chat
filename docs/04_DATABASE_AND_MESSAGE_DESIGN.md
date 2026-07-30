# 데이터베이스·메시지 설계

## 결정된 경계

- 현재 DB는 SMCODI_CHAT만 사용하는 독립 PostgreSQL이다.
- 기존 SMCODI DB에 접속하거나 데이터를 쓰지 않는다.
- SMCODI와 나중에 합치기 쉽도록 UUID, 시간대 포함 시각(`timestamptz`), JSONB, 조직·직원·어르신·StaffHub 명명 원칙을 맞췄다.
- SQLite 마이그레이션 파일과 백업 스크립트는 과거 시험자료로만 남기고 새 실행경로에서는 사용하지 않는다.

## 주요 테이블

| 영역 | 테이블 | 목적 |
|---|---|---|
| 기관 | `organizations`, `domain_modules` | 기관과 기능 모듈 경계 |
| 조직 | `organization_units` | 사업부·부서·층·팀을 데이터로 관리 |
| 직종·직위 | `staff_job_codes`, `staff_job_assignments`, `staff.position_title` | 실제 업무 직종과 기관 내부 직위를 분리 저장 |
| 직원 | `staff`, `staff_organization_assignments` | 인사정보와 조직 배정 |
| 계정 | `users`, `roles`, `user_roles` | 로그인 계정과 역할을 인사정보에서 분리 |
| 어르신 | `rooms`, `recipients` | 물리 층·생활실과 가상 어르신 |
| 어르신 동기화 | `recipient_sync_batches`, `recipient_sync_items` | SMCODI 내보내기 비교·항목별 승인·반영 이력 |
| 채팅 | `staff_hub_rooms`, `staff_hub_room_memberships`, `staff_hub_room_membership_overrides` | 자동·지정 채팅방, 접근권한, 자동방의 관리자 추가·제외 예외 |
| 메시지 | `staff_hub_messages` | 유형·본문·선택적 어르신·확장 JSON |
| 메시지-어르신 | `staff_hub_message_recipient_links` | 한 메시지에 여러 어르신 후보·확정·제외 상태 연결 |
| 자료 | `attachments` | 이미지·음성·PDF 메타데이터와 실제 파일 경로 |
| 첨부 판독 | `attachment_text_extractions` | 이미지 OCR·음성 받아쓰기 상태, 모델 결과, 담당자 확인문 |
| OCR 교정기억 | `staff_hub_ocr_correction_memories` | 담당자가 직접 고친 안전한 1:1 단어 교정과 반복횟수 |
| 확인 | `staff_hub_message_read_receipts`, `staff_hub_message_comments`, `staff_hub_message_thread_views` | 읽은 직원, 댓글, 댓글 마지막 확인시각 |
| 업무전달 | `staff_hub_action_items` | 보내기 단계에서 지정한 인수인계·업무협조·확인요청 |
| 방 요약 | `staff_hub_room_digests` | 방·기간 단위 요약의 향후 저장 경계 |
| 처리 | `staff_hub_processing_items` | 사람이 판독·분류할 업무자료 처리상태 |
| 당일서류 초안 | `staff_hub_work_item_document_drafts` | 업무자료별 서류 종류·버전·AI 초안·담당자 수정·승인 이력 |
| 보안 | `auth_sessions`, `auth_login_attempts`, `audit_events` | 세션·로그인 제한·감사기록 |

SMCODI의 현재 StaffHub에는 직종방이 없지만 채팅 프로젝트 요구에 따라 `staff_hub_rooms.room_type='job'`을 확장값으로 사용한다. 조직명과 직종명은 코드에 고정하지 않는다.

직종은 사회복지사·간호조무사·요양보호사처럼 실제 자격·업무 종류이고, 직위는 대표·원장·사무국장·선임사회복지사·간호팀장·요양팀장처럼 기관 안의 역할이다. 현재 직위는 직종 배정과 독립된 `staff.position_title`에 저장하고, `staff_job_assignments.position_title`은 과거 배정 이력의 호환정보로만 유지한다. 실제 직종을 아직 확인하지 못한 직원은 직위를 유지한 채 현재 직종을 미지정으로 둘 수 있으며, 이 경우 잘못된 직종 자동방에는 배정되지 않는다. 과거 시험자료의 `대표자·시설장·사무국장` 직종은 실제 자격으로 자동 변환하지 않고, 참조가 없어진 뒤 기록보존 방식으로 사용중지한다.

자동방의 기본 구성원은 사업부·부서·층·팀·직종 규칙으로 계산한다. 관리자가 특정 직원을 추가하거나 제외하면 `staff_hub_room_membership_overrides.action`에 `include` 또는 `exclude`로 기록한다. 조직 변경 뒤에도 이 예외는 유지되며, 관리자가 기본 구성으로 되돌리면 불필요한 예외행을 제거한다. 접근권한만 변경하고 과거 메시지·사진·댓글은 삭제하지 않는다.

## 메시지와 업무자료

현재 실제 작성 유형은 `chat`, `notice`다. 향후 `resident_report`, `voice_report`, `handover`, `task_request`, `task_result`, `system`을 추가할 수 있다.

작성자가 직접 한 어르신을 고른 경우에는 기존 `staff_hub_messages.recipient_id`를 대표 연결로 유지하면서 `staff_hub_message_recipient_links`에도 `manual/confirmed` 연결을 만든다. 작성자가 고르지 않은 일반 글이나 보고서 판독문에서는 현재 활성 어르신 명단과 정확히 일치하는 이름을 여러 개 찾아 `candidate`로 연결한다. 처리 담당자가 각 후보를 `confirmed` 또는 `rejected`로 검토하며, 후보가 남아 있으면 시험 제안을 만들 수 없다.

이미지·음성·PDF는 `attachments`에 저장하며 원본 파일은 `data/uploads`에 둔다. 어르신을 직접 고른 메시지, 이름 후보가 발견된 메시지, 또는 보고서 이미지에는 `staff_hub_processing_items`가 생성되고 작성시점의 본문·작성자·방·확인된 어르신 목록·첨부 ID를 `source_snapshot`에 보존한다. 기존 단일 `recipient_id`는 호환용 대표 어르신으로만 사용한다.

보고서 이미지와 지원되는 음성 첨부에는 `attachment_text_extractions`를 만든다. 보고서 이미지 전송 단계에서 어르신 선택은 필수가 아니다. 상태는 `pending → processing → completed/failed → reviewed`이며, OCR·받아쓰기 모델 결과(`extracted_text`)와 담당자 확인문(`reviewed_text`)을 덮어쓰지 않고 분리한다. 판독문에서 찾은 여러 이름도 후보 연결로 저장하며, 담당자 확인문이 저장되면 후보와 시험 제안을 다시 계산한다.

`staff_hub_rooms.room_type='self'`와 `owner_staff_id`는 직원 한 명만 참여하는 `나와의 대화` 방을 나타낸다. 메시지 전달은 새 메시지를 만들되 원본 메시지 ID·방·작성자 메타데이터를 보존하고 첨부파일은 별도 저장키로 복사한다. 전달 API도 현재 사용자가 가입한 활성 방만 대상으로 허용한다.

요양보호사의 입력 지연을 만들지 않도록 채팅 저장과 판독·AI 작업을 분리했다. 채팅은 먼저 즉시 저장·전달되고, 업무함에서 담당자가 `AI로 다시 정리`를 눌렀을 때만 Nemotron API → Qwen 35B → Gemma 4 → `prototype-rule-v1` 기초규칙 순으로 검토한다. 외부·로컬 모델이 빠뜨린 기존 위험도·확인질문·당일서류 후보는 서버가 기초규칙과 안전 병합한다. 제안은 `ai_payload`, 담당자가 수정·확정한 결과는 `confirmed_payload`에 분리하며 확정자와 확정시각도 저장한다. AI 검토는 원문을 바꾸지 않으며 확정 결과도 아직 실제 SMCODI 공식 서류로 자동 전송되지 않는다.

당일 작성 서류 초안은 `staff_hub_work_item_document_drafts`에 업무자료·서류 종류별 버전으로 추가 저장한다. 현재 실제 생성 대상은 급여제공기록지·간호일지·상담일지·신체제재 기록지·프로그램 운영기록지다. 담당자는 같은 화면에서 초안을 수정·저장하거나 AI에게 다시 작성하도록 요청하고, 사용하지 않을 서류는 제외할 수 있다. 최종 버튼은 선택된 최신 초안의 변경 내용을 먼저 저장한 뒤 업무내용과 함께 승인한다. 이름·시간·숫자·약·신체 부위·사건 및 고위험 내용은 자동 확정하지 않으며 확인질문에 사람이 응답해야 승인할 수 있다.

통합사정·급여제공계획·급여제공결과평가·낙상위험도·욕창위험도·인지기능·욕구사정은 단일 메시지에서 당일 초안으로 만들지 않는다. 어르신별 확정자료를 기간으로 누적해 담당자가 필요 시점에 호출하는 별도 기능으로 연결한다.

담당자가 OCR 판독문을 원본과 대조해 저장하면, 원문과 확인문 사이에서 동일 위치의 짧은 한글 단어 교정만 `staff_hub_ocr_correction_memories`에 누적한다. 전체 문장 재작성, 어르신 이름, 숫자와 복합 변경은 학습하지 않는다. 누적 교정은 다음 판독에서 자동 치환하지 않고 철자 후보와 로컬 AI 문맥으로만 사용한다.

댓글은 원문 메시지를 덮어쓰지 않고 별도 행으로 보존한다. `staff_hub_message_thread_views`로 사용자별 댓글 확인시각을 관리해 메시지 목록에 전체 댓글 수와 새 댓글 수를 함께 표시한다. 댓글 본문도 업무함의 방 요약과 판독 원문에 포함한다.

메시지를 보낼 때 선택적으로 `staff_hub_action_items`를 함께 만들 수 있다. 현재 유형은 인수인계, 업무협조, 확인요청이며 담당자와 처리상태를 원문 메시지에 연결한다. 일반 채팅은 이 단계를 사용하지 않아도 즉시 전송된다.

어르신의 기본 순서는 `recipients.sort_order`로 관리한다. 채팅방의 참여자 배정 기준(`unit_id`)과 어르신 우선층(`resident_scope_unit_id`)을 분리했으므로, 직접 선택 방도 참여 직원과 무관하게 특정 층 어르신을 목록 앞에 표시할 수 있다.

SMCODI 어르신 내보내기는 `recipient_sync_batches`에 파일명·SHA-256 요약값·내보내기 시각·상태를, `recipient_sync_items`에 외부 식별자·현재 스냅샷·신규/변경/이용중지/충돌 비교결과·승인시각을 저장한다. 원본 파일 자체는 저장하지 않는다. 동기화로 생성된 어르신만 `recipients.internal_code='SMCODI:{external_id}'` 범위에서 관리하므로 수동·시험 등록 어르신은 파일 누락만으로 중지되지 않는다. 이용중지는 삭제가 아니라 `is_active=false`, `status='inactive'`로 기록을 보존한다.

채팅방 종료는 물리 삭제가 아니라 `staff_hub_rooms.is_active=false`로 처리한다. 참여자의 활성 멤버십과 WebSocket 접근은 즉시 회수하지만 메시지·사진·댓글·업무기록은 보존하며 관리자가 복구할 수 있다. 조직정보와 직종도 같은 이유로 사용중지·복구 방식으로 관리한다.

## 업무기록 검토 상태

1. `pending`: 원문 스냅샷만 생성
2. `in_review`: 시험 제안 생성 또는 담당자 검토 중
3. `ready`: 담당자가 수정내용을 확인·확정
4. `dismissed`: 업무서류 후보로 사용하지 않음

시험 제안은 분류, 위험도, 전달대상, 서류 후보, 요약, 오타·띄어쓰기 검토문을 포함한다. `prototype_suggested` 또는 `ai_reviewed` 제안 없이 `ready` 상태로 바꾸는 API 요청은 거부한다.

## 서류 후보 조회

`/api/document-candidates`는 `ready` 상태이고 확정자·확정시각·확정내용이 모두 있는 항목만 반환한다. 서류 종류, 업무 분류, 위험도로 필터링할 수 있으며 현재 권한자의 사업부 범위 안에서만 집계한다. 화면은 읽기 전용이고 원문·확정문·확정자까지 함께 보여준다.

## 마이그레이션·백업

- 활성 Alembic 경로: `backend/migrations/postgres_versions`
- 현재 헤드: `020_document_draft_index_names`
- 검토용 기준 SQL: `backend/migrations/sql/001_smcodi_baseline.sql`
- DB 백업: PostgreSQL custom 형식의 `pg_dump`
- 첨부파일 백업: 운영 전환 뒤 보존이 필요할 때 `backup_postgres.ps1 -IncludeAttachments`
- 현재 프로토타입 사진은 사용자 결정에 따라 DB 백업에서 제외할 수 있다.
- `test-postgres-restore.ps1`은 현재 DB를 건드리지 않고 임시 DB에 복원해 핵심 테이블을 확인한다.
- 운영 복원 전에는 쓰기를 중지하고 DB 덤프와 필요한 첨부파일 백업 시점을 함께 확인한다.
