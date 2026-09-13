export function defaultAssessmentChatPeriod(input: {
  endDate: string;
  previousBasisDate?: string;
  previousBasisDates?: string[];
}): { startDate: string; endDate: string; source: "previous_basis" | "six_month_fallback" };
