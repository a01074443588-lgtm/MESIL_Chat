const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function parseDate(value) {
  if (typeof value !== "string" || !DATE_PATTERN.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString().slice(0, 10) === value ? parsed : null;
}

function formatDate(value) {
  return value.toISOString().slice(0, 10);
}

function addDays(value, days) {
  const next = new Date(value.getTime());
  next.setUTCDate(next.getUTCDate() + days);
  return next;
}

function subtractMonthsClamped(value, months) {
  const targetMonthIndex = value.getUTCFullYear() * 12 + value.getUTCMonth() - months;
  const targetYear = Math.floor(targetMonthIndex / 12);
  const targetMonth = targetMonthIndex - targetYear * 12;
  const lastDay = new Date(Date.UTC(targetYear, targetMonth + 1, 0)).getUTCDate();
  return new Date(
    Date.UTC(targetYear, targetMonth, Math.min(value.getUTCDate(), lastDay)),
  );
}

export function defaultAssessmentChatPeriod({
  endDate,
  previousBasisDate,
  previousBasisDates = [],
}) {
  const parsedEnd = parseDate(endDate);
  if (!parsedEnd) {
    throw new Error("매실챗 확인 종료일이 올바르지 않습니다.");
  }
  const parsedPrevious = [
    previousBasisDate,
    ...(Array.isArray(previousBasisDates) ? previousBasisDates : []),
  ]
    .map(parseDate)
    .filter((value) => value && value <= parsedEnd)
    .sort((left, right) => left.getTime() - right.getTime())[0] ?? null;
  if (parsedPrevious && parsedPrevious <= parsedEnd) {
    const nextDay = addDays(parsedPrevious, 1);
    return {
      startDate: formatDate(nextDay > parsedEnd ? parsedEnd : nextDay),
      endDate: formatDate(parsedEnd),
      source: "previous_basis",
    };
  }
  return {
    startDate: formatDate(subtractMonthsClamped(parsedEnd, 6)),
    endDate: formatDate(parsedEnd),
    source: "six_month_fallback",
  };
}
