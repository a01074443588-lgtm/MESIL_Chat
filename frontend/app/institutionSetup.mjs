export function institutionSetupState({
  checked,
  isAdmin,
  isReviewerSession,
  isCodedSynthetic = false,
  units,
}) {
  const activeLivingSpaceCount = units.filter(
    (unit) => unit.unit_type === "floor" && unit.is_active,
  ).length;

  return {
    activeLivingSpaceCount,
    required:
      checked &&
      isAdmin &&
      !isReviewerSession &&
      !isCodedSynthetic &&
      activeLivingSpaceCount === 0,
  };
}
