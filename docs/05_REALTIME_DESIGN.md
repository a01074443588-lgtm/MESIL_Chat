# 실시간 통신 설계

## 연결

브라우저는 로그인 HttpOnly 세션 쿠키로 `/api/ws`에 연결한다. 서버는 연결 전 세션 만료·폐기·재직상태를 확인한다.

## 이벤트

- `ready`: 연결 인증 완료
- `message_created`: 새 메시지 전체 표시자료
- `rooms_changed`: 소속·지정방 변경 후 목록 재조회
- `employees_changed`: 관리자 직원목록 재조회
- `ping` / `pong`: 25초 간격 세션 재검증
- `force_logout`: 퇴사 또는 보안상 강제종료

## 권한판정

메시지 조회·작성은 매 요청마다 `staff_hub_room_memberships.left_at IS NULL`을 확인한다. WebSocket은 전달수단일 뿐 권한 원본이 아니다.

## 현재 제한

연결목록이 단일 프로세스 메모리에 있으므로 백엔드를 여러 대 실행하면 프로세스 간 메시지가 자동 전달되지 않는다. 다중 서버 단계에서는 Redis Pub/Sub 또는 메시지 큐를 추가하고, DB 권한판정은 그대로 유지한다.
