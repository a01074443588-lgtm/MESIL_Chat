const DESCRIPTION =
  "사진·판독문에서 이름 후보가 발견된 메시지입니다. 올바른 어르신을 확인해 주세요.";

export function residentConfirmationPresentation({ data, error }) {
  if (error) {
    return {
      status: "error",
      message: "확인 대기 목록을 불러오지 못했습니다.",
      retryLabel: "다시 시도",
    };
  }
  if (!data || data.count < 1) return { status: "hidden" };
  return {
    status: "ready",
    label: `어르신 연결 확인 ${data.count}건`,
    description: DESCRIPTION,
  };
}
