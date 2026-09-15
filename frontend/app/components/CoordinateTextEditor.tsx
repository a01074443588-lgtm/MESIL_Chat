"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { apiFetch } from "../api";
import type {
  Attachment,
  AttachmentCoordinateReview,
  CoordinateBox,
  CoordinateRegion,
} from "../types";

type Tool = "select" | "add" | "place" | "merge";

const EDITOR_VERSION = "coordinate-editor-group-transform-5";
const MIN_SIZE = 0.015;
const MAX_ROTATION_DEGREES = 30;
const ROTATION_STEP_DEGREES = 1;
const ROTATION_POINTER_SENSITIVITY = 0.4;
const ROTATION_SNAP_DEGREES = 0.5;
const MAX_UNDO_STEPS = 30;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function nextClientId() {
  return `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function rotationDegrees(box: CoordinateBox) {
  const value = box.rotation_degrees ?? 0;
  return Number.isFinite(value)
    ? clamp(value, -MAX_ROTATION_DEGREES, MAX_ROTATION_DEGREES)
    : 0;
}

function boxStyle(box: CoordinateBox) {
  return {
    left: `${box.left * 100}%`,
    top: `${box.top * 100}%`,
    width: `${box.width * 100}%`,
    height: `${box.height * 100}%`,
    transform: `rotate(${rotationDegrees(box)}deg)`,
    transformOrigin: "center center",
  };
}

function cloneRegions(regions: CoordinateRegion[]) {
  return regions.map((region) => ({
    ...region,
    bbox: { ...region.bbox },
  }));
}

function normalizedAngleDelta(value: number) {
  let result = value;
  while (result > 180) result -= 360;
  while (result < -180) result += 360;
  return result;
}

function rotatedBoxFitsCanvas(box: CoordinateBox, canvas: HTMLElement) {
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return false;
  const radians = (rotationDegrees(box) * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const width = box.width * bounds.width;
  const height = box.height * bounds.height;
  const centerX = (box.left + box.width / 2) * bounds.width;
  const centerY = (box.top + box.height / 2) * bounds.height;
  const corners = [
    [-width / 2, -height / 2],
    [width / 2, -height / 2],
    [width / 2, height / 2],
    [-width / 2, height / 2],
  ];
  return corners.every(([localX, localY]) => {
    const x = centerX + localX * cos - localY * sin;
    const y = centerY + localX * sin + localY * cos;
    return (
      x >= -0.5 &&
      x <= bounds.width + 0.5 &&
      y >= -0.5 &&
      y <= bounds.height + 0.5
    );
  });
}

function roundedRotation(value: number) {
  return Math.round(value / ROTATION_SNAP_DEGREES) * ROTATION_SNAP_DEGREES;
}

function safeSharedRotationDelta(
  originals: { id: string; bbox: CoordinateBox }[],
  desiredDelta: number,
  canvas: HTMLElement,
) {
  if (!originals.length || Math.abs(desiredDelta) < 0.001) return 0;
  const minimumDelta = Math.max(
    ...originals.map(
      ({ bbox }) => -MAX_ROTATION_DEGREES - rotationDegrees(bbox),
    ),
  );
  const maximumDelta = Math.min(
    ...originals.map(
      ({ bbox }) => MAX_ROTATION_DEGREES - rotationDegrees(bbox),
    ),
  );
  const boundedDelta = clamp(desiredDelta, minimumDelta, maximumDelta);
  const allFit = (delta: number) =>
    originals.every(({ bbox }) =>
      rotatedBoxFitsCanvas(
        {
          ...bbox,
          rotation_degrees: roundedRotation(rotationDegrees(bbox) + delta),
        },
        canvas,
      ),
    );
  if (allFit(boundedDelta)) return boundedDelta;

  let safeMagnitude = 0;
  let unsafeMagnitude = Math.abs(boundedDelta);
  const direction = Math.sign(boundedDelta);
  for (let index = 0; index < 14; index += 1) {
    const middle = (safeMagnitude + unsafeMagnitude) / 2;
    if (allFit(direction * middle)) safeMagnitude = middle;
    else unsafeMagnitude = middle;
  }
  return direction * safeMagnitude;
}

function safeSharedMoveDelta(
  originals: { id: string; bbox: CoordinateBox }[],
  desiredDeltaX: number,
  desiredDeltaY: number,
  canvas: HTMLElement,
) {
  const bounds = canvas.getBoundingClientRect();
  if (!originals.length || !bounds.width || !bounds.height) {
    return { x: 0, y: 0 };
  }

  let minimumDeltaX = Number.NEGATIVE_INFINITY;
  let maximumDeltaX = Number.POSITIVE_INFINITY;
  let minimumDeltaY = Number.NEGATIVE_INFINITY;
  let maximumDeltaY = Number.POSITIVE_INFINITY;

  originals.forEach(({ bbox }) => {
    const radians = (rotationDegrees(bbox) * Math.PI) / 180;
    const widthPixels = bbox.width * bounds.width;
    const heightPixels = bbox.height * bounds.height;
    const halfWidth =
      (Math.abs(Math.cos(radians)) * widthPixels +
        Math.abs(Math.sin(radians)) * heightPixels) /
      2 /
      bounds.width;
    const halfHeight =
      (Math.abs(Math.sin(radians)) * widthPixels +
        Math.abs(Math.cos(radians)) * heightPixels) /
      2 /
      bounds.height;
    const centerX = bbox.left + bbox.width / 2;
    const centerY = bbox.top + bbox.height / 2;
    minimumDeltaX = Math.max(minimumDeltaX, halfWidth - centerX);
    maximumDeltaX = Math.min(maximumDeltaX, 1 - halfWidth - centerX);
    minimumDeltaY = Math.max(minimumDeltaY, halfHeight - centerY);
    maximumDeltaY = Math.min(maximumDeltaY, 1 - halfHeight - centerY);
  });

  if (minimumDeltaX > maximumDeltaX || minimumDeltaY > maximumDeltaY) {
    return { x: 0, y: 0 };
  }
  return {
    x: clamp(desiredDeltaX, minimumDeltaX, maximumDeltaX),
    y: clamp(desiredDeltaY, minimumDeltaY, maximumDeltaY),
  };
}

function resizedRotatedBox(
  original: CoordinateBox,
  point: { x: number; y: number },
  canvas: HTMLElement,
) {
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return original;
  const radians = (rotationDegrees(original) * Math.PI) / 180;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  const widthPixels = original.width * bounds.width;
  const heightPixels = original.height * bounds.height;
  const centerX = (original.left + original.width / 2) * bounds.width;
  const centerY = (original.top + original.height / 2) * bounds.height;
  const anchorX = centerX - (widthPixels / 2) * cos + (heightPixels / 2) * sin;
  const anchorY = centerY - (widthPixels / 2) * sin - (heightPixels / 2) * cos;
  const pointerX = point.x * bounds.width;
  const pointerY = point.y * bounds.height;
  const deltaX = pointerX - anchorX;
  const deltaY = pointerY - anchorY;
  const localWidth = deltaX * cos + deltaY * sin;
  const localHeight = -deltaX * sin + deltaY * cos;
  if (
    localWidth < MIN_SIZE * bounds.width ||
    localHeight < MIN_SIZE * bounds.height
  ) {
    return original;
  }
  const nextWidth = Math.max(MIN_SIZE * bounds.width, localWidth);
  const nextHeight = Math.max(MIN_SIZE * bounds.height, localHeight);
  const nextCenterX = anchorX + (nextWidth / 2) * cos - (nextHeight / 2) * sin;
  const nextCenterY = anchorY + (nextWidth / 2) * sin + (nextHeight / 2) * cos;
  const candidate: CoordinateBox = {
    ...original,
    left: nextCenterX / bounds.width - nextWidth / bounds.width / 2,
    top: nextCenterY / bounds.height - nextHeight / bounds.height / 2,
    width: nextWidth / bounds.width,
    height: nextHeight / bounds.height,
  };
  return rotatedBoxFitsCanvas(candidate, canvas) ? candidate : original;
}

function splitRotatedBox(original: CoordinateBox, canvas: HTMLElement) {
  const bounds = canvas.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return null;
  const radians = (rotationDegrees(original) * Math.PI) / 180;
  const childHeight = original.height / 2;
  const offsetPixels = (original.height * bounds.height) / 4;
  const centerX = (original.left + original.width / 2) * bounds.width;
  const centerY = (original.top + original.height / 2) * bounds.height;
  const offsetX = -Math.sin(radians) * offsetPixels;
  const offsetY = Math.cos(radians) * offsetPixels;
  const childBox = (
    childCenterX: number,
    childCenterY: number,
  ): CoordinateBox => ({
    ...original,
    left: childCenterX / bounds.width - original.width / 2,
    top: childCenterY / bounds.height - childHeight / 2,
    height: childHeight,
    rotation_degrees: rotationDegrees(original),
  });
  const first = childBox(centerX - offsetX, centerY - offsetY);
  const second = childBox(centerX + offsetX, centerY + offsetY);
  return rotatedBoxFitsCanvas(first, canvas) &&
    rotatedBoxFitsCanvas(second, canvas)
    ? ([first, second] as const)
    : null;
}

function normalizedPoint(
  event: ReactPointerEvent<HTMLElement>,
  element: HTMLElement,
) {
  const bounds = element.getBoundingClientRect();
  return {
    x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
    y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1),
  };
}

export function CoordinateTextEditor({
  attachment,
  imageUrl,
  onClose,
  onSaved,
}: {
  attachment: Attachment;
  imageUrl: string;
  onClose: () => void;
  onSaved?: (review: AttachmentCoordinateReview) => void;
}) {
  const [review, setReview] = useState<AttachmentCoordinateReview | null>(null);
  const [regions, setRegions] = useState<CoordinateRegion[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [mergeIds, setMergeIds] = useState<string[]>([]);
  const [tool, setTool] = useState<Tool>("select");
  const [undoCount, setUndoCount] = useState(0);
  const [scale, setScale] = useState(1);
  const [showOverlay, setShowOverlay] = useState(true);
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const [loading, setLoading] = useState(true);
  const [autoLocating, setAutoLocating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const originalScrollRef = useRef<HTMLDivElement | null>(null);
  const overlayScrollRef = useRef<HTMLDivElement | null>(null);
  const overlayCanvasRef = useRef<HTMLDivElement | null>(null);
  const toolMenuRef = useRef<HTMLDivElement | null>(null);
  const regionElementRefs = useRef(new Map<string, HTMLButtonElement>());
  const selectedEditorRef = useRef<HTMLTextAreaElement | null>(null);
  const syncingScrollRef = useRef(false);
  const undoStackRef = useRef<
    {
      regions: CoordinateRegion[];
      selectedId: string | null;
      selectedIds: string[];
    }[]
  >([]);
  const gestureRef = useRef<{
    kind: "add" | "move" | "resize" | "rotate";
    id?: string;
    startX: number;
    startY: number;
    startClientX?: number;
    startClientY?: number;
    startPointerAngle?: number;
    moved?: boolean;
    changed?: boolean;
    original?: CoordinateBox;
    moveOriginals?: { id: string; bbox: CoordinateBox }[];
    rotationOriginals?: { id: string; bbox: CoordinateBox }[];
    snapshot?: CoordinateRegion[];
    snapshotSelectedId?: string | null;
    snapshotSelectedIds?: string[];
  } | null>(null);

  useEffect(() => {
    function closeToolMenuOnOutsidePointer(event: PointerEvent) {
      const menu = toolMenuRef.current;
      if (
        menu &&
        event.target instanceof Node &&
        !menu.contains(event.target)
      ) {
        setToolMenuOpen(false);
      }
    }

    document.addEventListener(
      "pointerdown",
      closeToolMenuOnOutsidePointer,
      true,
    );
    return () => {
      document.removeEventListener(
        "pointerdown",
        closeToolMenuOnOutsidePointer,
        true,
      );
    };
  }, []);

  const selected = useMemo(
    () => regions.find((region) => region.client_id === selectedId) ?? null,
    [regions, selectedId],
  );

  const selectedIndex = useMemo(
    () => regions.findIndex((region) => region.client_id === selectedId),
    [regions, selectedId],
  );

  const placedRegions = useMemo(
    () =>
      regions.filter((region) => region.placement_status !== "needs_position"),
    [regions],
  );

  const unplacedRegions = useMemo(
    () =>
      regions.filter((region) => region.placement_status === "needs_position"),
    [regions],
  );

  useEffect(() => {
    let active = true;
    void apiFetch<AttachmentCoordinateReview>(
      `/api/attachments/${attachment.id}/coordinate-review`,
    )
      .then(async (value) => {
        if (!active) return;
        setReview(value);
        setRegions(value.regions);
        setSelectedId(value.regions[0]?.client_id ?? null);
        setSelectedIds(
          value.regions[0]?.client_id ? [value.regions[0].client_id] : [],
        );
        undoStackRef.current = [];
        setUndoCount(0);
        const needsAutoLocation =
          value.is_bootstrap &&
          value.regions.some(
            (region) => region.placement_status === "needs_position",
          );
        if (!needsAutoLocation) return;
        setAutoLocating(true);
        setFeedback("판독문이 적힌 위치를 자동으로 찾는 중입니다.");
        try {
          const located = await apiFetch<AttachmentCoordinateReview>(
            `/api/attachments/${attachment.id}/coordinate-review/auto-locate`,
            { method: "POST" },
          );
          if (!active) return;
          setReview(located);
          setRegions(located.regions);
          setSelectedId(located.regions[0]?.client_id ?? null);
          setSelectedIds(
            located.regions[0]?.client_id ? [located.regions[0].client_id] : [],
          );
          undoStackRef.current = [];
          setUndoCount(0);
          const placedCount = located.regions.filter(
            (region) => region.placement_status !== "needs_position",
          ).length;
          setFeedback(
            placedCount > 0
              ? `판독문 ${placedCount}개 위치를 자동으로 찾았습니다.`
              : "자동으로 확인된 위치가 없습니다. 필요한 글자만 직접 지정해 주세요.",
          );
        } catch (reason) {
          if (!active) return;
          setFeedback(
            reason instanceof Error
              ? `자동 위치 찾기 실패: ${reason.message}`
              : "자동 위치를 찾지 못했습니다. 필요한 글자만 직접 지정해 주세요.",
          );
        } finally {
          if (active) setAutoLocating(false);
        }
      })
      .catch((reason) => {
        if (active) {
          setFeedback(
            reason instanceof Error
              ? reason.message
              : "좌표 판독문을 열지 못했습니다.",
          );
        }
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [attachment.id]);

  const updateRegion = useCallback(
    (id: string, update: Partial<CoordinateRegion>) => {
      setRegions((current) =>
        current.map((region) =>
          region.client_id === id ? { ...region, ...update } : region,
        ),
      );
    },
    [],
  );

  const rememberSnapshot = useCallback(
    (
      snapshot = cloneRegions(regions),
      snapshotSelectedId = selectedId,
      snapshotSelectedIds = selectedIds,
    ) => {
      undoStackRef.current = [
        ...undoStackRef.current.slice(-(MAX_UNDO_STEPS - 1)),
        {
          regions: cloneRegions(snapshot),
          selectedId: snapshotSelectedId,
          selectedIds: [...snapshotSelectedIds],
        },
      ];
      setUndoCount(undoStackRef.current.length);
    },
    [regions, selectedId, selectedIds],
  );

  function undoLastChange() {
    const previous = undoStackRef.current.pop();
    setUndoCount(undoStackRef.current.length);
    if (!previous) return;
    setRegions(cloneRegions(previous.regions));
    setSelectedId(
      previous.selectedId &&
        previous.regions.some(
          (region) => region.client_id === previous.selectedId,
        )
        ? previous.selectedId
        : (previous.regions[0]?.client_id ?? null),
    );
    setSelectedIds(
      previous.selectedIds.filter((id) =>
        previous.regions.some((region) => region.client_id === id),
      ),
    );
    setMergeIds([]);
    setTool("select");
    setFeedback("마지막 상자 조정을 되돌렸습니다.");
  }

  const revealRegion = useCallback((id: string, focusEditor = false) => {
    setSelectedId(id);
    setSelectedIds([id]);
    requestAnimationFrame(() => {
      regionElementRefs.current.get(id)?.scrollIntoView({
        block: "center",
        inline: "center",
        behavior: "smooth",
      });
      if (focusEditor) {
        const editor = selectedEditorRef.current;
        editor?.focus({ preventScroll: true });
        editor?.setSelectionRange(0, editor.value.length);
      }
    });
  }, []);

  const synchronizeScroll = useCallback(
    (source: HTMLDivElement, target: HTMLDivElement | null) => {
      if (!target || syncingScrollRef.current) return;
      syncingScrollRef.current = true;
      const xMax = Math.max(1, source.scrollWidth - source.clientWidth);
      const yMax = Math.max(1, source.scrollHeight - source.clientHeight);
      const targetXMax = Math.max(0, target.scrollWidth - target.clientWidth);
      const targetYMax = Math.max(0, target.scrollHeight - target.clientHeight);
      target.scrollLeft = (source.scrollLeft / xMax) * targetXMax;
      target.scrollTop = (source.scrollTop / yMax) * targetYMax;
      requestAnimationFrame(() => {
        syncingScrollRef.current = false;
      });
    },
    [],
  );

  function applyAndGoNext(id: string) {
    const currentIndex = regions.findIndex((region) => region.client_id === id);
    if (currentIndex < 0) return;
    const current = regions[currentIndex];
    const next = regions[currentIndex + 1];
    updateRegion(id, { review_required: false });
    setFeedback(
      current.placement_status === "needs_position"
        ? "글자는 반영했습니다. 이 항목은 원문 위치 확인이 필요합니다."
        : "수정한 글자를 원문 위치에 반영했습니다.",
    );
    if (next) {
      revealRegion(next.client_id, true);
    }
  }

  function startCanvasGesture(event: ReactPointerEvent<HTMLDivElement>) {
    if ((tool !== "add" && tool !== "place") || !overlayCanvasRef.current)
      return;
    if (event.target !== event.currentTarget) return;
    const point = normalizedPoint(event, overlayCanvasRef.current);
    event.currentTarget.setPointerCapture(event.pointerId);
    gestureRef.current = {
      kind: "add",
      id: tool === "place" ? (selectedId ?? undefined) : undefined,
      startX: point.x,
      startY: point.y,
      snapshot: cloneRegions(regions),
      snapshotSelectedId: selectedId,
      snapshotSelectedIds: [...selectedIds],
    };
  }

  function startRegionGesture(
    event: ReactPointerEvent<HTMLButtonElement>,
    region: CoordinateRegion,
  ) {
    event.stopPropagation();
    if (tool === "merge") {
      setMergeIds((current) =>
        current.includes(region.client_id)
          ? current.filter((id) => id !== region.client_id)
          : [...current, region.client_id],
      );
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      setSelectedIds((current) => {
        if (current.includes(region.client_id)) {
          const next = current.filter((id) => id !== region.client_id);
          setSelectedId((primary) =>
            primary === region.client_id ? (next.at(-1) ?? null) : primary,
          );
          setFeedback(
            next.length > 1
              ? `${next.length}개 상자를 함께 이동·기울이도록 선택했습니다.`
              : next.length === 1
                ? "상자 1개를 선택했습니다."
                : "선택을 해제했습니다.",
          );
          return next;
        }
        const next = [...current, region.client_id];
        setSelectedId(region.client_id);
        setFeedback(
          `${next.length}개 상자를 함께 이동·기울이도록 선택했습니다.`,
        );
        return next;
      });
      return;
    }
    const moveIds =
      selectedIds.includes(region.client_id) && selectedIds.length > 1
        ? selectedIds
        : [region.client_id];
    const moveOriginals = regions
      .filter((item) => moveIds.includes(item.client_id))
      .map((item) => ({ id: item.client_id, bbox: { ...item.bbox } }));
    setSelectedId(region.client_id);
    setSelectedIds(moveIds);
    if (tool !== "select" || !overlayCanvasRef.current) return;
    const canvas = overlayCanvasRef.current;
    const point = normalizedPoint(event, canvas);
    canvas.setPointerCapture(event.pointerId);
    gestureRef.current = {
      kind: "move",
      id: region.client_id,
      startX: point.x,
      startY: point.y,
      startClientX: event.clientX,
      startClientY: event.clientY,
      moved: false,
      original: region.bbox,
      moveOriginals,
      snapshot: cloneRegions(regions),
      snapshotSelectedId: selectedId,
      snapshotSelectedIds: [...selectedIds],
    };
  }

  function startResize(event: ReactPointerEvent<HTMLSpanElement>) {
    if (!selected || !overlayCanvasRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    const canvas = overlayCanvasRef.current;
    const point = normalizedPoint(event, canvas);
    canvas.setPointerCapture(event.pointerId);
    gestureRef.current = {
      kind: "resize",
      id: selected.client_id,
      startX: point.x,
      startY: point.y,
      original: selected.bbox,
      snapshot: cloneRegions(regions),
      snapshotSelectedId: selectedId,
      snapshotSelectedIds: [...selectedIds],
    };
  }

  function startRotate(event: ReactPointerEvent<HTMLSpanElement>) {
    if (!selected || !overlayCanvasRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    const canvas = overlayCanvasRef.current;
    const bounds = canvas.getBoundingClientRect();
    const centerX =
      bounds.left +
      (selected.bbox.left + selected.bbox.width / 2) * bounds.width;
    const centerY =
      bounds.top +
      (selected.bbox.top + selected.bbox.height / 2) * bounds.height;
    const rotationIds = selectedIds.includes(selected.client_id)
      ? selectedIds
      : [selected.client_id];
    const rotationOriginals = regions
      .filter((region) => rotationIds.includes(region.client_id))
      .map((region) => ({ id: region.client_id, bbox: { ...region.bbox } }));
    canvas.setPointerCapture(event.pointerId);
    gestureRef.current = {
      kind: "rotate",
      id: selected.client_id,
      startX: centerX,
      startY: centerY,
      startPointerAngle: Math.atan2(
        event.clientY - centerY,
        event.clientX - centerX,
      ),
      original: selected.bbox,
      rotationOriginals,
      snapshot: cloneRegions(regions),
      snapshotSelectedId: selectedId,
      snapshotSelectedIds: [...selectedIds],
    };
  }

  function moveGesture(event: ReactPointerEvent<HTMLDivElement>) {
    const gesture = gestureRef.current;
    const canvas = overlayCanvasRef.current;
    if (!gesture || !canvas) return;
    const point = normalizedPoint(event, canvas);
    if (
      gesture.kind === "move" &&
      gesture.id &&
      gesture.original &&
      gesture.moveOriginals?.length
    ) {
      const distance = Math.hypot(
        event.clientX - (gesture.startClientX ?? event.clientX),
        event.clientY - (gesture.startClientY ?? event.clientY),
      );
      if (!gesture.moved && distance < 4) return;
      gesture.moved = true;
      const safeDelta = safeSharedMoveDelta(
        gesture.moveOriginals,
        point.x - gesture.startX,
        point.y - gesture.startY,
        canvas,
      );
      gesture.changed =
        Math.abs(safeDelta.x) > 0.00001 || Math.abs(safeDelta.y) > 0.00001;
      const nextById = new Map(
        gesture.moveOriginals.map(({ id, bbox }) => [
          id,
          {
            ...bbox,
            left: bbox.left + safeDelta.x,
            top: bbox.top + safeDelta.y,
          },
        ]),
      );
      setRegions((current) =>
        current.map((region) => {
          const bbox = nextById.get(region.client_id);
          return bbox
            ? {
                ...region,
                bbox,
                placement_status: "confirmed",
                position_confidence: 1,
                source: "manual",
              }
            : region;
        }),
      );
    }
    if (gesture.kind === "resize" && gesture.id && gesture.original) {
      const bbox = resizedRotatedBox(gesture.original, point, canvas);
      gesture.changed =
        bbox.left !== gesture.original.left ||
        bbox.top !== gesture.original.top ||
        bbox.width !== gesture.original.width ||
        bbox.height !== gesture.original.height;
      updateRegion(gesture.id, {
        bbox,
        placement_status: "confirmed",
        position_confidence: 1,
        source: "manual",
      });
    }
    if (
      gesture.kind === "rotate" &&
      gesture.id &&
      gesture.original &&
      gesture.rotationOriginals?.length &&
      gesture.startPointerAngle !== undefined
    ) {
      const pointerAngle = Math.atan2(
        event.clientY - gesture.startY,
        event.clientX - gesture.startX,
      );
      const rawDelta = normalizedAngleDelta(
        ((pointerAngle - gesture.startPointerAngle) * 180) / Math.PI,
      );
      const desiredDelta = rawDelta * ROTATION_POINTER_SENSITIVITY;
      const safeDelta = safeSharedRotationDelta(
        gesture.rotationOriginals,
        desiredDelta,
        canvas,
      );
      const nextById = new Map(
        gesture.rotationOriginals.map(({ id, bbox }) => [
          id,
          {
            ...bbox,
            rotation_degrees: roundedRotation(
              rotationDegrees(bbox) + safeDelta,
            ),
          },
        ]),
      );
      gesture.changed = Math.abs(safeDelta) >= ROTATION_SNAP_DEGREES / 2;
      setRegions((current) =>
        current.map((region) => {
          const bbox = nextById.get(region.client_id);
          return bbox
            ? {
                ...region,
                bbox,
                placement_status: "confirmed",
                position_confidence: 1,
                source: "manual",
              }
            : region;
        }),
      );
    }
  }

  function endGesture(event: ReactPointerEvent<HTMLDivElement>) {
    const gesture = gestureRef.current;
    const canvas = overlayCanvasRef.current;
    if (!gesture || !canvas) return;
    if (gesture.kind === "add") {
      const point = normalizedPoint(event, canvas);
      const left = Math.min(gesture.startX, point.x);
      const top = Math.min(gesture.startY, point.y);
      const width = Math.max(MIN_SIZE, Math.abs(point.x - gesture.startX));
      const height = Math.max(MIN_SIZE, Math.abs(point.y - gesture.startY));
      const id = gesture.id ?? nextClientId();
      const bbox = {
        left,
        top,
        width: Math.min(width, 1 - left),
        height: Math.min(height, 1 - top),
        rotation_degrees: 0,
      };
      if (gesture.id) {
        updateRegion(gesture.id, {
          bbox,
          placement_status: "confirmed",
          position_confidence: 1,
          source: "manual",
        });
        setFeedback("선택한 글자를 원문 위치에 연결했습니다.");
      } else {
        setRegions((current) => [
          ...current,
          {
            client_id: id,
            bbox,
            raw_text: "",
            corrected_text: "",
            role: "general",
            document_template: review?.document_template ?? null,
            section_role: null,
            resident_id: null,
            position_confidence: 1,
            review_required: false,
            placement_status: "confirmed",
            source: "manual",
          },
        ]);
      }
      setSelectedId(id);
      setSelectedIds([id]);
      setTool("select");
      if (gesture.snapshot) {
        rememberSnapshot(
          gesture.snapshot,
          gesture.snapshotSelectedId ?? null,
          gesture.snapshotSelectedIds ?? [],
        );
      }
    }
    if (gesture.kind === "move" && gesture.changed) {
      if (gesture.snapshot) {
        rememberSnapshot(
          gesture.snapshot,
          gesture.snapshotSelectedId ?? null,
          gesture.snapshotSelectedIds ?? [],
        );
      }
      const count = gesture.moveOriginals?.length ?? 1;
      setFeedback(
        count > 1
          ? `${count}개 상자를 함께 이동했습니다. 저장하면 다음에도 유지됩니다.`
          : "글자 영역 위치를 옮겼습니다. 저장하면 다음에도 유지됩니다.",
      );
    }
    if (gesture.kind === "resize" && gesture.changed) {
      if (gesture.snapshot) {
        rememberSnapshot(
          gesture.snapshot,
          gesture.snapshotSelectedId ?? null,
          gesture.snapshotSelectedIds ?? [],
        );
      }
      setFeedback(
        "글자 영역 크기를 조정했습니다. 저장하면 다음에도 유지됩니다.",
      );
    }
    if (gesture.kind === "rotate" && gesture.changed) {
      if (gesture.snapshot) {
        rememberSnapshot(
          gesture.snapshot,
          gesture.snapshotSelectedId ?? null,
          gesture.snapshotSelectedIds ?? [],
        );
      }
      const count = gesture.rotationOriginals?.length ?? 1;
      setFeedback(
        `${count}개 상자를 같은 만큼 기울였습니다. 저장하면 비교 자료에 각도도 남습니다.`,
      );
    }
    gestureRef.current = null;
  }

  function cancelGesture() {
    const gesture = gestureRef.current;
    if (gesture?.changed && gesture.snapshot) {
      setRegions(cloneRegions(gesture.snapshot));
      setSelectedId(gesture.snapshotSelectedId ?? null);
      setSelectedIds(gesture.snapshotSelectedIds ?? []);
    }
    gestureRef.current = null;
  }

  function splitSelected() {
    if (!selected) {
      setFeedback("먼저 나눌 상자를 선택해 주세요.");
      return;
    }
    const text = selected.corrected_text.trim();
    const splitAt =
      text.indexOf("\n") >= 0 ? text.indexOf("\n") : Math.ceil(text.length / 2);
    if (splitAt <= 0 || splitAt >= text.length) {
      setFeedback(
        "두 부분으로 나눌 글자가 부족합니다. 두 글자 이상인 상자를 선택해 주세요.",
      );
      return;
    }
    const first = text.slice(0, splitAt).trim();
    const second = text.slice(splitAt).trim();
    if (!first || !second) {
      setFeedback("빈 글자 영역이 생기지 않도록 나눌 위치를 확인해 주세요.");
      return;
    }
    const canvas = overlayCanvasRef.current;
    if (!canvas) {
      setFeedback("사진 화면을 불러온 뒤 다시 시도해 주세요.");
      return;
    }
    const splitBoxes = splitRotatedBox(selected.bbox, canvas);
    if (!splitBoxes) {
      setFeedback(
        "사진 가장자리에서는 상자를 나눌 수 없습니다. 상자를 안쪽으로 조금 옮겨 주세요.",
      );
      return;
    }
    const [firstBox, secondBox] = splitBoxes;
    const secondId = nextClientId();
    rememberSnapshot();
    updateRegion(selected.client_id, {
      corrected_text: first,
      bbox: firstBox,
      placement_status: "confirmed",
    });
    setRegions((current) => [
      ...current,
      {
        ...selected,
        client_id: secondId,
        corrected_text: second,
        bbox: secondBox,
        source: "manual",
        placement_status: "confirmed",
      },
    ]);
    setSelectedId(secondId);
    setSelectedIds([secondId]);
    setFeedback(
      Math.abs(rotationDegrees(selected.bbox)) > 0.05
        ? "기울기를 유지한 채 상자를 두 개로 나눴습니다."
        : "상자를 두 개로 나눴습니다.",
    );
  }

  function mergeSelected() {
    const items = regions.filter((region) =>
      mergeIds.includes(region.client_id),
    );
    if (items.length < 2) return;
    if (items.some((item) => Math.abs(rotationDegrees(item.bbox)) > 0.05)) {
      setFeedback("기울어진 상자는 0°로 맞춘 뒤 병합해 주세요.");
      return;
    }
    const left = Math.min(...items.map((item) => item.bbox.left));
    const top = Math.min(...items.map((item) => item.bbox.top));
    const right = Math.max(
      ...items.map((item) => item.bbox.left + item.bbox.width),
    );
    const bottom = Math.max(
      ...items.map((item) => item.bbox.top + item.bbox.height),
    );
    rememberSnapshot();
    const merged: CoordinateRegion = {
      ...items[0],
      client_id: nextClientId(),
      bbox: {
        left,
        top,
        width: right - left,
        height: bottom - top,
        rotation_degrees: 0,
      },
      raw_text: items
        .map((item) => item.raw_text)
        .filter(Boolean)
        .join(" "),
      corrected_text: items
        .map((item) => item.corrected_text)
        .filter(Boolean)
        .join(" "),
      source: "manual",
      placement_status: "confirmed",
      position_confidence: 1,
    };
    setRegions((current) => [
      ...current.filter((region) => !mergeIds.includes(region.client_id)),
      merged,
    ]);
    setSelectedId(merged.client_id);
    setSelectedIds([merged.client_id]);
    setMergeIds([]);
    setTool("select");
    setFeedback("선택한 상자를 하나로 합쳤습니다.");
  }

  function setSelectedRotation(nextRotation: number) {
    if (!selected || !overlayCanvasRef.current) return;
    const rotationIds = selectedIds.includes(selected.client_id)
      ? selectedIds
      : [selected.client_id];
    const originals = regions
      .filter((region) => rotationIds.includes(region.client_id))
      .map((region) => ({ id: region.client_id, bbox: { ...region.bbox } }));
    const desiredDelta = nextRotation - rotationDegrees(selected.bbox);
    const safeDelta = safeSharedRotationDelta(
      originals,
      desiredDelta,
      overlayCanvasRef.current,
    );
    if (Math.abs(safeDelta) < 0.05) {
      setFeedback("선택한 상자가 사진 밖으로 나가므로 더 기울일 수 없습니다.");
      return;
    }
    rememberSnapshot();
    const nextById = new Map(
      originals.map(({ id, bbox }) => [
        id,
        {
          ...bbox,
          rotation_degrees: roundedRotation(rotationDegrees(bbox) + safeDelta),
        },
      ]),
    );
    setRegions((current) =>
      current.map((region) => {
        const bbox = nextById.get(region.client_id);
        return bbox
          ? {
              ...region,
              bbox,
              placement_status: "confirmed",
              position_confidence: 1,
              source: "manual",
            }
          : region;
      }),
    );
    setFeedback(`${originals.length}개 상자의 기울기를 함께 조정했습니다.`);
  }

  function deleteSelected() {
    if (!selected) return;
    if (regions.length <= 1) {
      setFeedback(
        "마지막 상자는 삭제할 수 없습니다. 글자를 수정하거나 위치를 조정해 주세요.",
      );
      return;
    }
    const currentIndex = regions.findIndex(
      (region) => region.client_id === selected.client_id,
    );
    rememberSnapshot();
    const remaining = regions.filter(
      (region) => region.client_id !== selected.client_id,
    );
    setRegions(remaining);
    setSelectedId(
      remaining[Math.min(currentIndex, remaining.length - 1)]?.client_id ??
        null,
    );
    setSelectedIds(
      remaining[Math.min(currentIndex, remaining.length - 1)]?.client_id
        ? [remaining[Math.min(currentIndex, remaining.length - 1)].client_id]
        : [],
    );
    setMergeIds((current) => current.filter((id) => id !== selected.client_id));
    setSelectedIds((current) =>
      current.filter((id) => id !== selected.client_id),
    );
    setFeedback(
      "선택한 상자를 삭제했습니다. 잘못 지웠다면 되돌리기를 누르세요.",
    );
  }

  async function save() {
    if (!review || !imageSize.width || !imageSize.height || !regions.length)
      return;
    setSaving(true);
    setFeedback("");
    try {
      const next = await apiFetch<AttachmentCoordinateReview>(
        `/api/attachments/${attachment.id}/coordinate-review`,
        {
          method: "POST",
          body: JSON.stringify({
            image_width: imageSize.width,
            image_height: imageSize.height,
            editor_version: EDITOR_VERSION,
            document_template: review.document_template,
            regions,
            confirm_text: true,
          }),
        },
      );
      setReview(next);
      setRegions(next.regions);
      setSelectedIds((current) =>
        current.filter((id) =>
          next.regions.some((region) => region.client_id === id),
        ),
      );
      undoStackRef.current = [];
      setUndoCount(0);
      onSaved?.(next);
      setFeedback(
        `수정 내용과 위치 기록 ${next.version_number}판을 확정 이력으로 저장했습니다.`,
      );
    } catch (reason) {
      setFeedback(
        reason instanceof Error
          ? reason.message
          : "수정 내용을 저장하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  function imageLayer(className: string, overlay: boolean) {
    return (
      <div
        className={`coordinate-canvas ${className}${overlay && showOverlay ? " show-overlay" : ""}`}
        style={{ width: `${scale * 100}%` }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={imageUrl}
          alt={attachment.original_name}
          onLoad={(event) =>
            setImageSize({
              width: event.currentTarget.naturalWidth,
              height: event.currentTarget.naturalHeight,
            })
          }
        />
        {overlay && showOverlay ? (
          <div
            ref={overlayCanvasRef}
            className={`coordinate-overlay tool-${tool}`}
            onPointerDown={startCanvasGesture}
            onPointerMove={moveGesture}
            onPointerUp={endGesture}
            onPointerCancel={cancelGesture}
          >
            {placedRegions.map((region) => {
              const active = region.client_id === selectedId;
              const groupSelected = selectedIds.includes(region.client_id);
              const merging = mergeIds.includes(region.client_id);
              return (
                <button
                  key={region.client_id}
                  ref={(element) => {
                    if (element)
                      regionElementRefs.current.set(region.client_id, element);
                    else regionElementRefs.current.delete(region.client_id);
                  }}
                  type="button"
                  className={`coordinate-region role-${region.role}${region.review_required ? " uncertain" : ""}${active ? " active" : ""}${groupSelected ? " group-selected" : ""}${merging ? " merging" : ""}`}
                  style={boxStyle(region.bbox)}
                  onPointerDown={(event) => startRegionGesture(event, region)}
                  onClick={(event) => {
                    if (tool === "select" && !event.ctrlKey && !event.metaKey) {
                      revealRegion(region.client_id, true);
                    }
                  }}
                  title={
                    region.raw_text
                      ? `최초 OCR: ${region.raw_text}`
                      : "새 글자 영역"
                  }
                >
                  <span>{region.corrected_text || "글자 입력"}</span>
                  {active ? (
                    <>
                      <span
                        className="coordinate-rotation-handle"
                        role="presentation"
                        title="드래그하여 상자 기울이기"
                        onPointerDown={startRotate}
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                        }}
                      >
                        기울기
                      </span>
                      <span
                        className="coordinate-resize-handle"
                        role="presentation"
                        title="드래그하여 상자 크기 조정"
                        onPointerDown={startResize}
                        onClick={(event) => {
                          event.preventDefault();
                          event.stopPropagation();
                        }}
                      />
                    </>
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <section
      className="coordinate-editor"
      role="dialog"
      aria-modal="true"
      aria-label="좌표 판독문 편집"
      onPointerDownCapture={(event) => {
        const menu = toolMenuRef.current;
        if (toolMenuOpen && menu && !menu.contains(event.target as Node)) {
          setToolMenuOpen(false);
        }
      }}
      onClickCapture={(event) => {
        const menu = toolMenuRef.current;
        if (toolMenuOpen && menu && !menu.contains(event.target as Node)) {
          setToolMenuOpen(false);
        }
      }}
      onKeyDownCapture={(event) => {
        if (event.key === "Escape" && toolMenuOpen) {
          event.preventDefault();
          event.stopPropagation();
          setToolMenuOpen(false);
        }
      }}
    >
      <header className="coordinate-editor-header">
        <div>
          <strong>이미지 위에서 판독문 수정</strong>
          <span>{attachment.original_name}</span>
        </div>
        <div className="coordinate-editor-zoom">
          <button
            type="button"
            onClick={() => setScale((value) => Math.max(1, value - 0.25))}
          >
            −
          </button>
          <span>{Math.round(scale * 100)}%</span>
          <button
            type="button"
            onClick={() => setScale((value) => Math.min(4, value + 0.25))}
          >
            +
          </button>
          <button
            type="button"
            className="coordinate-reset-zoom"
            onClick={() => setScale(1)}
          >
            원본
          </button>
          <button
            type="button"
            className="coordinate-editor-close"
            aria-label="좌표 편집 닫기"
            onClick={onClose}
          >
            ×
          </button>
        </div>
      </header>

      <nav className="coordinate-editor-tools" aria-label="좌표 편집 도구">
        <button
          type="button"
          className={tool === "add" ? "active" : ""}
          onClick={() => {
            setTool(tool === "add" ? "select" : "add");
            setMergeIds([]);
          }}
        >
          + 상자 추가
        </button>
        <button type="button" onClick={undoLastChange} disabled={!undoCount}>
          되돌리기
        </button>
        <div
          ref={toolMenuRef}
          className="coordinate-tool-menu"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget))
              setToolMenuOpen(false);
          }}
        >
          <button
            type="button"
            className="coordinate-tool-menu-toggle"
            aria-expanded={toolMenuOpen}
            onClick={(event) => {
              event.preventDefault();
              setToolMenuOpen((value) => !value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                setToolMenuOpen(false);
              }
            }}
          >
            상자 정리
          </button>
          {toolMenuOpen ? (
            <div>
              <button
                type="button"
                onClick={() => {
                  splitSelected();
                  setToolMenuOpen(false);
                }}
                disabled={!selected}
              >
                선택 상자 나누기
              </button>
              <button
                type="button"
                className={tool === "merge" ? "active" : ""}
                onClick={() => {
                  setTool(tool === "merge" ? "select" : "merge");
                  setMergeIds([]);
                  setToolMenuOpen(false);
                }}
              >
                {tool === "merge" ? "병합 선택 취소" : "병합할 상자 선택"}
              </button>
              {tool === "merge" ? (
                <button
                  type="button"
                  onClick={() => {
                    mergeSelected();
                    setToolMenuOpen(false);
                  }}
                  disabled={mergeIds.length < 2}
                >
                  선택 {mergeIds.length}개 병합
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          className="mobile-overlay-toggle"
          onClick={() => setShowOverlay((value) => !value)}
        >
          {showOverlay ? "원본 보기" : "글자 겹쳐 보기"}
        </button>
      </nav>

      <div className="coordinate-editor-main">
        <section
          className={`coordinate-editor-pane original-pane${showOverlay ? " mobile-hidden" : ""}`}
        >
          <strong>원본</strong>
          <div
            ref={originalScrollRef}
            className="coordinate-scroll"
            onScroll={(event) =>
              synchronizeScroll(event.currentTarget, overlayScrollRef.current)
            }
          >
            {imageLayer("original-canvas", false)}
          </div>
        </section>
        <section
          className={`coordinate-editor-pane overlay-pane${showOverlay ? "" : " mobile-hidden"}`}
        >
          <div className="coordinate-pane-heading">
            <strong>글자 겹쳐 보기</strong>
            <span>사진의 글자를 누르면 아래에서 바로 고칠 수 있습니다.</span>
          </div>
          <div
            ref={overlayScrollRef}
            className="coordinate-scroll"
            onScroll={(event) =>
              synchronizeScroll(event.currentTarget, originalScrollRef.current)
            }
          >
            {imageLayer("overlay-canvas", true)}
          </div>
        </section>
      </div>

      <section
        className="coordinate-quick-editor"
        aria-label="선택한 글자 수정"
      >
        {selected ? (
          <>
            <div className="coordinate-selected-meta">
              <strong>
                {selectedIndex + 1} / {regions.length}
              </strong>
              <span>
                {selectedIds.length > 1
                  ? `${selectedIds.length}개 함께 이동·기울이기 · Ctrl+클릭으로 추가/해제`
                  : selected.review_required
                    ? "확인 필요 · 맞으면 Enter"
                    : selected.placement_status === "needs_position"
                      ? "사진 위치가 아직 없습니다."
                      : "Ctrl+클릭으로 여러 상자 선택"}
              </span>
            </div>
            <label>
              <span>처음 읽은 글자</span>
              <output>{selected.raw_text || "읽지 못함"}</output>
            </label>
            <label className="coordinate-corrected-field">
              <span>올바른 글자</span>
              <textarea
                ref={selectedEditorRef}
                value={selected.corrected_text}
                rows={2}
                onChange={(event) =>
                  updateRegion(selected.client_id, {
                    corrected_text: event.target.value,
                  })
                }
                onKeyDown={(event) => {
                  if (
                    event.key !== "Enter" ||
                    event.shiftKey ||
                    event.nativeEvent.isComposing
                  )
                    return;
                  event.preventDefault();
                  applyAndGoNext(selected.client_id);
                }}
                aria-label="선택한 올바른 글자"
              />
              <small>
                Enter를 누르면 원문 위에 반영하고 다음 항목으로 이동합니다.
              </small>
            </label>
            <div className="coordinate-box-actions">
              <span>상자 기울기</span>
              <div className="coordinate-rotation-controls">
                <button
                  type="button"
                  aria-label="상자를 왼쪽으로 1도 기울이기"
                  onClick={() =>
                    setSelectedRotation(
                      rotationDegrees(selected.bbox) - ROTATION_STEP_DEGREES,
                    )
                  }
                >
                  −
                </button>
                <output aria-label="현재 상자 기울기">
                  {rotationDegrees(selected.bbox).toFixed(1)}°
                </output>
                <button
                  type="button"
                  aria-label="상자를 오른쪽으로 1도 기울이기"
                  onClick={() =>
                    setSelectedRotation(
                      rotationDegrees(selected.bbox) + ROTATION_STEP_DEGREES,
                    )
                  }
                >
                  +
                </button>
                <button type="button" onClick={() => setSelectedRotation(0)}>
                  0°
                </button>
              </div>
              {selected.placement_status === "needs_position" ? (
                <button
                  type="button"
                  className={tool === "place" ? "active" : ""}
                  onClick={() => {
                    setTool("place");
                    setFeedback(
                      "오른쪽 사진에서 이 글자가 있는 부분을 드래그해 주세요.",
                    );
                  }}
                >
                  {tool === "place"
                    ? "사진에서 드래그하세요"
                    : "사진에서 위치 지정"}
                </button>
              ) : null}
              <button
                type="button"
                className="coordinate-delete"
                onClick={deleteSelected}
                disabled={regions.length <= 1}
                title={
                  regions.length <= 1
                    ? "마지막 상자는 삭제할 수 없습니다."
                    : "선택한 상자 삭제"
                }
              >
                상자 삭제
              </button>
              <small>글줄의 처음부터 끝까지 상자 안에 넣어 주세요.</small>
            </div>
          </>
        ) : (
          <p>사진 위의 글자를 눌러 수정해 주세요.</p>
        )}
      </section>

      {unplacedRegions.length ? (
        <details className="coordinate-unplaced" open>
          <summary>위치 미확인 글자 {unplacedRegions.length}개</summary>
          <p>
            사진 위에 억지로 올리지 않았습니다. 글자를 고른 뒤 위치를 한 번만
            지정해 주세요.
          </p>
          <div>
            {unplacedRegions.map((region) => (
              <button
                key={region.client_id}
                type="button"
                className={region.client_id === selectedId ? "active" : ""}
                onClick={() => revealRegion(region.client_id, true)}
              >
                {region.corrected_text || region.raw_text || "읽지 못한 글자"}
              </button>
            ))}
          </div>
        </details>
      ) : null}

      <footer className="coordinate-editor-footer">
        <p role="status">
          {feedback ||
            (review?.is_bootstrap
              ? `실제 위치 ${placedRegions.length}개 · 위치 미확인 ${unplacedRegions.length}개`
              : `저장된 좌표 기록 ${review?.version_number ?? 0}판`)}
        </p>
        <div>
          <button
            type="button"
            className="button button-secondary"
            onClick={onClose}
          >
            닫기
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={loading || autoLocating || saving || !regions.length}
            onClick={() => void save()}
          >
            {autoLocating
              ? "위치 찾는 중…"
              : saving
                ? "저장 중…"
                : "수정 내용 저장"}
          </button>
        </div>
      </footer>
    </section>
  );
}
