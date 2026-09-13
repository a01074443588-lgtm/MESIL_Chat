export function incrementRoomUnreadCount(rooms, roomId) {
  const index = rooms.findIndex((room) => room.id === roomId);
  if (index < 0) return rooms;

  return rooms.map((room, roomIndex) =>
    roomIndex === index
      ? { ...room, unread_count: Math.max(0, room.unread_count ?? 0) + 1 }
      : room,
  );
}
