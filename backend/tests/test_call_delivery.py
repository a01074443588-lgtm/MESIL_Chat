import asyncio
import threading
import time
from uuid import uuid4

from app.call_delivery import CallDeliveryCoordinator
from app import main as main_module


def test_delivery_paths_are_isolated_deduplicated_and_bounded():
    async def exercise():
        results = []
        coordinator = CallDeliveryCoordinator()
        slow_started = asyncio.Event()
        mobile_done = asyncio.Event()

        async def slow_web():
            slow_started.set()
            await asyncio.sleep(60)

        async def mobile():
            mobile_done.set()
            return 1

        def record(result):
            results.append(result)

        assert coordinator.schedule(
            ("synthetic-call", "recipient", "browser", "invite"),
            slow_web,
            timeout_seconds=0.02,
            on_result=record,
        )
        assert not coordinator.schedule(
            ("synthetic-call", "recipient", "browser", "invite"),
            slow_web,
            timeout_seconds=0.02,
            on_result=record,
        )
        assert coordinator.schedule(
            ("synthetic-call", "recipient", "android", "invite"),
            mobile,
            timeout_seconds=0.2,
            on_result=record,
        )

        await asyncio.wait_for(slow_started.wait(), 0.2)
        await asyncio.wait_for(mobile_done.wait(), 0.2)
        await coordinator.drain()

        by_path = {result.key[2]: result for result in results}
        assert by_path["browser"].status == "timeout"
        assert by_path["android"].status == "success"
        assert coordinator.pending_count == 0

    asyncio.run(exercise())


def test_expired_invite_is_not_scheduled():
    async def exercise():
        coordinator = CallDeliveryCoordinator(now_ms=lambda: 10_000)
        called = False

        async def operation():
            nonlocal called
            called = True

        assert not coordinator.schedule(
            ("expired-call", "recipient", "android", "invite"),
            operation,
            timeout_seconds=1,
            expires_at_ms=9_999,
        )
        await coordinator.drain()
        assert called is False

    asyncio.run(exercise())


def test_real_delivery_scheduler_does_not_wait_for_slow_web_paths(monkeypatch):
    async def exercise():
        coordinator = CallDeliveryCoordinator()
        mobile_started = threading.Event()
        release_web = threading.Event()
        caller_events = []

        async def send_to_users(user_ids, payload):
            if payload.get("event") == "voice_call_delivery_status":
                caller_events.append(payload)
                return len(user_ids)
            await asyncio.sleep(0.05)
            return 0

        def send_mobile(_user_ids, *, payload, ttl_seconds):
            mobile_started.set()
            return 1

        def send_browser(_user_ids, **_kwargs):
            # Keep the external push blocked until the coordinator has reported
            # its timeout. Releasing on mobile start races the timeout itself.
            release_web.wait(5)
            return 0

        monkeypatch.setattr(main_module, "VOICE_CALL_DELIVERY", coordinator)
        monkeypatch.setattr(main_module, "VOICE_CALL_WEBSOCKET_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(main_module, "VOICE_CALL_BROWSER_PUSH_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(main_module.manager, "send_to_users", send_to_users)
        monkeypatch.setattr(main_module, "send_voice_call_mobile_push", send_mobile)
        monkeypatch.setattr(main_module, "send_voice_call_web_push", send_browser)
        monkeypatch.setattr(main_module, "_record_voice_call_delivery_result", lambda **_kwargs: None)

        now_ms = int(time.time() * 1000)
        main_module._schedule_voice_call_delivery(
            call_id=uuid4(),
            room_id=uuid4(),
            caller_user_id=uuid4(),
            caller_name="합성 발신자",
            recipient_user_id=uuid4(),
            call_mode="audio",
            member_count=2,
            mobile_action_token="synthetic-action-token",
            expires_at_ms=now_ms + 50_000,
            websocket_payload={"event": "voice_call_invite"},
        )

        try:
            assert await asyncio.to_thread(
                mobile_started.wait, 0.2
            ), "느린 웹 경로가 Android 전송 시작을 막았습니다."
            await asyncio.wait_for(coordinator.drain(), 1)
        finally:
            release_web.set()

        assert caller_events == [
            {
                "event": "voice_call_delivery_status",
                "call_id": caller_events[0]["call_id"],
                "delivery_complete": True,
                "delivered": True,
                "path_statuses": {
                    "android": "success",
                    "browser": "timeout",
                    "websocket": "timeout",
                },
            }
        ]

    asyncio.run(exercise())
