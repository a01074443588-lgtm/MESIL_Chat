"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import type { Resident } from "../types";

type ServiceFilter = "all" | "facility" | "daycare" | "homecare";

const serviceFilters: Array<{ value: ServiceFilter; label: string }> = [
  { value: "all", label: "전체" },
  { value: "facility", label: "시설" },
  { value: "daycare", label: "주간보호" },
  { value: "homecare", label: "방문요양" },
];

function residentLabel(resident: Resident) {
  if (resident.service_type === "facility") {
    return ["시설", resident.room_name, resident.display_name]
      .filter(Boolean)
      .join(" ");
  }
  if (resident.service_type === "daycare") {
    return `주간보호 ${resident.display_name}`;
  }
  if (resident.service_type === "homecare") {
    return `방문요양 ${resident.display_name}`;
  }
  return resident.display_name;
}

type ResidentPickerDialogProps = {
  open: boolean;
  residents: Resident[];
  selectedIds: string[];
  onApply: (residentIds: string[]) => void;
  onClose: () => void;
};

export function ResidentPickerDialog({
  open,
  residents,
  selectedIds,
  onApply,
  onClose,
}: ResidentPickerDialogProps) {
  const [draftIds, setDraftIds] = useState<string[]>(selectedIds);
  const [query, setQuery] = useState("");
  const [serviceFilter, setServiceFilter] = useState<ServiceFilter>("all");

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      onClose();
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, open]);

  const filteredResidents = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("ko-KR");
    return residents.filter((resident) => {
      if (
        serviceFilter !== "all" &&
        resident.service_type !== serviceFilter
      ) {
        return false;
      }
      if (!normalizedQuery) return true;
      return residentLabel(resident)
        .toLocaleLowerCase("ko-KR")
        .includes(normalizedQuery);
    });
  }, [query, residents, serviceFilter]);

  if (!open || typeof document === "undefined") return null;

  function requestClose() {
    onClose();
  }

  function finishSelection() {
    onApply(draftIds);
    onClose();
  }

  function toggleResident(residentId: string) {
    setDraftIds((current) =>
      current.includes(residentId)
        ? current.filter((id) => id !== residentId)
        : [...current, residentId],
    );
  }

  return createPortal(
    <div className="resident-picker-backdrop" role="presentation">
      <section
        className="resident-picker-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="resident-picker-title"
      >
        <header className="resident-picker-header">
          <div>
            <strong id="resident-picker-title">어르신 선택</strong>
            <span>선택하지 않아도 보고 내용에서 이름을 찾습니다.</span>
          </div>
          <button type="button" aria-label="어르신 선택 닫기" onClick={requestClose}>
            ×
          </button>
        </header>

        <div className="resident-picker-search">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="이름 검색"
            aria-label="어르신 이름 검색"
            autoFocus
          />
          <div className="resident-picker-filters" aria-label="서비스 구분">
            {serviceFilters.map((item) => (
              <button
                key={item.value}
                type="button"
                className={serviceFilter === item.value ? "active" : ""}
                aria-pressed={serviceFilter === item.value}
                onClick={() => setServiceFilter(item.value)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        <div className="resident-picker-list">
          {filteredResidents.length ? (
            filteredResidents.map((resident) => {
              const selected = draftIds.includes(resident.id);
              return (
                <label
                  key={resident.id}
                  className={`resident-picker-option${selected ? " selected" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() => toggleResident(resident.id)}
                  />
                  <span>{residentLabel(resident)}</span>
                </label>
              );
            })
          ) : (
            <p className="resident-picker-empty">검색 결과가 없습니다.</p>
          )}
        </div>

        <footer className="resident-picker-footer">
          <button
            type="button"
            className="button button-secondary"
            disabled={draftIds.length === 0}
            onClick={() => setDraftIds([])}
          >
            선택 해제
          </button>
          <button
            type="button"
            className="button button-primary"
            onClick={finishSelection}
          >
            {draftIds.length ? `${draftIds.length}명 선택 완료` : "선택 없이 닫기"}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
