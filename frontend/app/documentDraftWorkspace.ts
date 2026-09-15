import { apiBase } from "./api";
import type {
  CareBriefingCard,
  DailyDocumentType,
  PeriodDocumentDraft,
  PeriodWorkdeskSource,
} from "./types";

export const periodDocumentLabels: Record<DailyDocumentType, string> = {
  care_service_record: "급여제공기록지",
  nursing_log: "간호일지",
  consultation_log: "상담일지",
  physical_restraint_log: "신체제재 기록지",
  program_log: "프로그램 운영기록지",
};

type DocumentDraftWorkspaceInput = {
  card: CareBriefingCard;
  drafts: PeriodDocumentDraft[];
  sources: PeriodWorkdeskSource[];
  periodLabel: string;
  onOpenSource: (roomId: string, messageId: string) => void | Promise<void>;
};

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function appendTextElement(
  documentRef: Document,
  parent: HTMLElement,
  tagName: keyof HTMLElementTagNameMap,
  value: string,
  className?: string,
) {
  const element = documentRef.createElement(tagName);
  element.textContent = value;
  if (className) element.className = className;
  parent.appendChild(element);
  return element;
}

function uniqueValues(values: string[]) {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

export function openDocumentDraftWorkspace({
  card,
  drafts,
  sources,
  periodLabel,
  onOpenSource,
}: DocumentDraftWorkspaceInput) {
  if (!card.event_group_id || drafts.length === 0) {
    throw new Error("이 사건에 연결된 기록·서류 초안이 없습니다.");
  }
  if (drafts.some((draft) => draft.event_group_id !== card.event_group_id)) {
    throw new Error("사건과 서류 초안의 근거 범위가 일치하지 않습니다.");
  }

  const draftWindow = window.open(
    "",
    "_blank",
    "popup,width=1080,height=900,resizable=yes,scrollbars=yes",
  );
  if (!draftWindow) {
    throw new Error("새 창이 차단되었습니다. 팝업을 허용한 뒤 다시 시도해 주세요.");
  }

  const draftDocument = draftWindow.document;
  draftDocument.open();
  draftDocument.write(`<!doctype html>
    <html lang="ko">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>기록·서류 초안 만들기</title>
        <style>
          :root { color-scheme: light; font-family: "Malgun Gothic", "Noto Sans KR", sans-serif; color: #173447; background: #edf3f1; }
          * { box-sizing: border-box; }
          body { margin: 0; background: #edf3f1; }
          button, textarea, input { font: inherit; }
          main { width: min(1040px, 100%); margin: 0 auto; padding: 24px; }
          .panel { border: 1px solid #c8d7d3; border-radius: 14px; background: #fff; box-shadow: 0 10px 28px rgba(18, 52, 71, .08); margin-bottom: 16px; padding: 20px; }
          h1, h2, h3, p { margin-top: 0; }
          h1 { margin-bottom: 8px; font-size: 25px; }
          h2 { font-size: 18px; }
          h3 { font-size: 15px; }
          p, li, label, small { line-height: 1.65; }
          .eyebrow { color: #5a7f10; font-size: 12px; font-weight: 900; letter-spacing: .04em; }
          .summary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
          .summary-grid > div { border-radius: 10px; background: #f3f7f5; padding: 12px; }
          .summary-grid strong { display: block; color: #456009; font-size: 12px; margin-bottom: 5px; }
          .warning { border-color: #dfb66f; background: #fff8e9; }
          .warning strong { color: #9b5a00; }
          .status { border-radius: 10px; background: #f9e7df; color: #8e3a1e; font-weight: 900; padding: 12px; }
          .status.checked { background: #e8f4e4; color: #3d6506; }
          .draft-card { border-top: 1px solid #d9e3e0; margin-top: 18px; padding-top: 18px; }
          .field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }
          .field-grid > div { border: 1px solid #d9e3e0; border-radius: 9px; padding: 10px; }
          .field-grid strong { display: block; color: #456009; font-size: 12px; margin-bottom: 4px; }
          .needs-confirmation { border-radius: 9px; background: #fff8e9; color: #7f4d05; padding: 10px; }
          .needs-confirmation ul { margin-bottom: 0; }
          textarea { width: 100%; min-height: 270px; resize: vertical; border: 1px solid #aebfba; border-radius: 10px; background: #fbfdfc; color: #173447; line-height: 1.7; padding: 14px; }
          .print-content { display: none; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
          .check-list { display: grid; gap: 10px; }
          .check-list label { display: grid; grid-template-columns: 22px 1fr; align-items: start; gap: 7px; border-radius: 9px; background: #f3f7f5; padding: 10px; }
          .check-list input { width: 18px; height: 18px; margin-top: 2px; accent-color: #5a7f10; }
          .actions { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 15px; }
          button, .file-link { min-height: 42px; border: 1px solid #5a7f10; border-radius: 9px; background: #fff; color: #456009; cursor: pointer; font-weight: 900; padding: 9px 14px; text-decoration: none; }
          button.primary { background: #4d700e; color: #fff; }
          button:disabled { cursor: not-allowed; opacity: .45; }
          details.evidence-panel { padding: 0; }
          details.evidence-panel > summary { cursor: pointer; font-size: 18px; font-weight: 900; list-style-position: inside; padding: 20px; }
          details.evidence-panel[open] > summary { border-bottom: 1px solid #d9e3e0; }
          .evidence-content { padding: 0 20px 20px; }
          .evidence { border-top: 1px solid #d9e3e0; margin-top: 14px; padding-top: 14px; }
          .evidence-meta { color: #5c6f77; font-size: 11px; }
          .reply { border-left: 3px solid #b7c9c4; margin-top: 8px; padding-left: 10px; }
          .reply p { margin-bottom: 0; }
          .attachments { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
          .attachments img { display: block; width: 180px; height: 130px; border: 1px solid #c8d7d3; border-radius: 9px; object-fit: cover; }
          .file-link { display: inline-flex; align-items: center; max-width: 100%; overflow-wrap: anywhere; }
          .extraction-evidence { width: 100%; border-left: 3px solid #b7c9c4; background: #f7faf8; padding: 10px; }
          .extraction-evidence strong { display: block; margin-bottom: 6px; }
          .extraction-evidence p { margin-bottom: 7px; white-space: pre-wrap; overflow-wrap: anywhere; }
          .extraction-evidence p:last-child { margin-bottom: 0; }
          .privacy-note { color: #5c6f77; font-size: 11px; }
          @media (max-width: 700px) { main { padding: 12px; } .summary-grid, .field-grid { grid-template-columns: 1fr; } }
          @media print {
            @page { size: A4; margin: 15mm; }
            body, :root { background: #fff; }
            main { width: 100%; padding: 0; }
            .panel { break-inside: avoid; border-color: #aebfba; box-shadow: none; }
            .draft-card { break-inside: auto; }
            textarea, .actions, .open-source, .attachments { display: none !important; }
            .print-content { display: block; }
          }
        </style>
      </head>
      <body><main id="draft-root"></main></body>
    </html>`);
  draftDocument.close();

  const root = draftDocument.getElementById("draft-root");
  if (!root) {
    draftWindow.close();
    throw new Error("서류 초안 화면을 준비하지 못했습니다.");
  }

  const headingPanel = draftDocument.createElement("section");
  headingPanel.className = "panel";
  appendTextElement(draftDocument, headingPanel, "p", "AI 돌봄 브리핑", "eyebrow");
  appendTextElement(draftDocument, headingPanel, "h1", "기록·서류 초안 만들기");
  appendTextElement(
    draftDocument,
    headingPanel,
    "p",
    "DEV 비공식 자료 · 코드화된 합성 시험자료",
    "privacy-note",
  );
  appendTextElement(
    draftDocument,
    headingPanel,
    "p",
    "AI가 이 사건을 기록·서류 후보로 판단해 만든 편집용 초안입니다.",
  );
  const summaryGrid = draftDocument.createElement("div");
  summaryGrid.className = "summary-grid";
  const summaryItems = [
    ["대상자", card.resident_name],
    ["무슨 일", card.change_summary],
    ["현재 최종상태", card.final_status_summary],
    [
      "발생·갱신",
      `${formatDateTime(card.occurred_at)} · 최근 ${formatDateTime(card.latest_at)}`,
    ],
    ["분석 범위", periodLabel],
    ["AI가 중요하게 본 이유", card.check_reasons.join(" · ")],
  ];
  for (const [label, value] of summaryItems) {
    const item = draftDocument.createElement("div");
    appendTextElement(draftDocument, item, "strong", label);
    appendTextElement(draftDocument, item, "span", value || "확인 필요");
    summaryGrid.appendChild(item);
  }
  headingPanel.appendChild(summaryGrid);
  root.appendChild(headingPanel);

  const warningPanel = draftDocument.createElement("section");
  warningPanel.className = "panel warning";
  appendTextElement(draftDocument, warningPanel, "strong", "직원 확인 전 AI 초안");
  appendTextElement(
    draftDocument,
    warningPanel,
    "p",
    "이 화면은 공식 기록 저장이나 SMCODI 전송을 하지 않습니다. 원문 대화·답글·사진과 이름, 날짜, 수치, 신체 부위, 조치를 대조한 뒤 인쇄 또는 PDF로 저장하세요. 담당자와 완료 여부가 근거에 없으면 추측하지 말고 ‘확인 필요’로 남겨야 합니다.",
  );
  appendTextElement(
    draftDocument,
    warningPanel,
    "p",
    "창을 닫으면 편집 내용은 보존되지 않습니다.",
    "privacy-note",
  );
  root.appendChild(warningPanel);

  const draftPanel = draftDocument.createElement("section");
  draftPanel.className = "panel";
  appendTextElement(draftDocument, draftPanel, "h2", "AI 서류 초안");
  for (const draft of drafts) {
    const cardElement = draftDocument.createElement("article");
    cardElement.className = "draft-card";
    appendTextElement(
      draftDocument,
      cardElement,
      "h3",
      periodDocumentLabels[draft.document_type],
    );
    appendTextElement(
      draftDocument,
      cardElement,
      "p",
      "사건 발생 즉시 만든 편집용 초안 · 공식 기록 저장 전 직원 확인 필요",
      "privacy-note",
    );
    const fieldGrid = draftDocument.createElement("div");
    fieldGrid.className = "field-grid";
    for (const [fieldName, fieldValue] of Object.entries(draft.draft_fields)) {
      const field = draftDocument.createElement("div");
      appendTextElement(draftDocument, field, "strong", fieldName);
      appendTextElement(draftDocument, field, "span", fieldValue);
      fieldGrid.appendChild(field);
    }
    cardElement.appendChild(fieldGrid);
    const confirmationBox = draftDocument.createElement("div");
    confirmationBox.className = "needs-confirmation";
    appendTextElement(
      draftDocument,
      confirmationBox,
      "strong",
      `확인 필요 ${draft.missing_fields.length}개`,
    );
    if (draft.missing_fields.length) {
      const missingList = draftDocument.createElement("ul");
      for (const missingField of draft.missing_fields) {
        appendTextElement(draftDocument, missingList, "li", missingField);
      }
      confirmationBox.appendChild(missingList);
    } else {
      appendTextElement(
        draftDocument,
        confirmationBox,
        "p",
        "자동으로 비어 있는 필드는 없지만 아래 사람 확인 항목은 반드시 대조합니다.",
      );
    }
    const humanList = draftDocument.createElement("ul");
    for (const humanField of draft.human_verification_fields) {
      appendTextElement(draftDocument, humanList, "li", humanField);
    }
    confirmationBox.appendChild(humanList);
    cardElement.appendChild(confirmationBox);
    const editor = draftDocument.createElement("textarea");
    editor.value = draft.content;
    editor.setAttribute(
      "aria-label",
      `${periodDocumentLabels[draft.document_type]} AI 초안 편집`,
    );
    const printContent = draftDocument.createElement("div");
    printContent.className = "print-content";
    printContent.textContent = draft.content;
    editor.addEventListener("input", () => {
      printContent.textContent = editor.value;
    });
    const draftActions = draftDocument.createElement("div");
    draftActions.className = "actions";
    const copyButton = draftDocument.createElement("button");
    copyButton.type = "button";
    copyButton.textContent = "초안 문안 전체 선택";
    copyButton.addEventListener("click", () => {
      editor.focus();
      editor.select();
      copyButton.textContent = "문안 선택됨 · Ctrl+C로 복사해 주세요";
    });
    draftActions.appendChild(copyButton);
    cardElement.append(editor, printContent, draftActions);
    draftPanel.appendChild(cardElement);
  }
  root.appendChild(draftPanel);

  const verificationPanel = draftDocument.createElement("section");
  verificationPanel.className = "panel";
  appendTextElement(draftDocument, verificationPanel, "h2", "직원 근거 대조");
  const reviewStatus = appendTextElement(
    draftDocument,
    verificationPanel,
    "p",
    "직원 확인 전 · 인쇄/PDF 버튼이 잠겨 있습니다.",
    "status",
  );
  const verificationItems = uniqueValues([
    ...drafts.flatMap((draft) => draft.verification_questions),
    "근거 원문·답글·사진과 이름, 날짜, 수치, 신체 부위, 조치를 대조했습니다.",
    "담당자·완료 여부가 근거에 없으면 ‘확인 필요’로 남겼습니다.",
    "공식 기록으로 사용하기 전에 기관의 기록 기준과 작성자를 확인했습니다.",
  ]);
  const checkList = draftDocument.createElement("div");
  checkList.className = "check-list";
  const checkboxes = verificationItems.map((item, index) => {
    const label = draftDocument.createElement("label");
    const checkbox = draftDocument.createElement("input");
    checkbox.type = "checkbox";
    checkbox.id = `draft-check-${index}`;
    const labelText = draftDocument.createElement("span");
    labelText.textContent = item;
    label.append(checkbox, labelText);
    checkList.appendChild(label);
    return checkbox;
  });
  verificationPanel.appendChild(checkList);
  const actions = draftDocument.createElement("div");
  actions.className = "actions";
  const printButton = draftDocument.createElement("button");
  printButton.type = "button";
  printButton.className = "primary";
  printButton.textContent = "확인 완료 후 인쇄·PDF 저장";
  printButton.disabled = true;
  const updateVerificationState = () => {
    const checked = checkboxes.every((checkbox) => checkbox.checked);
    printButton.disabled = !checked;
    reviewStatus.textContent = checked
      ? "직원 근거 대조 완료 · 인쇄/PDF 저장 가능"
      : "직원 확인 전 · 인쇄/PDF 버튼이 잠겨 있습니다.";
    reviewStatus.classList.toggle("checked", checked);
  };
  for (const checkbox of checkboxes) {
    checkbox.addEventListener("change", updateVerificationState);
  }
  printButton.addEventListener("click", () => draftWindow.print());
  actions.appendChild(printButton);
  verificationPanel.appendChild(actions);
  root.appendChild(verificationPanel);

  const evidencePanel = draftDocument.createElement("details");
  evidencePanel.className = "panel evidence-panel";
  appendTextElement(
    draftDocument,
    evidencePanel,
    "summary",
    `근거 보기 · 원문·답글·사진 ${sources.length}건`,
  );
  const evidenceContent = draftDocument.createElement("div");
  evidenceContent.className = "evidence-content";
  appendTextElement(
    draftDocument,
    evidenceContent,
    "p",
    "초안 본문에서 제외한 원문·답글·첨부도 삭제하지 않았습니다. 필요할 때 펼쳐 원문을 대조하세요.",
  );
  for (const [sourceIndex, source] of sources.entries()) {
    const evidence = draftDocument.createElement("article");
    evidence.className = "evidence";
    appendTextElement(
      draftDocument,
      evidence,
      "h3",
      `근거 ${sourceIndex + 1} · ${source.room_name}`,
    );
    appendTextElement(
      draftDocument,
      evidence,
      "p",
      source.message.body.trim() ||
        `첨부파일 ${source.message.attachments.length}건이 포함된 대화입니다.`,
    );
    appendTextElement(
      draftDocument,
      evidence,
      "p",
      `${source.message.sender_name} · ${formatDateTime(source.message.created_at)} · 답글 ${source.reply_user_count}명`,
      "evidence-meta",
    );

    if (source.message.attachments.length) {
      const attachments = draftDocument.createElement("div");
      attachments.className = "attachments";
      for (const attachment of source.message.attachments) {
        const attachmentUrl = `${apiBase()}/api/workdesk/attachments/${encodeURIComponent(attachment.id)}`;
        const link = draftDocument.createElement("a");
        link.href = attachmentUrl;
        link.target = "_blank";
        link.rel = "noopener";
        link.className = "file-link";
        if (attachment.mime_type.startsWith("image/")) {
          const image = draftDocument.createElement("img");
          image.src = attachmentUrl;
          image.alt = attachment.original_name;
          link.appendChild(image);
        } else {
          link.textContent = `첨부파일 열기 · ${attachment.original_name}`;
        }
        attachments.appendChild(link);
        const extraction = attachment.text_extraction;
        if (extraction) {
          const extractionTexts = uniqueValues([
            extraction.latest_confirmed_text ?? "",
            extraction.reviewed_text ?? "",
            extraction.suggested_text ?? "",
            extraction.extracted_text ?? "",
            extraction.original_extracted_text ?? "",
            extraction.error_message ?? "",
          ]);
          if (extractionTexts.length) {
            const extractionEvidence = draftDocument.createElement("div");
            extractionEvidence.className = "extraction-evidence";
            appendTextElement(
              draftDocument,
              extractionEvidence,
              "strong",
              attachment.mime_type.startsWith("audio/")
                ? "음성 받아쓰기 원문"
                : "이미지 글자 판독 원문",
            );
            for (const extractionText of extractionTexts) {
              appendTextElement(
                draftDocument,
                extractionEvidence,
                "p",
                extractionText,
              );
            }
            attachments.appendChild(extractionEvidence);
          }
        }
      }
      evidence.appendChild(attachments);
    }

    for (const comment of source.comments) {
      const reply = draftDocument.createElement("div");
      reply.className = "reply";
      appendTextElement(
        draftDocument,
        reply,
        "strong",
        `${comment.author_name} · ${formatDateTime(comment.created_at)}`,
      );
      appendTextElement(draftDocument, reply, "p", comment.body);
      evidence.appendChild(reply);
    }

    const openSourceButton = draftDocument.createElement("button");
    openSourceButton.type = "button";
    openSourceButton.className = "open-source";
    openSourceButton.textContent = "대화에서 확인·답글하기";
    openSourceButton.addEventListener("click", () => {
      window.focus();
      void Promise.resolve(
        onOpenSource(source.message.room_id, source.message.id),
      ).catch(() => {
        openSourceButton.textContent = "대화 이동 실패 · 원래 창에서 다시 시도";
      });
    });
    const sourceActions = draftDocument.createElement("div");
    sourceActions.className = "actions";
    sourceActions.appendChild(openSourceButton);
    evidence.appendChild(sourceActions);
    evidenceContent.appendChild(evidence);
  }
  evidencePanel.appendChild(evidenceContent);
  root.appendChild(evidencePanel);

  draftWindow.focus();
}
