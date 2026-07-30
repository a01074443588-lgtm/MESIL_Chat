from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any
from uuid import UUID

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[UUID, list[tuple[UUID, WebSocket]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def connect(
        self, user_id: UUID, session_id: UUID, websocket: WebSocket
    ) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[user_id].append((session_id, websocket))

    async def disconnect(
        self, user_id: UUID, session_id: UUID, websocket: WebSocket
    ) -> None:
        async with self._lock:
            connections = self._connections.get(user_id, [])
            target = (session_id, websocket)
            if target in connections:
                connections.remove(target)
            if not connections:
                self._connections.pop(user_id, None)

    async def send_to_users(
        self, user_ids: set[UUID], payload: dict[str, Any]
    ) -> None:
        async with self._lock:
            targets = [
                (user_id, session_id, connection)
                for user_id in user_ids
                for session_id, connection in list(self._connections.get(user_id, []))
            ]
        failed: list[tuple[UUID, UUID, WebSocket]] = []
        for user_id, session_id, connection in targets:
            try:
                await connection.send_json(payload)
            except Exception:
                failed.append((user_id, session_id, connection))
        for user_id, session_id, connection in failed:
            await self.disconnect(user_id, session_id, connection)

    async def force_logout(self, user_id: UUID, reason: str) -> None:
        async with self._lock:
            targets = list(self._connections.pop(user_id, []))
        for _session_id, connection in targets:
            try:
                await connection.send_json({"event": "force_logout", "reason": reason})
                await connection.close(code=4003, reason=reason)
            except Exception:
                pass

    async def force_logout_sessions(
        self, user_id: UUID, session_ids: set[UUID], reason: str
    ) -> None:
        if not session_ids:
            return
        async with self._lock:
            current = self._connections.get(user_id, [])
            targets = [
                (session_id, connection)
                for session_id, connection in current
                if session_id in session_ids
            ]
            remaining = [
                (session_id, connection)
                for session_id, connection in current
                if session_id not in session_ids
            ]
            if remaining:
                self._connections[user_id] = remaining
            else:
                self._connections.pop(user_id, None)
        for _session_id, connection in targets:
            try:
                await connection.send_json({"event": "force_logout", "reason": reason})
                await connection.close(code=4003, reason=reason)
            except Exception:
                pass


manager = ConnectionManager()
