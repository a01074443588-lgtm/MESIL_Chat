import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";
import {
  isRecentWakeNotification,
  WAKE_NOTIFICATION_MAX_AGE_MS,
} from "../app/wakeNotification.mjs";
import {
  APP_UPDATE_CHECK_INTERVAL_MS,
  decideAppUpdateAction,
  isNewAppBuild,
  shouldRunAppUpdateCheck,
} from "../app/appUpdatePolicy.mjs";
import {
  isSessionAuthenticationFailure,
  SESSION_RETRY_INTERVAL_MS,
  shouldRetrySessionCheck,
} from "../app/sessionRecovery.mjs";
import * as sessionRecoveryPolicy from "../app/sessionRecovery.mjs";
import {
  shouldReconnectWebSocket,
  WEBSOCKET_RECONNECT_MAX_MS,
  websocketReconnectDelay,
} from "../app/websocketReconnect.mjs";
import { institutionSetupState } from "../app/institutionSetup.mjs";
import { defaultAssessmentChatPeriod } from "../app/assessmentPeriod.mjs";
import { incrementRoomUnreadCount } from "../app/unreadRooms.mjs";
import * as residentConfirmationPolicy from "../app/residentConfirmation.mjs";

test("resident confirmation hides loading and empty states without showing a false zero", () => {
  assert.equal(
    typeof residentConfirmationPolicy.residentConfirmationPresentation,
    "function",
  );
  assert.deepEqual(
    residentConfirmationPolicy.residentConfirmationPresentation({
      data: null,
      error: false,
    }),
    { status: "hidden" },
  );
  assert.deepEqual(
    residentConfirmationPolicy.residentConfirmationPresentation({
      data: { count: 0 },
      error: false,
    }),
    { status: "hidden" },
  );
});

test("resident confirmation labels only a positive inbox and explains its meaning", () => {
  assert.deepEqual(
    residentConfirmationPolicy.residentConfirmationPresentation({
      data: { count: 2 },
      error: false,
    }),
    {
      status: "ready",
      label: "어르신 연결 확인 2건",
      description:
        "사진·판독문에서 이름 후보가 발견된 메시지입니다. 올바른 어르신을 확인해 주세요.",
    },
  );
});

test("resident confirmation exposes a retryable error instead of disguising it as zero", () => {
  assert.deepEqual(
    residentConfirmationPolicy.residentConfirmationPresentation({
      data: { count: 0 },
      error: true,
    }),
    {
      status: "error",
      message: "확인 대기 목록을 불러오지 못했습니다.",
      retryLabel: "다시 시도",
    },
  );
});

test("keeps native call delivery available while the Android WebView is hidden", () => {
  assert.equal(
    typeof sessionRecoveryPolicy.shouldAcknowledgeNativeWebCall,
    "function",
  );
  assert.equal(
    sessionRecoveryPolicy.shouldAcknowledgeNativeWebCall({
      native: true,
      visibilityState: "hidden",
    }),
    false,
  );
  assert.equal(
    sessionRecoveryPolicy.shouldAcknowledgeNativeWebCall({
      native: true,
      visibilityState: "visible",
    }),
    true,
  );
  assert.equal(
    sessionRecoveryPolicy.shouldAcknowledgeNativeWebCall({
      native: false,
      visibilityState: "visible",
    }),
    false,
  );
});

test("retains optional native entry compatibility and prevents a second Android web-app icon", async () => {
  const [
    { isAndroidWebInstallCandidate, parseStaffReleaseMetadata },
    { resolveChatEntryPlatform, platformManifestDisplay },
    component,
    page,
    login,
    chat,
    layout,
    serviceWorker,
    styles,
  ] = await Promise.all([
    import("../app/androidInstall.mjs"),
    import("../app/chatEntry.mjs"),
    readFile(new URL("../app/components/ChatEntryChoice.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/LoginScreen.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/mesil-chat-sw-v6.js", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.equal(resolveChatEntryPlatform("Mozilla/5.0 (Linux; Android 15)", false), "android");
  assert.equal(resolveChatEntryPlatform("Mozilla/5.0 (iPhone)", false), "web");
  assert.equal(resolveChatEntryPlatform("Mozilla/5.0 (Windows NT 10.0)", false), "web");
  assert.equal(resolveChatEntryPlatform("Mozilla/5.0 (Linux; Android 15)", true), "native");
  assert.equal(platformManifestDisplay("Mozilla/5.0 (Linux; Android 15)"), "browser");
  assert.equal(platformManifestDisplay("Mozilla/5.0 (iPhone)"), "standalone");
  assert.equal(isAndroidWebInstallCandidate("Mozilla/5.0 (Linux; Android 15)", false), true);
  assert.deepEqual(
    parseStaffReleaseMetadata({
      filename: "MESIL_Chat-1.0.0-test.20260901.1-staff-test.apk",
      version_name: "1.0.0-test.20260901.1",
      version_code: 2026090101,
      bytes: 123456,
      sha256: "a".repeat(64),
    }),
    {
      filename: "MESIL_Chat-1.0.0-test.20260901.1-staff-test.apk",
      version_name: "1.0.0-test.20260901.1",
      version_code: 2026090101,
      bytes: 123456,
      sha256: "a".repeat(64),
      download_url: "/downloads/MESIL_Chat-1.0.0-test.20260901.1-staff-test.apk",
    },
  );
  assert.equal(parseStaffReleaseMetadata({ filename: "../unsafe.apk" }), null);
  assert.match(component, /설치 없이 웹으로 사용/);
  assert.match(component, /Android 통화 알림 앱 받기/);
  assert.match(component, /PC·아이폰·앱을 설치하지 않을 때/);
  assert.match(component, /휴대전화 잠금 상태에서도 통화 알림/);
  assert.match(page, /<ChatEntryChoice/);
  assert.match(page, /<ChatApp/);
  assert.doesNotMatch(login, /ChatEntryChoice/);
  assert.doesNotMatch(login, /AndroidInstallPrompt/);
  assert.doesNotMatch(chat, /AndroidInstallPrompt/);
  assert.match(layout, /platform\.webmanifest/);
  assert.doesNotMatch(serviceWorker, /"\/mesil-chat\.webmanifest"/);
  assert.match(styles, /\.chat-entry-choice/);
});

test("disabled APK delivery never requests release metadata", async () => {
  const { loadChatEntryRelease } = await import("../app/chatEntry.mjs");
  let requests = 0;
  const result = await loadChatEntryRelease(false, async () => {
    requests += 1;
    throw new Error("Disabled APK delivery must not use the network");
  });
  assert.equal(result, null);
  assert.equal(requests, 0);
});

test("explicitly enabled APK delivery reads metadata and handles unavailable releases", async () => {
  const { loadChatEntryRelease } = await import("../app/chatEntry.mjs");
  const payload = { filename: "synthetic.apk" };
  const result = await loadChatEntryRelease(true, async (url, options) => {
    assert.equal(url, "/downloads/staff-test-release.json");
    assert.equal(options.cache, "no-store");
    return { ok: true, json: async () => payload };
  });
  assert.deepEqual(result, payload);
  assert.equal(await loadChatEntryRelease(true, async () => ({ ok: false })), null);
});

test("public source excludes APK payloads and release metadata from distributable assets", async () => {
  const files = await readdir(new URL("../public/", import.meta.url), { recursive: true });
  assert.deepEqual(files.filter((name) => /\.apk$|staff-test-release\.json$/i.test(name)), []);
  const serviceWorker = await readFile(new URL("../public/mesil-chat-sw-v6.js", import.meta.url), "utf8");
  assert.match(serviceWorker, /url\.pathname\.startsWith\("\/downloads\/"\)/);
});

test("uses the short Mesil chat brand text without an APK download mount", async () => {
  const [login, chat, layout, manifestText, legacyManifestText, caddy, compose] =
    await Promise.all([
      readFile(new URL("../app/components/LoginScreen.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
      readFile(new URL("../public/mesil-chat.webmanifest", import.meta.url), "utf8"),
      readFile(new URL("../public/manifest.webmanifest", import.meta.url), "utf8"),
      readFile(new URL("../../deploy/Caddyfile", import.meta.url), "utf8"),
      readFile(new URL("../../docker-compose.yml", import.meta.url), "utf8"),
    ]);

  assert.match(login, /<small>메실채팅<\/small>/);
  assert.match(chat, /<small>메실채팅<\/small>/);
  assert.doesNotMatch(login, /메디컬 실버 채팅/);
  assert.doesNotMatch(chat, /메디컬 실버 채팅/);
  assert.doesNotMatch(layout, /메디컬 실버 채팅/);
  assert.equal(JSON.parse(manifestText).description.includes("메실채팅"), true);
  assert.equal(JSON.parse(legacyManifestText).description.includes("메실채팅"), true);
  assert.match(caddy, /respond @downloads 404/);
  assert.doesNotMatch(compose, /mobile\/artifacts|\/srv\/downloads/);
});

test("defaults reassessment chat period from the previous reviewed basis or six months", () => {
  assert.deepEqual(
    defaultAssessmentChatPeriod({
      endDate: "2026-08-31",
      previousBasisDate: "2026-05-18",
    }),
    {
      startDate: "2026-05-19",
      endDate: "2026-08-31",
      source: "previous_basis",
    },
  );
  assert.deepEqual(
    defaultAssessmentChatPeriod({
      endDate: "2026-08-31",
      previousBasisDate: null,
    }),
    {
      startDate: "2026-02-28",
      endDate: "2026-08-31",
      source: "six_month_fallback",
    },
  );
  assert.deepEqual(
    defaultAssessmentChatPeriod({
      endDate: "2026-08-31",
      previousBasisDate: "invalid",
    }),
    {
      startDate: "2026-02-28",
      endDate: "2026-08-31",
      source: "six_month_fallback",
    },
  );
});

test("starts reassessment chat evidence after the earliest latest form basis date", () => {
  assert.deepEqual(
    defaultAssessmentChatPeriod({
      endDate: "2026-08-31",
      previousBasisDates: ["2026-05-18", "2026-03-01", "2026-04-12"],
    }),
    {
      startDate: "2026-03-02",
      endDate: "2026-08-31",
      source: "previous_basis",
    },
  );
});

test("shows first institution setup only to an admin with no active living space", () => {
  assert.deepEqual(
    institutionSetupState({
      checked: true,
      isAdmin: true,
      isReviewerSession: false,
      units: [],
    }),
    { activeLivingSpaceCount: 0, required: true },
  );
  assert.equal(
    institutionSetupState({
      checked: true,
      isAdmin: true,
      isReviewerSession: false,
      units: [{ unit_type: "floor", is_active: true }],
    }).required,
    false,
  );
  assert.equal(
    institutionSetupState({
      checked: true,
      isAdmin: false,
      isReviewerSession: false,
      units: [],
    }).required,
    false,
  );
  assert.equal(
    institutionSetupState({
      checked: true,
      isAdmin: true,
      isReviewerSession: true,
      units: [],
    }).required,
    false,
  );
  assert.equal(
    institutionSetupState({
      checked: true,
      isAdmin: true,
      isReviewerSession: false,
      isCodedSynthetic: true,
      units: [],
    }).required,
    false,
  );
});

test("bridges Android share intents into the existing attachment picker", async () => {
  const [chatApp, nativeShare] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/nativeShare.ts", import.meta.url), "utf8"),
  ]);

  assert.match(nativeShare, /registerPlugin<NativeShareReceiverPlugin>/);
  assert.match(nativeShare, /"MesilShareReceiver"/);
  assert.match(nativeShare, /consumePendingShare/);
  assert.match(nativeShare, /Capacitor\.convertFileSrc/);
  assert.match(nativeShare, /releaseSharedFiles/);
  assert.match(chatApp, /consumeNativeSharedFiles/);
  assert.match(chatApp, /listenForNativeShares/);
  assert.match(chatApp, /attachmentSelectionError\(sharedFiles\)/);
});

test("loads room images lazily without showing an unexplained blank box", async () => {
  const attachmentDisplay = await readFile(
    new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
    "utf8",
  );

  assert.match(attachmentDisplay, /loading="lazy"/);
  assert.match(attachmentDisplay, /decoding="async"/);
  assert.match(attachmentDisplay, /사진 불러오는 중…/);
  assert.match(attachmentDisplay, /사진을 불러오지 못했습니다/);
  assert.match(attachmentDisplay, /safeExtractionErrorMessage/);
  assert.match(attachmentDisplay, /https\?:\\\/\\\//);
});

test("keeps handwriting correction inside the image editor with instant internal recording", async () => {
  const [panel, messageDetail, attachmentDisplay, coordinateEditor, types, styles] = await Promise.all([
    readFile(
      new URL(
        "../app/components/HandwritingVoiceCorrectionPanel.tsx",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL("../app/components/MessageDetailOverlay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/CoordinateTextEditor.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.doesNotMatch(messageDetail, /<HandwritingVoiceCorrectionPanel/);
  assert.match(attachmentDisplay, /className="handwriting-correction-dialog"/);
  assert.match(attachmentDisplay, /<HandwritingVoiceCorrectionPanel/);
  assert.match(
    attachmentDisplay,
    /const editorAttachment =\s*reviewAttachment\?\.id === attachment\.id/,
  );
  assert.match(attachmentDisplay, /attachments=\{editorAttachments\}/);
  assert.match(
    panel,
    /latest_confirmed_text[\s\S]*reviewed_text[\s\S]*suggested_text[\s\S]*extracted_text[\s\S]*original_extracted_text/,
  );
  assert.match(panel, /손글씨 수정/);
  assert.match(panel, /직접 고치기/);
  assert.match(panel, /말로 고치기/);
  assert.match(panel, /말하는 방법/);
  assert.match(panel, /전체 읽어주기/);
  assert.match(panel, /틀린 부분만 말하기/);
  assert.match(panel, /내용 설명으로 보완/);
  assert.match(panel, /role="group"/);
  assert.match(panel, /aria-pressed/);
  assert.match(panel, /navigator\.mediaDevices\.getUserMedia/);
  assert.match(panel, /new MediaRecorder/);
  assert.match(panel, /recorder\.start\(\)/);
  assert.doesNotMatch(panel, /recorder\.start\(500\)/);
  assert.match(panel, /MAX_RECORDING_SECONDS = 120/);
  assert.match(panel, /audio\/mp4/);
  assert.match(panel, /마이크 시작/);
  assert.match(panel, /녹음 마치기/);
  assert.match(panel, /녹음 취소/);
  assert.match(panel, /처리 취소/);
  assert.match(panel, /voice-correction-recordings/);
  assert.match(panel, /내부 음성 받아쓰기가 완료됐습니다/);
  assert.match(panel, /await compareEvidence\(transcribed\.id, mode\)/);
  assert.match(panel, /받아쓰기와 OCR 대조가 끝났습니다/);
  assert.match(panel, /천천히 또박또박 읽어 주세요/);
  assert.match(panel, /최종문을 정리하고 있습니다/);
  assert.match(panel, /comparison\.audio_quality\.message/);
  assert.match(panel, /음성 처리가 종료됐습니다/);
  assert.match(panel, /시험·예외용 음성파일 사용/);
  assert.match(panel, /접수/);
  assert.match(panel, /음성 전사/);
  assert.match(panel, /교정 제안/);
  assert.match(panel, /승인 대기/);
  assert.match(
    panel,
    /parameters\.set\("refine", "true"\)/,
  );
  assert.match(panel, /refine: "false"/);
  assert.match(panel, /setComparison\(result\)/);
  assert.match(panel, /setRefining\(true\)/);
  assert.match(panel, /if \(!draftDirtyRef\.current\) setDirectEdit\(refinedText\)/);
  assert.match(panel, /내부 AI가 문장을 다듬는 중입니다/);
  assert.match(panel, /원본·음성·최종문 비교/);
  assert.match(panel, /원본 OCR/);
  assert.match(panel, /음성 전사본/);
  assert.match(panel, /최종문 제안/);
  assert.match(panel, /변경 부분 강조 미리보기/);
  assert.match(panel, /voice-correction-final-editor/);
  assert.match(panel, /value=\{finalDraftText\}/);
  assert.match(panel, /updateFinalDraft/);
  assert.match(panel, /const finalDirtyRef = useRef\(false\)/);
  assert.match(panel, /result\.versions\.at\(-1\)/);
  assert.match(panel, /setFinalDraftText\(latest\.approved_final_text\)/);
  assert.match(panel, /setFinalConfirmed\(true\)/);
  assert.match(panel, /DifferenceText/);
  assert.match(panel, /voice-correction-difference/);
  assert.match(panel, /검토 중/);
  assert.match(panel, /승인 완료/);
  assert.match(panel, /최종문 전체를 확인했습니다/);
  assert.match(panel, /확인 전·취소·화면 이탈 상태는 저장되지 않습니다/);
  assert.match(panel, /공식 기록·서명·공단 전송·모델 학습에는 반영되지 않습니다/);
  assert.match(panel, /이전 승인본을 덮어쓰지 않고 새 수정본으로 추가합니다/);
  assert.match(panel, /voice-correction-approvals/);
  assert.match(panel, /method: "POST"/);
  assert.match(panel, /crypto\.randomUUID/);
  assert.match(panel, /savingRef/);
  assert.match(panel, /aria-live="polite"/);
  assert.match(panel, /effectiveWarnings\.length/);
  assert.match(panel, /확인 필요: \{effectiveWarnings\.join/);
  assert.match(panel, /recordingState === "error"/);
  assert.match(panel, /\? "다시 녹음"/);
  assert.match(panel, /원본 위치 보기 · 확인된 글줄/);
  assert.match(panel, /원본 위치 확인 필요 · 임의 좌표를 만들지 않습니다/);
  assert.match(panel, /coordinate_region_ids/);
  assert.match(panel, /source_text: sourceText\.trim\(\)/);
  assert.match(panel, /proposed_text: proposedText\.trim\(\)/);
  assert.match(panel, /final_text: finalText/);
  assert.match(panel, /conflicts_confirmed: finalConfirmed/);
  assert.match(types, /HandwritingVoiceCorrectionComparison/);
  assert.match(types, /HandwritingCorrectionApprovalHistory/);
  assert.match(types, /official_record_saved: false/);
  assert.match(types, /training_data_adopted: false/);
  assert.match(styles, /\.handwriting-voice-correction/);
  assert.match(styles, /\.handwriting-correction-dialog/);
  assert.match(styles, /\.handwriting-correction-image-scroll/);
  assert.match(styles, /\.handwriting-confirmed-region/);
  assert.match(styles, /\.voice-correction-recorder/);
  assert.match(styles, /\.voice-correction-primary-actions/);
  assert.match(styles, /\.voice-correction-voice-mode/);
  assert.match(styles, /\.voice-correction-three-way/);
  assert.match(styles, /\.voice-correction-final-preview/);
  assert.match(styles, /\.voice-correction-difference/);
  assert.match(styles, /\.voice-correction-inline-warning/);
  assert.match(styles, /\.voice-correction-approval-workspace/);
  assert.match(styles, /@media \(max-width: 560px\)/);
  assert.doesNotMatch(panel, /바뀐 부분과 이유/);
  assert.doesNotMatch(panel, /문장별 직원 확인/);
  assert.doesNotMatch(panel, /approval-sentence-list/);
  assert.doesNotMatch(panel, /전체 승인 해제/);
  assert.doesNotMatch(panel, /문장 구분이 달라졌습니다/);
  assert.doesNotMatch(panel, /위 차이를 원본과 대조했습니다/);
  assert.doesNotMatch(panel, /sentenceShapeMatches/);
  assert.doesNotMatch(
    panel,
    /시간·수량·단위·이름·약명·진단·후속상태의 충돌 또는 미확인 항목/,
  );
  assert.doesNotMatch(panel, /method:\s*"PATCH"/);
  assert.doesNotMatch(panel, /official-record/);
  assert.doesNotMatch(panel, /role="radiogroup"/);
  assert.doesNotMatch(panel, />\s*전체 읽어주기 비교하기\s*</);
  assert.doesNotMatch(coordinateEditor, /선택한 글자 고급 설정/);
  assert.doesNotMatch(coordinateEditor, /<option value="general">일반 문장<\/option>/);
  assert.doesNotMatch(coordinateEditor, /<option value="time">시간<\/option>/);
});

test("keeps resident care planning separate from AI scoring and labels the operating cycle", async () => {
  const [periodWorkDesk, planningPanel, cycleWorkspace, intakeWorkspace, types] = await Promise.all([
    readFile(new URL("../app/components/PeriodWorkDesk.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/ResidentCarePlanningPanel.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/ResidentAssessmentCycleWorkspace.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AssessmentDraftIntakeWorkspace.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
  ]);

  assert.match(periodWorkDesk, /<ResidentCarePlanningPanel/);
  assert.match(periodWorkDesk, /대화·답글이 0건입니다. 오류가 아닙니다/);
  assert.match(periodWorkDesk, /범위를 넓히거나 모든 어르신을 선택해 주세요/);
  assert.match(periodWorkDesk, /aria-label="어르신 선택"/);
  assert.match(types, /is_test_data: boolean/);
  assert.match(planningPanel, /사용자 제공 운영 원칙/);
  assert.match(planningPanel, /AI는 점수·진단·급여계획을 자동 확정하지 않습니다/);
  assert.match(planningPanel, /저장된 사실/);
  assert.match(planningPanel, /추가로 물어볼 항목/);
  assert.match(planningPanel, /전문가 확인 항목/);
  assert.match(planningPanel, /상태 변경 시 즉시 재검토 후보/);
  assert.match(planningPanel, /저장된 수정 이력/);
  assert.match(planningPanel, /assessmentReasonLabels/);
  assert.match(planningPanel, /assessment-chat-link-status/);
  assert.match(planningPanel, /활용할 확인 기록 \$\{linkedChatSourceCount\}건 자동 연결/);
  assert.match(planningPanel, /연결된 매실챗 기록 없음 · 문서자료로 계속 진행합니다/);
  assert.match(planningPanel, /현재 편집본/);
  assert.match(planningPanel, /periodic_reassessment: "정기 재평가"/);
  assert.match(planningPanel, /state_change_reassessment: "상태변화"/);
  assert.match(planningPanel, /staff_review: "직원 재검토"/);
  assert.match(
    planningPanel,
    /직원 최종 확인 전 공식 기록 저장·서명 확정·공단 전송은 하지 않습니다/,
  );
  assert.match(planningPanel, /new-admission-drafts/);
  assert.match(planningPanel, /new-admission-drafts\/\$\{draft\.id\}\/questions/);
  assert.match(planningPanel, /다시 묻지 않고 재사용하는 사실/);
  assert.match(planningPanel, /직원에게 확인할 항목/);
  assert.match(planningPanel, /작성기관 직접 확인/);
  assert.match(planningPanel, /다른 기관 참고/);
  assert.match(planningPanel, /기초사정·급여제공계획 검토 결과/);
  assert.match(planningPanel, /검토한 기초사정 4종과 급여제공계획을 확인합니다/);
  assert.match(planningPanel, /검토 결과 인쇄·PDF/);
  assert.match(
    planningPanel,
    /print-form-needs-assessment/,
    "욕구사정 인쇄 양식은 다른 서류와 구분해 한 페이지 밀도로 조정할 수 있어야 합니다.",
  );
  assert.match(
    planningPanel,
    /break-inside:\s*avoid/,
    "양식 절 제목만 페이지 끝에 남지 않도록 절 단위 페이지 분할을 막아야 합니다.",
  );
  assert.match(
    planningPanel,
    /\.print-form-needs-assessment h3/,
    "욕구사정의 반복 절 여백은 인쇄 전용 규칙으로 압축해야 합니다.",
  );
  assert.match(
    planningPanel,
    /근거로 채운 내용을 확인하고 필요한 부분만 수정해 주세요/,
  );
  assert.match(planningPanel, /이 양식 복사/);
  assert.match(planningPanel, /form-structure-preview/);
  assert.match(planningPanel, /양식 항목을 바로 수정/);
  assert.match(planningPanel, /carePlanCellStateLabels/);
  assert.match(planningPanel, /기초사정에서 반영/);
  assert.match(planningPanel, /추천 초안 · 직원 확인/);
  assert.match(planningPanel, /readOnly=\{column === "장기요양 필요영역"\}/);
  assert.match(planningPanel, /row\.cell_evidence_refs\[column\]/);
  assert.match(planningPanel, /reviewed_document_types/);
  assert.match(planningPanel, /form_values/);
  assert.match(planningPanel, /이 양식의 내용을 검토했습니다/);
  assert.match(planningPanel, /기초사정 4종 검토/);
  assert.doesNotMatch(planningPanel, /직원이 수정할 최종 초안/);
  assert.doesNotMatch(planningPanel, /5종 편집용 초안의 수정 내용을 확인했습니다/);
  assert.doesNotMatch(planningPanel, /<h3>직원 수정문<\/h3>/);
  assert.match(planningPanel, /const latestDraftSummary = draftHistory\?\.items\[0\]/);
  assert.match(planningPanel, /draft\.assessment_date/);
  assert.match(types, /reviewed_assessment_count: number/);
  assert.match(planningPanel, /const previousDraftSummaries = draftHistory\?\.items\.slice\(1\)/);
  assert.match(planningPanel, /이전 분석 이력 \{previousDraftSummaries\.length\}건/);
  assert.match(planningPanel, /aria-label="저장된 비교 자료"/);
  assert.match(planningPanel, /근거 보기/);
  assert.match(planningPanel, /작성 순서·확인 항목 상세 보기/);
  assert.match(planningPanel, /workspace=\{questionFlows\[latestDraftSummary\.id\]\.form_workspace\}/);
  assert.match(planningPanel, /new-admission-drafts\/\$\{draft\.id\}\/evidence-summary/);
  assert.match(planningPanel, /자료에서 확인한 내용/);
  assert.match(planningPanel, /확인된 사실/);
  assert.match(planningPanel, /자동 선택하지 않음/);
  assert.match(planningPanel, /근거가 부족한 항목/);
  assert.match(planningPanel, /반영 양식/);
  assert.match(planningPanel, /보완 양식/);
  assert.match(planningPanel, /evidenceDocumentSummary\(item\.document_types\)/);
  assert.match(planningPanel, /상담일지·전원 관련 서류 등 추가자료 반영/);
  assert.match(
    planningPanel,
    /new-admission-drafts\/\$\{draftRecord\.id\}\/materials/,
  );
  assert.match(planningPanel, /기존 초안을 덮어쓰지 않고 새 수정본으로 반영합니다/);
  const formFirstWorkspace = planningPanel.slice(
    planningPanel.indexOf("function FormFirstWorkspace"),
    planningPanel.indexOf("function CarePlanDraftPreview"),
  );
  assert.doesNotMatch(
    formFirstWorkspace,
    /className="form-workspace-summary"/,
    "기초사정 작성 화면은 확인 자료·근거·보완 건수를 기본 화면에 앞세우면 안 됩니다.",
  );
  assert.doesNotMatch(
    formFirstWorkspace,
    /반영 \{item\.confirmed_or_evidence_count\} · 보완 \{item\.staff_input_count\}/,
    "양식 탭은 내부 반영·보완 건수를 표시하지 않아야 합니다.",
  );
  assert.doesNotMatch(
    formFirstWorkspace,
    /className="form-workspace-pending"/,
    "빈칸 목록을 기본 화면에 길게 펼치지 않아야 합니다.",
  );
  assert.match(formFirstWorkspace, /검토 순서 \{activeStepNumber\}\/\{workspace\.documents\.length\}/);
  assert.match(formFirstWorkspace, /<summary>제출 자료와 근거 보기<\/summary>/);
  assert.match(formFirstWorkspace, /<summary>직원이 확인할 내용<\/summary>/);
  assert.doesNotMatch(
    planningPanel,
    /key=\{`\$\{latestDraftSummary\.id\}-\$\{questionFlows\[latestDraftSummary\.id\]\.form_workspace\.revision\}`\}/,
    "수정본 저장 직후 편집기를 다시 마운트해 저장 완료 안내를 지우면 안 됩니다.",
  );
  assert.match(
    formFirstWorkspace,
    /setFormValues\(workspaceFormValues\(workspace\)\)/,
    "새 수정본을 받은 편집기는 저장 안내를 유지하면서 최신 양식 값으로 동기화해야 합니다.",
  );
  assert.match(
    formFirstWorkspace,
    /const workspaceIdentity = `\$\{draftRecord\.id\}:\$\{workspace\.revision\}`/,
    "편집기는 효과 훅이 아니라 초안 ID와 수정본 번호로 새 양식을 구분해야 합니다.",
  );
  assert.doesNotMatch(
    formFirstWorkspace,
    /useEffect\(\(\) => \{[\s\S]*?setFormValues\(workspaceFormValues\(workspace\)\)/,
    "양식 동기화를 효과 훅의 즉시 상태 변경으로 처리하면 안 됩니다.",
  );
  assert.match(types, /NewAdmissionFormWorkspace/);
  assert.match(types, /document_types: CarePlanningDocumentType\[\]/);
  assert.match(types, /AssessmentEvidenceSummary/);
  assert.match(planningPanel, /충돌·현재 상태·점수·진단·서명은\s*자동 확정하지 않으며/);
  assert.match(intakeWorkspace, /한 PDF에 여러 서류가 있으면 페이지별로 자동 분류합니다/);
  assert.match(intakeWorkspace, /자동 분류\(권장\)/);
  assert.match(intakeWorkspace, /자동 분류로 되돌리기/);
  assert.match(intakeWorkspace, /assessment-chat-evidence\/preview/);
  assert.match(intakeWorkspace, /자동 연결할 매실챗 확인 기록/);
  assert.match(intakeWorkspace, /선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다/);
  assert.match(intakeWorkspace, /excluded_chat_source_refs/);
  assert.match(intakeWorkspace, /item\.documentKinds\.join\(","\)/);
  assert.match(intakeWorkspace, /previousBasisDates: string\[\]/);
  assert.match(planningPanel, /draft\.reviewed_assessment_count !== 4/);
  assert.match(planningPanel, /latestBasisDateByDocument/);
  assert.match(intakeWorkspace, /defaultAssessmentChatPeriod/);
  assert.match(intakeWorkspace, /직전 기초사정·급여계획 기준일 다음 날부터 자동 연결했습니다/);
  assert.match(intakeWorkspace, /직전 기준일이 없어 최근 6개월을 기본으로 연결했습니다/);
  assert.match(
    intakeWorkspace,
    /인정서와 개인별장기요양이용계획서를 기본으로, 처방전·입소 전 건강검진 등 현재 가진 자료를 함께 제출합니다/,
  );
  assert.match(
    intakeWorkspace,
    /직전 기초사정 4종과 직전 급여제공계획을 기준자료로 사용하고, 가능한 경우 결과평가도 함께 제출합니다/,
  );
  assert.match(
    intakeWorkspace,
    /확인할 수 없는 양식 칸은 비워 두어 직원이 직접 보완합니다/,
  );
  assert.doesNotMatch(
    intakeWorkspace,
    /없거나 충돌한 내용은 근거 부족·직원 확인 필요로 남깁니다/,
  );
  assert.match(planningPanel, /기초사정 4단계 안전 작성 순서/);
  assert.match(planningPanel, /전체 4단계 중/);
  assert.match(planningPanel, /workflow\.workflow_notice/);
  assert.match(planningPanel, /이전 자료에서 재사용한 사실/);
  assert.match(planningPanel, /이 단계에서 새로 확인할 사항/);
  assert.match(planningPanel, /점수·위험등급·진단: 아직 확정하지 않음/);
  assert.match(planningPanel, /직원 최종 확인 전 공식 기록 저장·서명 확정·공단 전송은 하지 않습니다/);
  assert.match(planningPanel, /장기요양급여 제공계획 편집용 초안/);
  assert.match(planningPanel, /대상 · \{residentName\} · 현재 수정 이력 v\{preview\.revision\}의 직원 확인/);
  assert.match(planningPanel, /확인된 기초사정 결과/);
  assert.match(planningPanel, /급여제공계획 초안에 반영된 내용/);
  assert.match(planningPanel, /직원에게 물어볼 내용/);
  assert.match(planningPanel, /자료 충돌·현재 관찰 필요/);
  assert.match(planningPanel, /전문가·공식 기준 확인 필요/);
  assert.match(planningPanel, /급여 종류·횟수·시간·점수·진단·목표·서명은 확인된 값 없이 만들지 않습니다/);
  assert.match(planningPanel, /공식 기록 저장/);
  assert.match(planningPanel, /서명 확정/);
  assert.match(planningPanel, /공단 전송/);
  assert.match(types, /NewAdmissionCarePlanPreview/);
  assert.doesNotMatch(
    planningPanel,
    /className="care-planning-card"[\s\S]{0,100}open=\{item\.is_candidate\}/,
    "기초사정 항목은 사용자가 펼치기 전 기본 접힘이어야 합니다.",
  );
  assert.match(planningPanel, /마지막 \{item\.assessment_date/);
  assert.match(planningPanel, /다음\{" "\}/);
  assert.match(planningPanel, /상태변경 후보/);
  assert.match(types, /ResidentCarePlanningProfile/);
  assert.match(periodWorkDesk, /기존 작성본·수정 이력/);
  assert.match(cycleWorkspace, /신규입소와 기존 어르신의 정기 재평가·상태변경·직원 재검토/);
  assert.match(cycleWorkspace, /const reasonOptions: ResidentAssessmentReason\[\]/);
  assert.doesNotMatch(cycleWorkspace, /Object\.entries\(reasonLabels\)/);
  assert.match(cycleWorkspace, /직원이 원본을 확인해 직접 입력/);
  assert.match(cycleWorkspace, /Carefor 읽기 전용 공급자는 별도 승인 전 연결하지 않습니다/);
  assert.match(cycleWorkspace, /평가 회차/);
  assert.match(cycleWorkspace, /이전과 현재의 차이/);
  assert.match(cycleWorkspace, /급여제공계획 검토 목록에 담기/);
  assert.match(cycleWorkspace, /아직 공식 계획에는 반영되지 않았습니다/);
  assert.match(cycleWorkspace, /current_revision: updated\.current_revision/);
  assert.match(cycleWorkspace, /공식 저장 전 편집용 초안/);
  assert.match(cycleWorkspace, /공식 기록 저장/);
  assert.match(cycleWorkspace, /서명 확정/);
  assert.match(cycleWorkspace, /공단 전송/);
  assert.match(types, /ResidentAssessmentCycle/);
});

test("refreshes the conversation after an admin comment succeeds without relying on websocket timing", async () => {
  const chatApp = await readFile(
    new URL("../app/components/ChatApp.tsx", import.meta.url),
    "utf8",
  );
  const submitComment = chatApp.match(
    /async function submitComment[\s\S]*?(?=\n  function openForwardDialog)/,
  )?.[0];

  assert.ok(submitComment, "submitComment implementation should be present");
  assert.match(submitComment, /await apiFetch<MessageComment>/);
  assert.match(submitComment, /setCommentThread\(\(current\) => \[\.\.\.current, comment\]\)/);
  assert.match(submitComment, /setCommentBody\(""\);\s*await refreshRoomAfterAiShare\(\);/);
  assert.match(chatApp, /event\.nativeEvent\.isComposing/);
  assert.match(chatApp, /event\.currentTarget\.form\?\.requestSubmit\(\)/);
  assert.match(chatApp, /Enter로 등록 · Shift\+Enter로 줄바꿈/);
});

test("keeps full audio transcripts collapsed in message detail", async () => {
  const attachmentDisplay = await readFile(
    new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
    "utf8",
  );

  assert.match(attachmentDisplay, /className="attachment-transcript-details"/);
  assert.match(attachmentDisplay, /음성 전사 원문 보기/);
  assert.match(attachmentDisplay, /isAudio \? \(/);
  const compactAttachment = attachmentDisplay.match(
    /if \(compact\) \{[\s\S]*?(?=\n  if \(isImage\))/,
  )?.[0];
  assert.ok(compactAttachment, "compact attachment rendering should be present");
  assert.match(compactAttachment, /isAudio/);
  assert.match(compactAttachment, /<audio controls preload="metadata" src=\{url\}>/);
  assert.match(compactAttachment, /음성 파일 재생/);
});

test("keeps attachment transcription, correction, and original save directly discoverable", async () => {
  const [chatApp, attachmentDisplay, globalStyles] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(chatApp, /이미지 글자 판독·수정/);
  assert.match(chatApp, /음성 받아쓰기 확인·수정/);
  assert.match(chatApp, /data-attachment-review-entry/);
  assert.match(chatApp, /openMessageDetail\(message\.id\)/);
  assert.match(
    chatApp,
    /const canProcessAttachmentText =\s*\(mine && hasImageAttachment\) \|\| me\.role === "admin" \|\| me\.can_process_records/,
  );
  assert.match(chatApp, /canProcessAttachmentText[\s\S]*?첨부 보기·저장/);
  assert.match(
    chatApp,
    /canProcessRecords=\{me\.role === "admin" \|\| me\.can_process_records\}/,
  );
  assert.match(attachmentDisplay, /원본 저장/);
  assert.match(attachmentDisplay, /saveOriginalFile/);
  assert.match(attachmentDisplay, /판독문 확인·수정/);
  assert.match(attachmentDisplay, /받아쓰기 확인·수정/);
  assert.match(attachmentDisplay, /data-attachment-view="staff-simple"/);
  assert.match(attachmentDisplay, /확인된 받아쓰기/);
  assert.match(attachmentDisplay, /확인된 판독문/);
  assert.match(attachmentDisplay, /아직 관리자가 확인한 텍스트가 없습니다/);
  assert.match(attachmentDisplay, /if \(!canEditExtraction\)/);
  assert.doesNotMatch(
    globalStyles,
    /@media \(max-width: 720px\) and \(orientation: portrait\)[\s\S]*?\.attachment-extraction-heading-actions,[\s\S]*?display: none !important/,
  );
});

test("keeps confirmed OCR text in WorkDesk detail without duplicating it in the candidate list", async () => {
  const [workDesk, attachmentDisplay] = await Promise.all([
    readFile(
      new URL("../app/components/WorkDesk.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
      "utf8",
    ),
  ]);

  assert.match(
    workDesk,
    /className="work-item-thumbnail"[\s\S]*?<AttachmentDisplay[\s\S]*?compact[\s\S]*?showCompactConfirmedText=\{false\}/,
  );
  assert.match(attachmentDisplay, /showCompactConfirmedText = true/);
  assert.match(
    attachmentDisplay,
    /showCompactConfirmedText && confirmedText \? \(/,
  );
  assert.match(
    workDesk,
    /className={`work-attachment-item[\s\S]*?<AttachmentDisplay[\s\S]*?accessScope="workdesk"/,
  );
});

test("registers the Android app for native incoming-call notifications", async () => {
  const [
    chatApp,
    nativePush,
    pushPlugin,
    firebaseService,
    messageNotification,
    mainActivity,
    voiceCall,
  ] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/nativePush.ts", import.meta.url), "utf8"),
    readFile(
      new URL(
        "../../mobile/android/app/src/main/java/kr/silvermedical/mesilchat/MesilPushPlugin.java",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "../../mobile/android/app/src/main/java/kr/silvermedical/mesilchat/MesilFirebaseMessagingService.java",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "../../mobile/android/app/src/main/java/kr/silvermedical/mesilchat/MesilMessageNotification.java",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(
      new URL(
        "../../mobile/android/app/src/main/java/kr/silvermedical/mesilchat/MainActivity.java",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(new URL("../app/voiceCall.ts", import.meta.url), "utf8"),
  ]);

  assert.match(nativePush, /registerPlugin<MesilPushPlugin>\("MesilPush"\)/);
  assert.match(nativePush, /\/api\/mobile-push\/devices/);
  assert.match(chatApp, /isNativeMesilApp/);
  assert.match(nativePush, /installation_id/);
  assert.match(nativePush, /platform: "android"/);
  assert.match(nativePush, /fullScreenIntentAllowed/);
  assert.match(nativePush, /setCallScreenActive/);
  assert.match(nativePush, /setCallAudioRoute/);
  assert.match(chatApp, /setNativeCallScreenActive/);
  assert.match(chatApp, /setNativeCallAudioRoute/);
  assert.match(nativePush, /microphoneAllowed/);
  assert.match(nativePush, /cameraAllowed/);
  assert.match(nativePush, /readNativePushPermissionStatus/);
  assert.match(nativePush, /openNativeNotificationSettings/);
  assert.match(nativePush, /readPendingNativeCallAction/);
  assert.match(nativePush, /clearPendingNativeCallAction/);
  assert.match(nativePush, /acknowledgeNativeWebCall/);
  assert.match(nativePush, /markRegistrationSynchronized/);
  assert.match(pushPlugin, /getPermissionStatus/);
  assert.match(pushPlugin, /Settings\.ACTION_APP_NOTIFICATION_SETTINGS/);
  assert.match(nativePush, /openNativeFullScreenIntentSettings/);
  assert.match(nativePush, /await mesilPush\.openFullScreenIntentSettings\(\)/);
  assert.match(chatApp, /synchronizeNativePushRegistration/);
  assert.match(chatApp, /\/api\/voice-calls\/pending/);
  assert.match(chatApp, /searchParams\.get\("call_action"\)/);
  assert.match(chatApp, /target\?\.callAction === "accept"/);
  assert.match(chatApp, /await acceptIncomingCall\(\)/);
  assert.match(chatApp, /clearPendingNativeCallAction/);
  assert.match(chatApp, /target\?\.callAction === "decline"/);
  assert.match(voiceCall, /const dismissIncoming = useCallback/);
  assert.match(
    chatApp,
    /pendingCalls\.some\(\(call\) => call\.call_id === currentIncomingCallId\)/,
  );
  assert.match(chatApp, /dismissIncomingCall\(currentIncomingCallId\)/);
  assert.match(firebaseService, /"message"\.equals\(kind\)/);
  assert.match(firebaseService, /"comment"\.equals\(kind\)/);
  assert.match(firebaseService, /!MesilAppVisibility\.isForeground\(\)/);
  assert.match(firebaseService, /MesilMessageNotification\.show\(this, data\)/);
  assert.match(firebaseService, /webClaimGraceMillis/);
  assert.match(voiceCall, /voice_call_delivery_status/);
  assert.match(voiceCall, /통화 요청을 전달하지 못했습니다/);
  assert.match(messageNotification, /mesil-messages-v1/);
  assert.match(messageNotification, /NotificationCompat\.CATEGORY_MESSAGE/);
  assert.match(messageNotification, /NotificationCompat\.VISIBILITY_PRIVATE/);
  assert.match(mainActivity, /openMesilIntent/);
  assert.match(mainActivity, /loadUrl\(uri\.toString\(\)\)/);
  assert.doesNotMatch(mainActivity, /talk\.silvermedical\.kr/);
});

test("keeps a valid mobile session during temporary connection failures", async () => {
  assert.equal(isSessionAuthenticationFailure(401), true);
  assert.equal(isSessionAuthenticationFailure(403), true);
  assert.equal(isSessionAuthenticationFailure(0), false);
  assert.equal(isSessionAuthenticationFailure(500), false);
  assert.equal(SESSION_RETRY_INTERVAL_MS, 5_000);
  assert.equal(
    shouldRetrySessionCheck({ unavailable: true, online: true, visible: true }),
    true,
  );
  assert.equal(
    shouldRetrySessionCheck({
      unavailable: true,
      online: false,
      visible: true,
    }),
    false,
  );
  assert.equal(
    shouldRetrySessionCheck({
      unavailable: true,
      online: true,
      visible: false,
    }),
    false,
  );

  const chatApp = await readFile(
    new URL("../app/components/ChatApp.tsx", import.meta.url),
    "utf8",
  );
  assert.match(chatApp, /reason instanceof ApiError/);
  assert.match(chatApp, /isSessionAuthenticationFailure\(reason\.status\)/);
  assert.match(chatApp, /서버에 다시 연결하고 있습니다…/);
  assert.match(
    chatApp,
    /window\.addEventListener\("online", retryIfAvailable\)/,
  );
  assert.match(chatApp, /window\.addEventListener\("pageshow", onPageShow\)/);
  assert.match(
    chatApp,
    /document\.addEventListener\("visibilitychange", onVisibilityChange\)/,
  );
});

test("backs off chat reconnection while the phone is offline or hidden", () => {
  assert.equal(websocketReconnectDelay(0, 0.5), 1_000);
  assert.equal(websocketReconnectDelay(1, 0.5), 2_000);
  assert.equal(websocketReconnectDelay(4, 0.5), 16_000);
  assert.equal(websocketReconnectDelay(10, 0.5), WEBSOCKET_RECONNECT_MAX_MS);
  assert.equal(
    shouldReconnectWebSocket({
      disposed: false,
      forcedLogout: false,
      online: true,
      visible: true,
    }),
    true,
  );
  for (const state of [
    { disposed: true, forcedLogout: false, online: true, visible: true },
    { disposed: false, forcedLogout: true, online: true, visible: true },
    { disposed: false, forcedLogout: false, online: false, visible: true },
    { disposed: false, forcedLogout: false, online: true, visible: false },
  ]) {
    assert.equal(shouldReconnectWebSocket(state), false);
  }
});

test("keeps small-room voice and video calls permissioned and easy to control", async () => {
  const [
    chatApp,
    voiceCall,
    voiceCallOverlay,
    callChoiceSheet,
    notificationSound,
    globalStyles,
  ] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/voiceCall.ts", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/VoiceCallOverlay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/CallChoiceSheet.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/notificationSound.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(chatApp, /payload\.event\.startsWith\("voice_call_"\)/);
  assert.match(chatApp, /startActiveRoomCall\(activeRoom, "audio"\)/);
  assert.match(chatApp, /startActiveRoomCall\(activeRoom, "video"\)/);
  assert.match(chatApp, /activeRoom\.kind === "custom"/);
  assert.match(chatApp, />\s*통화\s*</);
  assert.match(callChoiceSheet, /음성통화/);
  assert.match(callChoiceSheet, /영상통화/);
  assert.match(voiceCall, /\/api\/voice-calls\/config/);
  assert.match(voiceCall, /max_video_participants/);
  assert.match(voiceCall, /navigator\.mediaDevices\.getUserMedia/);
  assert.match(voiceCall, /callMode === "video"[\s\S]*?: false,/);
  assert.match(voiceCall, /cameraConstraints\("user"\)/);
  assert.match(voiceCall, /facingMode/);
  assert.match(voiceCall, /track\.enabled = !next/);
  assert.match(voiceCall, /videoSender\.replaceTrack\(replacementTrack\)/);
  assert.match(voiceCall, /echoCancellation:\s*true/);
  assert.match(voiceCall, /noiseSuppression:\s*true/);
  assert.match(voiceCall, /new RTCPeerConnection/);
  assert.match(voiceCall, /PEER_CONNECTION_TIMEOUT_MS = 25_000/);
  assert.match(voiceCall, /PEER_RECONNECT_TIMEOUT_MS = 10_000/);
  assert.match(voiceCall, /통화 중계\(TURN\) 설정이 필요합니다/);
  assert.match(voiceCall, /connection\.connectionState === "failed"/);
  assert.match(voiceCall, /connection\.connectionState === "disconnected"/);
  assert.match(voiceCall, /상대방이 받음 · 연결 중…/);
  assert.match(voiceCall, /voice_call_invite/);
  assert.match(voiceCall, /voice_call_cancel/);
  assert.match(voiceCall, /voice_call_mode/);
  assert.match(voiceCall, /source_event/);
  assert.match(voiceCall, /상대방과 연결된 뒤 통화 종류를 바꿔 주세요/);
  assert.match(voiceCall, /통화 연결이 안정된 뒤 다시 눌러 주세요/);
  assert.match(voiceCall, /ensureLocalVideoTrack/);
  assert.match(voiceCall, /suspendLocalVideo/);
  assert.match(voiceCall, /renegotiatePeers/);
  assert.match(voiceCall, /videoSender\.replaceTrack\(null\)/);
  assert.match(chatApp, /sendRealtimeEventWhenConnected/);
  assert.match(chatApp, /timeoutMs = 8_000/);
  assert.match(chatApp, /socketRef\.current\?\.readyState !== WebSocket\.OPEN/);
  assert.match(
    voiceCall,
    /await sendRealtimeEventWhenConnected\(\{[\s\S]*?voice_call_join/,
  );
  assert.match(voiceCall, /voice_call_signal/);
  assert.match(voiceCall, /presentIncomingCall/);
  assert.match(voiceCall, /incomingRef\.current = null/);
  assert.doesNotMatch(voiceCall, /MediaRecorder/);
  assert.match(voiceCallOverlay, /받기/);
  assert.match(voiceCallOverlay, /거절/);
  assert.match(voiceCallOverlay, /음소거/);
  assert.match(voiceCallOverlay, /통화 종료/);
  assert.match(voiceCallOverlay, /영상통화/);
  assert.match(voiceCallOverlay, /카메라 끄기/);
  assert.match(voiceCallOverlay, /앞·뒤 전환/);
  assert.match(voiceCallOverlay, /speakerphoneOn/);
  assert.match(voiceCallOverlay, /스피커/);
  assert.match(voiceCallOverlay, /수화기/);
  assert.match(voiceCallOverlay, /영상으로/);
  assert.match(voiceCallOverlay, /음성으로/);
  assert.match(voiceCallOverlay, /전환 중…/);
  assert.match(voiceCallOverlay, /className="video-call-stage"/);
  assert.match(voiceCallOverlay, /<LocalVideo/);
  assert.match(voiceCallOverlay, /영상 연결 중…/);
  assert.match(voiceCallOverlay, /카메라 준비 중…/);
  assert.match(globalStyles, /video-call-stage-in/);
  assert.match(globalStyles, /video-call-preparing/);
  assert.match(notificationSound, /startIncomingCallRingtone/);
  assert.match(notificationSound, /startOutgoingCallTone/);
  assert.match(notificationSound, /stopCallTone/);
  assert.match(notificationSound, /frequency:\s*440/);
  assert.match(notificationSound, /frequency:\s*480/);
  assert.match(voiceCall, /void startIncomingCallRingtone\(\)/);
  assert.match(voiceCall, /void startOutgoingCallTone\(\)/);
  assert.doesNotMatch(voiceCall, /playIncomingCallNotification/);
  assert.match(globalStyles, /\.voice-call-layer/);
  assert.match(globalStyles, /\.voice-call-bar/);
  assert.match(globalStyles, /\.video-call-stage/);
  assert.match(globalStyles, /\.video-call-grid/);
  assert.match(globalStyles, /\.video-call-local/);
  assert.match(globalStyles, /\.video-call-controls/);
});

test("keeps an incoming call above any open staff-room dialog", async () => {
  const styles = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  const readZIndex = (selector) => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const block = styles.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
    assert.ok(block, `${selector} style block must exist`);
    const value = block[1].match(/z-index:\s*(\d+)/);
    assert.ok(value, `${selector} must define z-index`);
    return Number(value[1]);
  };

  assert.ok(
    readZIndex(".voice-call-layer") > readZIndex(".staff-room-layer"),
    "the incoming-call actions must not be hidden behind the staff-room dialog",
  );
});

async function render(pathname = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${pathname}`, {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the MESIL_Chat shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>MESIL_Chat<\/title>/i);
  assert.match(html, /접속 방법을 확인하고 있습니다/);
  assert.doesNotMatch(
    html,
    /codex-preview|react-loading-skeleton|taking shape/i,
  );
});

test("server-renders the noindex reviewer experience", async () => {
  const response = await render("/reviewer");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /심사위원 체험/);
  assert.match(html, /요양보호사로 체험하기/);
  assert.match(html, /사회복지사로 체험하기/);
  assert.match(html, /name="robots"[^>]+noindex/i);
  assert.doesNotMatch(
    html,
    /로그인 아이디|공통 비밀번호|임시 비밀번호|name=["']password["']/i,
  );
});

test("keeps app updates automatic and protects message drafts", async () => {
  const [layout, updateManager, globalStyles, serviceWorker] =
    await Promise.all([
      readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
      readFile(
        new URL("../app/components/AppUpdateManager.tsx", import.meta.url),
        "utf8",
      ),
      readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
      readFile(
        new URL("../public/mesil-chat-sw-v6.js", import.meta.url),
        "utf8",
      ),
    ]);

  assert.match(layout, /<AppUpdateManager \/>/);
  assert.match(updateManager, /registration\?\.update\(\)/);
  assert.match(updateManager, /\/app-version\.json/);
  assert.match(updateManager, /cache: "no-store"/);
  assert.match(updateManager, /checkForUpdate\(true\)/);
  assert.match(updateManager, /controllerchange/);
  assert.match(updateManager, /visibilitychange/);
  assert.match(updateManager, /pageshow/);
  assert.match(updateManager, /window\.addEventListener\("online"/);
  assert.match(updateManager, /\.composer textarea/);
  assert.match(updateManager, /\.comment-form textarea/);
  assert.match(updateManager, /data-app-update-dirty/);
  assert.match(updateManager, /새 버전이 준비되었습니다\./);
  assert.match(updateManager, /지금 업데이트/);
  assert.match(updateManager, /최신 버전으로 업데이트되었습니다\./);
  assert.match(updateManager, /sessionStorage/);
  assert.match(globalStyles, /\.app-update-banner/);
  assert.match(serviceWorker, /type === "SKIP_WAITING"/);
  assert.match(serviceWorker, /mesil-chat-shell-v13/);
  assert.match(serviceWorker, /app-version\.json/);
});

test("detects app-only deployments and checks immediately after resume", () => {
  assert.equal(APP_UPDATE_CHECK_INTERVAL_MS, 5 * 60 * 1000);
  assert.equal(isNewAppBuild("build-a", "build-a"), false);
  assert.equal(isNewAppBuild("build-a", "build-b"), true);
  assert.equal(isNewAppBuild("build-a", ""), false);
  assert.equal(isNewAppBuild("build-a", null), false);

  const lastCheckedAt = 1_000;
  assert.equal(
    shouldRunAppUpdateCheck(
      lastCheckedAt,
      lastCheckedAt + APP_UPDATE_CHECK_INTERVAL_MS - 1,
      false,
    ),
    false,
  );
  assert.equal(
    shouldRunAppUpdateCheck(lastCheckedAt, lastCheckedAt + 10_000, true),
    true,
  );
  assert.equal(
    shouldRunAppUpdateCheck(
      lastCheckedAt,
      lastCheckedAt + APP_UPDATE_CHECK_INTERVAL_MS,
      false,
    ),
    true,
  );
});

test("defers automatic reload until an unsaved draft can be preserved", () => {
  assert.equal(decideAppUpdateAction(false, false), "reload");
  assert.equal(decideAppUpdateAction(true, false), "defer");
  assert.equal(decideAppUpdateAction(true, true, false), "cancel");
  assert.equal(decideAppUpdateAction(true, true, true), "reload");
});

test("serves the current app build id without caching", async () => {
  const response = await render("/app-version.json");
  assert.equal(response.status, 200);
  assert.match(
    response.headers.get("content-type") ?? "",
    /^application\/json\b/i,
  );
  assert.match(response.headers.get("cache-control") ?? "", /no-store/i);
  const payload = await response.json();
  assert.equal(typeof payload.build_id, "string");
  assert.ok(payload.build_id.length > 0);

  const assetNames = await readdir(
    new URL("../dist/client/assets/", import.meta.url),
  );
  const updateManagerAsset = assetNames.find(
    (name) => name.startsWith("AppUpdateManager-") && name.endsWith(".js"),
  );
  assert.ok(updateManagerAsset, "빌드된 업데이트 관리자 파일이 있어야 합니다.");
  const updateManagerBundle = await readFile(
    new URL(`../dist/client/assets/${updateManagerAsset}`, import.meta.url),
    "utf8",
  );
  assert.ok(
    updateManagerBundle.includes(payload.build_id),
    "서버와 브라우저 화면의 빌드 ID가 같아야 합니다.",
  );
});

test("plays wake catch-up sound only for messages from the last minute", () => {
  const now = Date.parse("2026-07-30T12:00:00.000Z");

  assert.equal(WAKE_NOTIFICATION_MAX_AGE_MS, 60_000);
  assert.equal(isRecentWakeNotification("2026-07-30T11:59:30.000Z", now), true);
  assert.equal(
    isRecentWakeNotification("2026-07-30T11:58:59.999Z", now),
    false,
  );
  assert.equal(
    isRecentWakeNotification("2026-07-30T11:40:00.000Z", now),
    false,
  );
  assert.equal(isRecentWakeNotification("not-a-date", now), false);
});

test("keeps staff-created rooms, message recall, and audited admin review visible", async () => {
  const [
    chatApp,
    staffRoomPanel,
    staffContactSheet,
    adminReview,
    messageDetail,
    navigation,
    styles,
  ] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/StaffRoomPanel.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/StaffContactSheet.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AdminConversationReview.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/MessageDetailOverlay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/navigationHistory.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(chatApp, /직원 대화·통화/);
  assert.match(chatApp, /\/api\/messages\/\$\{message\.id\}\/recall/);
  assert.match(chatApp, /작성자가 회수한 메시지입니다/);
  assert.doesNotMatch(chatApp, /void deleteMessage\(message\)/);
  assert.match(staffRoomPanel, /\/api\/staff-rooms/);
  assert.match(staffRoomPanel, /함께할 직원/);
  assert.match(staffRoomPanel, /expectedMemberIds/);
  assert.match(staffRoomPanel, /: "대화"/);
  assert.match(staffRoomPanel, /음성통화/);
  assert.match(staffRoomPanel, /영상통화/);
  assert.match(staffContactSheet, /1:1채팅/);
  assert.match(staffContactSheet, />\s*통화\s*</);
  assert.match(chatApp, /className="message-sender-name"/);
  assert.match(chatApp, /member_ids: \[target\.id\]/);
  assert.match(chatApp, /openDirectStaffContact\("chat"\)/);
  assert.match(staffRoomPanel, /방장 넘기기/);
  assert.match(staffRoomPanel, /대화방 나가기/);
  assert.match(adminReview, /\/api\/admin\/conversation-access/);
  assert.match(adminReview, /15분간 열람/);
  assert.match(adminReview, /감사기록에 남습니다/);
  assert.match(adminReview, /reason instanceof ApiError/);
  assert.match(adminReview, /\[401, 403\]\.includes\(reason\.status\)/);
  assert.match(
    adminReview,
    /setAccess\(\{ active: false, expires_at: null \}\)/,
  );
  assert.match(
    messageDetail,
    /loadedDetail\?\.requestKey === detailRequestKey \? loadedDetail\.value : null/,
  );
  assert.match(navigation, /staffRoomOpen/);
  assert.match(styles, /\.staff-room-panel/);
  assert.match(styles, /\.admin-conversation-review/);
});

test("keeps AI help tied to an active message and Android Back history", async () => {
  const [chatApp, aiAssist, messageDetail, navigation, types, styles] =
    await Promise.all([
      readFile(
        new URL("../app/components/ChatApp.tsx", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL("../app/components/AiAssistPanel.tsx", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL("../app/components/MessageDetailOverlay.tsx", import.meta.url),
        "utf8",
      ),
      readFile(new URL("../app/navigationHistory.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    ]);

  assert.match(aiAssist, /\/api\/ai-assist\/config/);
  assert.match(
    aiAssist,
    /\/api\/messages\/\$\{message\.id\}\/ai-conversations\/latest/,
  );
  assert.match(aiAssist, /apiFetch<AiAssistConversation \| null>/);
  assert.match(aiAssist, /window\.setInterval/);
  assert.match(aiAssist, /isWorking \? 2_000 : 5_000/);
  assert.match(
    aiAssist,
    /scrollIntoView\(\{ behavior: "smooth", block: "nearest" \}\)/,
  );
  assert.doesNotMatch(aiAssist, /reason instanceof ApiError/);
  assert.match(
    aiAssist,
    /\/api\/messages\/\$\{message\.id\}\/ai-conversations/,
  );
  assert.match(
    aiAssist,
    /\/api\/ai-conversations\/\$\{conversation\.id\}\/turns/,
  );
  assert.match(aiAssist, /\/api\/ai-turns\/\$\{turn\.id\}\/share/);
  assert.match(aiAssist, /관련 과거기록 찾기/);
  assert.match(aiAssist, /위험·누락 확인/);
  assert.match(aiAssist, /민감자료 외부 전송 기본 차단/);
  assert.match(aiAssist, /processingLocationLabels\[turn\.processing_location\]/);
  assert.match(aiAssist, /turn\.external_transmission/);
  assert.doesNotMatch(aiAssist, /AI 판독: Codex/);
  assert.doesNotMatch(aiAssist, /개인정보 포함 여부 확인/);
  assert.doesNotMatch(aiAssist, /config\.external_real_image_data_enabled\) \?/);
  assert.match(aiAssist, /!config\.whisper_ready/);
  assert.match(aiAssist, /음성 받아쓰기는 현재 준비 중입니다/);
  assert.match(aiAssist, /service_context: serviceContext \|\| null/);
  assert.match(aiAssist, /inferServiceContext\(message\)/);
  assert.doesNotMatch(aiAssist, /이미지 판독 전에 시설·주간보호·방문요양/);
  assert.match(aiAssist, /새 분석 시작/);
  assert.match(
    aiAssist,
    /const startNewConversation = !conversation \|\| newAnalysisMode/,
  );
  assert.match(aiAssist, /!startNewConversation && conversation/);
  assert.match(aiAssist, /selectedAudioId/);
  assert.match(aiAssist, /ALL_AUDIO_FILES/);
  assert.match(aiAssist, /전체 \{audioFiles\.length\}개 종합 정리/);
  assert.match(aiAssist, /전체 음성 종합 정리/);
  assert.match(aiAssist, /pendingAudioTranscriptNames/);
  assert.match(aiAssist, /파일 순서와 파일명별로 구분/);
  assert.match(aiAssist, /첨부된 사진 \{images\.length\}장을 함께 분석합니다/);
  assert.match(aiAssist, /\? images\[0\]\?\.id/);
  assert.match(aiAssist, /audioFiles\.map\(\(attachment\)/);
  assert.doesNotMatch(aiAssist, /selectedAttachmentId/);
  assert.match(
    aiAssist,
    /syncGeneration !== conversationSyncGenerationRef\.current/,
  );
  assert.match(
    aiAssist,
    /if \(restoringConversation \|\| newAnalysisMode\) return/,
  );
  assert.match(aiAssist, /이전 내용 보기/);
  assert.match(aiAssist, /추가 질문/);
  assert.match(aiAssist, /현재 이용자 명단 대조 후보/);
  assert.match(aiAssist, /자동 확정이 아니므로 원문을 확인/);
  assert.match(aiAssist, /turn\.service_context_notice/);
  assert.match(aiAssist, /aria-live=/);
  assert.match(aiAssist, /event\.key !== "Escape"/);
  assert.match(aiAssist, /분석 엔진:/);
  assert.match(aiAssist, /deidentified_confirmed_by_user: deidentifiedConfirmed/);
  assert.match(aiAssist, /비식별 자료임을 확인하고 허용된 외부 AI 사용/);
  assert.match(aiAssist, /직접 식별정보가[\s\S]*?외부 전송을 차단/);
  assert.doesNotMatch(aiAssist, /turn\.provider, turn\.model/);
  assert.doesNotMatch(messageDetail, /AI에게 물어보기/);
  assert.match(messageDetail, /detail-action-footer/);
  assert.doesNotMatch(messageDetail, /AI 업무 도움/);
  assert.doesNotMatch(messageDetail, /AI로 확인/);
  assert.doesNotMatch(messageDetail, /onOpenAiAssist/);
  assert.match(messageDetail, /판독문 보기·수정/);
  assert.match(messageDetail, /받아쓰기 보기·수정/);
  assert.match(messageDetail, /관련 어르신 \$\{detailResidents\.length\}명/);
  assert.match(
    messageDetail,
    /어르신 확인 후보 \$\{candidateResidentCount\}명/,
  );
  assert.match(messageDetail, /ResidentLinkReview/);
  assert.match(messageDetail, /확인됨 \{confirmedResidentCount\}명/);
  assert.match(messageDetail, /attachments\.filter\(\(attachment\) =>/);
  assert.match(
    messageDetail,
    /\/api\/messages\/\$\{messageId\}\/image-text-extractions/,
  );
  assert.match(messageDetail, /JSON\.stringify\(\{ force \}\)/);
  assert.match(messageDetail, /runImageBatchAction\(true\)/);
  assert.match(
    messageDetail,
    /남은 이미지 \$\{retryableImageAttachments\.length\}장 글자 읽기/,
  );
  assert.match(
    messageDetail,
    /판독문 \$\{completedImageCount\}장 \$\{suffix\}/,
  );
  assert.doesNotMatch(chatApp, /canUseAi=\{!me\.is_reviewer_session\}/);
  assert.match(
    chatApp,
    /aiAssistMessage &&[\s\S]*?me\.role === "admin" \|\| me\.can_process_records[\s\S]*?!me\.is_reviewer_session \|\| mentorFullReview/,
  );
  assert.doesNotMatch(chatApp, /function openAiAssistFromDetail/);
  assert.doesNotMatch(chatApp, /aiAssistOpen: true/);
  assert.doesNotMatch(chatApp, /onOpenAiAssist=/);
  assert.match(chatApp, /onShared=\{refreshRoomAfterAiShare\}/);
  assert.match(navigation, /aiAssistOpen\?: true/);
  assert.match(navigation, /aiAssistOpen: navigation\.aiAssistOpen === true/);
  assert.match(
    chatApp,
    /function closeAiAssist[\s\S]*?window\.history\.back\(\)/,
  );
  assert.match(
    messageDetail,
    /loadedDetail\?\.requestKey === detailRequestKey \? loadedDetail\.value : null/,
  );
  assert.match(types, /status: AiAssistTurn\["status"\]/);
  assert.match(types, /fallback_provider: "local_deterministic"/);
  assert.match(
    types,
    /resident_name_candidates\?: AiAssistResidentNameCandidate\[\]/,
  );
  assert.match(types, /export type AiAssistServiceContext/);
  assert.match(types, /external_real_image_data_enabled: boolean/);
  assert.match(types, /service_context_notice\?: string \| null/);
  assert.match(styles, /\.ai-assist-layer/);
  assert.match(styles, /\.ai-assist-panel/);
  assert.match(styles, /\.ai-assist-deidentified-confirmation/);
  assert.doesNotMatch(styles, /\.ai-assist-service-context/);
  assert.match(styles, /\.ai-assist-resident-candidates/);
  assert.match(
    styles,
    /@media \(max-width: 720px\)[\s\S]*?\.ai-assist-panel[\s\S]*?max-height: 100dvh/,
  );
});

test("guards ten attachments and shows the selected total size", async () => {
  const [chatApp, attachmentFormats, attachmentDisplay, styles] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/attachmentFormats.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/components/AttachmentDisplay.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(chatApp, /const MAX_ATTACHMENTS_PER_MESSAGE = 10/);
  assert.match(chatApp, /const MAX_ATTACHMENT_BYTES = 30 \* 1024 \* 1024/);
  assert.match(
    chatApp,
    /const MAX_ATTACHMENTS_TOTAL_BYTES = 100 \* 1024 \* 1024/,
  );
  assert.match(chatApp, /attachmentSelectionError\(selected\)/);
  assert.match(chatApp, /attachmentSelectionError\(selectedFiles\)/);
  assert.match(chatApp, /mergeAttachmentSelections\(files, added\)/);
  assert.match(chatApp, /event\.currentTarget\.value = ""/);
  assert.match(chatApp, /사진·음성·파일/);
  assert.match(chatApp, /aria-label="선택한 첨부파일"/);
  assert.match(chatApp, /첨부 취소/);
  assert.match(chatApp, />\s*빼기\s*</);
  assert.doesNotMatch(chatApp, /slice\(0, 4\)/);
  assert.match(chatApp, /총 \{totalAttachmentMegabytes\(files\)\}MB/);
  assert.match(chatApp, /파일당 최대 30MB/);
  assert.match(chatApp, /파일 전체 용량은 100MB/);
  assert.match(chatApp, /현재 \$\{selectedFiles\.length\}개를 선택했습니다/);
  assert.match(chatApp, /글자도 읽기/);
  for (const extension of ["xlsx", "xls", "hwp", "hwpx", "docx", "doc", "pptx", "ppt", "txt", "csv"]) {
    assert.match(attachmentFormats, new RegExp(`\\.${extension}`));
  }
  assert.match(attachmentFormats, /매크로 문서\(XLSM·DOCM·PPTM\)/);
  assert.match(attachmentFormats, /이중 확장자로 위장된 파일/);
  assert.match(chatApp, /CHAT_ATTACHMENT_ACCEPT/);
  assert.doesNotMatch(chatApp, /SUPPORTED_ATTACHMENT_GUIDE|file-picker-supported-formats/);
  assert.match(attachmentDisplay, /documentAttachmentInfo/);
  assert.match(attachmentDisplay, /document-file-icon/);
  assert.match(attachmentDisplay, /다운로드/);
  assert.match(styles, /\.selected-file-list/);
  assert.match(styles, /\.compact-document-download/);
  assert.doesNotMatch(chatApp, /판독 선택됨|보고서 판독/);
});

test("keeps attachment messages and mobile actions simple and independent", async () => {
  const [
    chatApp,
    attachmentDisplay,
    residentPicker,
    messageDetail,
    staffRoom,
    presentation,
    styles,
  ] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/ResidentPickerDialog.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/MessageDetailOverlay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/StaffRoomPanel.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/messagePresentation.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(presentation, /파일을 첨부했습니다/);
  assert.match(presentation, /보고서 이미지를 첨부했습니다/);
  assert.match(presentation, /LONG_MESSAGE_CHARACTER_LIMIT = 500/);
  assert.match(presentation, /LONG_MESSAGE_LINE_LIMIT = 8/);
  assert.match(presentation, /isLongMessageBody/);
  assert.match(presentation, /사진 \$\{counts\.image\}장/);
  assert.match(presentation, /음성 \$\{counts\.audio\}개/);
  assert.match(chatApp, /messageDisplayBody\(\s*message\.body/);
  assert.match(chatApp, /className="message-more-button"/);
  assert.match(chatApp, /aria-label="메시지 메뉴 열기"/);
  assert.match(chatApp, /onContextMenu=/);
  assert.match(chatApp, /beginMessageActionLongPress/);
  assert.match(chatApp, />\s*답장\s*</);
  assert.match(chatApp, /me\.role === "admin"[\s\S]*?>\s*코멘트\s*</);
  assert.match(chatApp, /replyTarget/);
  assert.match(chatApp, /reply_to_message_id/);
  assert.match(chatApp, /다른 방 전달/);
  assert.match(chatApp, /대화내용 복사/);
  assert.match(chatApp, /다른 앱 공유/);
  assert.match(chatApp, /copySelectionActive/);
  assert.match(chatApp, /toggleCopySelection/);
  assert.match(chatApp, /copySelectedConversation/);
  assert.match(chatApp, /shareSelectedConversation/);
  assert.match(chatApp, /선택한 대화 \{copySelectionIds\.length\}개/);
  assert.match(chatApp, /선택한 대화 복사/);
  assert.match(chatApp, /선택한 대화 다른 앱 공유/);
  assert.match(
    chatApp,
    /공유 창을 열 수 없어 대화 문장은 복사하고 첨부파일은 저장했습니다/,
  );
  assert.doesNotMatch(
    chatApp,
    /reason instanceof Error\s*\? reason\.message\s*:\s*"다른 앱 공유를 시작하지 못했습니다\."/,
  );
  assert.match(chatApp, /prepareAttachmentShare/);
  assert.match(chatApp, /savePreparedAttachment/);
  assert.doesNotMatch(chatApp, /답글·상세/);
  assert.doesNotMatch(chatApp, /파일 열기·저장/);
  assert.doesNotMatch(chatApp, /글자 판독 보기/);
  assert.doesNotMatch(chatApp, /음성 받아쓰기 보기/);
  assert.doesNotMatch(chatApp, /대화 캡처 시작/);
  assert.doesNotMatch(chatApp, /공지로 다시 작성/);
  assert.doesNotMatch(chatApp, /전달·공유/);
  assert.doesNotMatch(messageDetail, /initialAction/);
  assert.match(chatApp, /message-comment-dialog/);
  assert.match(chatApp, /message-forward-dialog/);
  assert.doesNotMatch(attachmentDisplay, /portrait-recording-workspace-note/);
  assert.match(attachmentDisplay, /attachment-confirmed-text/);
  assert.match(attachmentDisplay, /latest_confirmed_text/);
  assert.doesNotMatch(
    styles,
    /\.care-planning-launcher,\s*\.new-admission-form-workspace,\s*\.form-workspace-editor/,
  );
  assert.match(
    styles,
    /@media \(max-width: 720px\)[\s\S]*?\.care-planning-pc-only-notice[\s\S]*?display: block/,
  );
  assert.match(styles, /\.message-copy-toolbar/);
  assert.match(styles, /\.message-copy-select/);
  assert.match(
    styles,
    /@media \(max-width: 720px\)[\s\S]*?\.message-action-menu button[\s\S]*?min-height: 48px/,
  );
  assert.match(chatApp, /function LatestCommentPreview/);
  assert.match(chatApp, /me\.role === "admin"[\s\S]*?<LatestCommentPreview/);
  assert.match(chatApp, /최신 답글 · \{latestComment\.author_name\}/);
  assert.match(chatApp, /latest_comment: payload\.comment/);
  assert.match(chatApp, /새 답글 \$\{message\.unread_comment_count\}개/);
  assert.match(styles, /\.latest-comment-preview-body/);
  assert.match(styles, /-webkit-line-clamp: 2/);
  assert.match(chatApp, /const longMessage = isLongMessageBody\(displayBody\)/);
  assert.match(chatApp, /className="bubble-text long-message-preview"/);
  assert.match(
    chatApp,
    /className="long-message-preview notice-long-message-preview"/,
  );
  assert.match(chatApp, /aria-label="긴 글 전체 내용 열기"/);
  assert.doesNotMatch(chatApp, />\s*전체보기\s*</);
  assert.doesNotMatch(chatApp, /!longMessage \?/);
  assert.doesNotMatch(chatApp, /open-detail-hint/);
  assert.doesNotMatch(chatApp, /<button\s+className="message-bubble/);
  assert.match(chatApp, /사진·음성·파일/);
  assert.doesNotMatch(chatApp, /사진·음성·파일 여러 개/);
  assert.match(chatApp, /추가 설정/);
  assert.match(chatApp, /composerOptionsOpen/);
  assert.match(
    chatApp,
    /\(me\.role === "admin" \|\| me\.can_process_records\)[\s\S]*?resident-picker-button/,
  );
  assert.match(chatApp, /어르신을 먼저 고르지 않아도 바로 보낼 수 있습니다/);
  assert.match(chatApp, /보낸 직원이나 담당자가 나중에 확인할 수 있습니다/);
  assert.doesNotMatch(chatApp, /글 성격 선택/);
  assert.doesNotMatch(chatApp, /setMessageNature/);
  assert.match(chatApp, /내용은 자동으로 분류됩니다/);
  const composerKeyHandler = chatApp.match(
    /<textarea[\s\S]*?value=\{messageBody\}[\s\S]*?onKeyDown=\{\(event\) => \{[\s\S]*?\}\}[\s\S]*?maxLength=\{2000\}/,
  )?.[0];
  assert.ok(composerKeyHandler, "main composer keyboard handler should be present");
  assert.match(composerKeyHandler, /!event\.nativeEvent\.isComposing/);
  assert.doesNotMatch(attachmentDisplay, /눌러서 크게 보기/);
  assert.match(attachmentDisplay, /function handleImageBackdropClick/);
  assert.match(attachmentDisplay, /event\.stopPropagation\(\)/);
  assert.match(attachmentDisplay, /image-lightbox-help-mobile/);
  assert.match(attachmentDisplay, /IMAGE_HISTORY_STATE_KEY/);
  assert.match(attachmentDisplay, /synchronizeImageWithHistory/);
  assert.match(
    attachmentDisplay,
    /const closeImage[\s\S]*?IMAGE_HISTORY_STATE_KEY[\s\S]*?window\.history\.back\(\)/,
  );
  assert.match(messageDetail, /window\.confirm/);
  assert.match(messageDetail, /<details className="mobile-action-more">/);
  assert.match(staffRoom, /직원을 먼저 선택하세요/);
  assert.match(staffRoom, /staff-room-start-actions/);
  assert.match(styles, /\.bubble-attachments\.single/);
  assert.match(
    styles,
    /\.detail-attachments\s*>\s*:only-child\s*\{[\s\S]*?grid-column:\s*1\s*\/\s*-1/,
  );
  assert.match(
    styles,
    /\.long-message-preview-body[\s\S]*?-webkit-line-clamp:\s*8/,
  );
  assert.doesNotMatch(styles, /\.long-message-more/);
  assert.match(styles, /\.long-message-preview::after[\s\S]*?content:\s*"···"/);
  assert.match(
    styles,
    /\.composer-tools \.file-picker,[\s\S]*?min-height:\s*48px/,
  );
  assert.match(styles, /\.composer-optional-tools \{[\s\S]*?flex:\s*1 0 100%/);
  assert.match(styles, /\.resident-picker-button \{[\s\S]*?min-width:\s*150px/);
  assert.match(styles, /\.message-nature-select \{[\s\S]*?flex:\s*1 1 130px/);
  assert.match(
    styles,
    /\.image-lightbox-controls button[\s\S]*?min-width:\s*44px/,
  );
  assert.match(residentPicker, /선택하지 않아도 보고 내용에서 이름을 찾습니다/);
  assert.match(residentPicker, /type="checkbox"/);
  assert.match(chatApp, /residentPickerOpen: true/);
  assert.match(attachmentDisplay, /EXTRACTION_EDITOR_HISTORY_STATE_KEY/);
  assert.match(attachmentDisplay, /이미지 판독문 수정/);
  assert.match(attachmentDisplay, /음성 받아쓰기 수정/);
  assert.match(attachmentDisplay, /5초 전/);
  assert.match(attachmentDisplay, /NAME_REVIEW_TOKENS/);
  assert.match(attachmentDisplay, /\["이름확인필요", "이름 확인 필요"\]/);
  assert.match(
    attachmentDisplay,
    /다음 이름확인필요 \(\{nameReviewRanges\.length\}\)/,
  );
  assert.match(
    attachmentDisplay,
    /textarea\.focus\(\{ preventScroll: true \}\)/,
  );
  assert.match(
    attachmentDisplay,
    /textarea\.setSelectionRange\(range\.start, range\.end\)/,
  );
  assert.match(attachmentDisplay, /textarea\.scrollTop/);
  assert.match(
    attachmentDisplay,
    /onDoubleClick=\{handleNameReviewDoubleClick\}/,
  );
  assert.doesNotMatch(attachmentDisplay, /handleNameReviewTouch/);
  assert.doesNotMatch(attachmentDisplay, /NAME_REVIEW_LONG_PRESS_MS/);
  assert.doesNotMatch(attachmentDisplay, /다음 이름 확인 필요/);
  assert.match(
    styles,
    /\.extraction-editor-text-header button[\s\S]*?min-height:\s*42px/,
  );
  assert.match(styles, /\.extraction-editor-main[\s\S]*?grid-template-columns/);
  assert.match(styles, /@media \(max-width: 780px\)[\s\S]*?40dvh/);
});

test("keeps product metadata, security flow, and PWA assets aligned", async () => {
  const [
    page,
    layout,
    globalStyles,
    api,
    loginScreen,
    reviewerPage,
    reviewerExperience,
    reviewerLanding,
    chatApp,
    periodWorkDesk,
    messageDetail,
    attachmentDisplay,
    pdfCanvasViewer,
    roomSearch,
    aiAssist,
    securityPanel,
    adminDrawer,
    notificationSound,
    pushNotifications,
    notificationPanel,
    packageJson,
    manifestText,
    serviceWorker,
    legacyManifestText,
    legacyServiceWorker,
    sharedTarget,
    attachmentShare,
  ] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/api.ts", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/LoginScreen.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/reviewer/page.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/ReviewerExperience.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/reviewerLanding.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/PeriodWorkDesk.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/MessageDetailOverlay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/PdfCanvasViewer.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/RoomSearchOverlay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AiAssistPanel.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/SecurityPanel.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AdminDrawer.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/notificationSound.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/pushNotifications.ts", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/NotificationSoundPanel.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(
      new URL("../public/mesil-chat.webmanifest", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../public/mesil-chat-sw-v6.js", import.meta.url), "utf8"),
    readFile(
      new URL("../public/manifest.webmanifest", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../public/sw.js", import.meta.url), "utf8"),
    readFile(new URL("../app/sharedTarget.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/attachmentShare.ts", import.meta.url), "utf8"),
  ]);

  const manifest = JSON.parse(manifestText);
  const legacyManifest = JSON.parse(legacyManifestText);
  assert.match(page, /<ChatApp \/>/);
  assert.match(layout, /MESIL_Chat/);
  assert.match(layout, /interactiveWidget:\s*"resizes-content"/);
  assert.match(chatApp, /window\.visualViewport/);
  assert.match(chatApp, /--app-viewport-height/);
  assert.match(
    globalStyles,
    /height:\s*var\(--app-viewport-height,\s*100dvh\)/,
  );
  assert.match(globalStyles, /\.composer\s*\{[\s\S]*?flex:\s*0 0 auto/);
  assert.match(chatApp, /must_change_password/);
  assert.match(chatApp, /loginNotice/);
  assert.match(chatApp, /파일을 보내는 중/);
  assert.match(chatApp, /서버에 안전하게 저장하는 중/);
  assert.match(
    chatApp,
    /data-app-update-dirty=\{[\s\S]*?messageBody\.trim\(\)\.length > 0[\s\S]*?files\.length > 0[\s\S]*?isSending/,
  );
  assert.match(api, /XMLHttpRequest/);
  assert.match(api, /apiUpload/);
  assert.match(api, /export function apiErrorMessage/);
  assert.match(api, /Array\.isArray\(detail\)/);
  assert.match(api, /candidate\.msg/);
  assert.match(api, /입력값을 확인해 주세요/);
  assert.doesNotMatch(api, /if \(payload\.detail\) message = payload\.detail/);
  assert.match(api, /"\/api\/auth\/login"/);
  assert.match(api, /"\/api\/auth\/logout"/);
  assert.match(loginScreen, /sessionNotice/);
  assert.match(loginScreen, /직원 전용/);
  assert.match(loginScreen, /업무에 필요한 최소한의 어르신 정보/);
  assert.doesNotMatch(loginScreen, /내부 개발·시험/);
  assert.doesNotMatch(loginScreen, /href="\/reviewer"/);
  assert.doesNotMatch(loginScreen, /심사위원 체험 안내/);
  assert.match(reviewerPage, /index:\s*false/);
  assert.match(reviewerPage, /<ReviewerExperience \/>/);
  assert.match(reviewerExperience, /요양보호사로 체험하기/);
  assert.match(reviewerExperience, /사회복지사로 체험하기/);
  assert.match(reviewerExperience, /실시간 채팅 추가 체험/);
  assert.match(reviewerExperience, /\/api\/auth\/reviewer-session/);
  assert.match(reviewerExperience, /실제 개인정보는 포함되어 있지 않습니다/);
  assert.doesNotMatch(
    reviewerExperience,
    /로그인 아이디|공통 비밀번호|임시 비밀번호|type=["']password["']/i,
  );
  assert.match(reviewerLanding, /window\.sessionStorage/);
  assert.match(reviewerLanding, /mesil-reviewer-destination/);
  assert.match(api, /"\/api\/auth\/reviewer-session"/);
  assert.match(chatApp, /is_reviewer_session/);
  assert.match(chatApp, /readReviewerLanding/);
  assert.match(chatApp, /setWorkdeskOpen\(true\)/);
  assert.match(chatApp, /체험 선택으로 돌아가기/);
  assert.match(globalStyles, /\.reviewer-page/);
  assert.match(globalStyles, /@media \(max-width: 390px\)/);
  assert.match(securityPanel, /\/api\/auth\/password/);
  assert.match(securityPanel, /\/api\/auth\/username-availability/);
  assert.match(securityPanel, /new_username: normalizedUsername/);
  assert.match(
    securityPanel,
    /new_password: passwordChanged \? newPassword : null/,
  );
  assert.match(securityPanel, /useState\(user\.username\)/);
  assert.match(securityPanel, /setNewUsername\(updated\.username\)/);
  assert.match(
    securityPanel,
    /useEffect\(\(\) => \{\s*if \(mandatory \|\| reviewOnly\) return;[\s\S]*?\}, \[loadSessions, mandatory, reviewOnly\]\)/,
  );
  assert.match(
    securityPanel,
    /onUserChanged\(updated\);\s*if \(mandatory\) \{\s*onClose\(\);\s*return;\s*\}\s*await loadSessions\(\)/,
  );
  assert.match(
    securityPanel,
    /className="form-success" role="status" aria-live="polite"/,
  );
  assert.match(securityPanel, />\s*로그인 아이디\s*</);
  assert.match(securityPanel, /현재 사용 중인 아이디입니다/);
  assert.match(securityPanel, /사용할 수 있는 아이디입니다/);
  assert.match(securityPanel, /이미 다른 직원이 사용 중인 아이디입니다/);
  assert.match(securityPanel, /한글·영문·숫자로 시작/);
  assert.match(
    securityPanel,
    /pattern="\[가-힣a-zA-Z0-9\]\[가-힣a-zA-Z0-9\._-\]\{1,79\}"/,
  );
  assert.match(securityPanel, /변경 내용 저장/);
  assert.match(securityPanel, /minLength=\{6\}/);
  assert.match(securityPanel, /숫자만 또는 문자만 사용해도 되며 6자 이상/);
  assert.match(securityPanel, /data-app-update-dirty/);
  assert.match(globalStyles, /\.security-form label[\s\S]*?font-size:\s*15px/);
  assert.match(securityPanel, /\/api\/auth\/sessions/);
  assert.match(adminDrawer, /reset-password/);
  assert.match(adminDrawer, /재직 복구/);
  assert.match(adminDrawer, /직원 삭제/);
  assert.match(adminDrawer, /method: "DELETE"/);
  assert.match(adminDrawer, /active_staff_count/);
  assert.match(adminDrawer, /직위로 옮길 직원/);
  assert.match(adminDrawer, /legacyPositionByJobCode/);
  assert.doesNotMatch(
    adminDrawer,
    /facility_director:\s*"원장"/,
  );
  assert.match(adminDrawer, /!unit\.is_test_data/);
  assert.match(adminDrawer, /unit\.parent_unit_id === businessId/);
  assert.match(adminDrawer, /기존 시험·예외 연결/);
  assert.match(adminDrawer, /\/api\/position-titles/);
  assert.match(adminDrawer, /직위 추가/);
  assert.match(adminDrawer, /현재 선택 목록에서 삭제/);
  assert.match(adminDrawer, /기존 대화·기록은 지워지지 않으며/);
  assert.match(adminDrawer, /active_resident_count/);
  assert.match(adminDrawer, /\/api\/admin\/residents\/\$\{resident\.id\}/);
  assert.match(adminDrawer, /roomKindLabels/);
  assert.match(adminDrawer, /resident-sync/);
  assert.match(adminDrawer, /\["employees", "직원"\]/);
  assert.match(adminDrawer, /\["residents", "어르신"\]/);
  assert.match(adminDrawer, /\["organization", "기관 설정"\]/);
  assert.match(adminDrawer, /\["custom-room", "채팅방"\]/);
  assert.match(adminDrawer, /role="dialog"/);
  assert.match(adminDrawer, /aria-modal="true"/);
  assert.match(adminDrawer, /처음 사용할 때 생활공간·직종·직위를 기관에 맞게 확인합니다/);
  assert.match(adminDrawer, /initialTab\?: "employees" \| "organization"/);
  assert.match(adminDrawer, /useState<Tab>\(initialTab\)/);
  assert.match(adminDrawer, /unit\.parent_unit_id === business\.id/);
  assert.match(adminDrawer, /approvedOrganizationDisplayOrder/);
  assert.match(adminDrawer, /unit\.unit_type === "floor" \|\| unit\.is_test_data/);
  assert.match(adminDrawer, /생활공간·직종·직위 추가·수정/);
  assert.match(adminDrawer, /roomEditorOpen/);
  assert.match(adminDrawer, /hasUnsavedRoomChanges/);
  assert.match(adminDrawer, /confirmDiscardRoomChanges/);
  assert.match(adminDrawer, /changes\.member_ids = roomMemberIds/);
  assert.match(adminDrawer, /roomAddedMemberCount/);
  assert.match(adminDrawer, /선택한 직원 \{selectedRoomMembers\.length\}명/);
  assert.match(adminDrawer, /이번 변경 · 추가/);
  assert.match(
    adminDrawer,
    /roomKind === "custom" && roomMemberIds\.length === 0/,
  );
  assert.match(
    adminDrawer,
    /현재 참여 기준에 맞는 직원에게 즉시 다시 표시됩니다/,
  );
  assert.match(adminDrawer, /discardAdminDrafts/);
  assert.match(adminDrawer, /직원 이름이나 소속을 먼저 검색해 주세요/);
  assert.match(adminDrawer, /참여자 예외 조정/);
  assert.match(adminDrawer, /이 방에서 먼저 보여줄 어르신/);
  assert.doesNotMatch(adminDrawer, /\["staff-sync", "케어포 직원"\]/);
  assert.doesNotMatch(adminDrawer, /\["new-employee", "예외 직원 등록"\]/);
  assert.match(adminDrawer, /aria-label="직원 관리 도구"/);
  assert.match(adminDrawer, /summary>엑셀 관리/);
  assert.match(adminDrawer, /직원 직접 등록/);
  assert.match(adminDrawer, /directStaffRegistrationPayload/);
  assert.match(adminDrawer, /\/api\/admin\/staff-directory/);
  assert.match(adminDrawer, /staffIdentityPayload/);
  assert.match(
    adminDrawer,
    /\/api\/admin\/staff-directory\/\$\{selectedEmployee\.staff_id\}/,
  );
  assert.match(
    adminDrawer,
    /\/api\/admin\/staff-directory\/\$\{selectedEmployee\.staff_id\}\/terminate/,
  );
  assert.match(
    adminDrawer,
    /로그인 계정이 있으면 현재 접속도 즉시 종료됩니다/,
  );
  assert.doesNotMatch(
    adminDrawer,
    /\/api\/employees\/\$\{selectedEmployee\.login_user_id\}\/terminate/,
  );
  assert.match(adminDrawer, /로그인 계정 유무와 관계없이 기관 직원 원본에 저장/);
  assert.match(
    adminDrawer,
    /disabled=\{selectedEmployee\.employment_status !== "active"\}/,
  );
  assert.match(adminDrawer, /로그인 계정도 발급/);
  assert.match(adminDrawer, /비워두면 자동 발급/);
  assert.match(adminDrawer, /로그인 없이 직원 명단과 서비스 배정정보만 먼저 관리/);
  assert.doesNotMatch(adminDrawer, /직원 등록 및 채팅방 자동 배정/);
  assert.doesNotMatch(adminDrawer, /직원 기본정보와 로그인 여부를 직접 입력합니다/);
  assert.match(adminDrawer, /useState<EmployeeStatusFilter>\("active"\)/);
  assert.doesNotMatch(
    adminDrawer,
    /!open \|\| !\["employees", "staff-sync"\]\.includes\(tab\)/,
  );
  assert.match(adminDrawer, /어르신정보 갱신/);
  assert.match(adminDrawer, /codedSyntheticMode/);
  assert.match(
    adminDrawer,
    /본선용 DEV는 코드화된 합성 어르신 자료만 사용합니다\. 케어포 명단/,
  );
  assert.match(adminDrawer, /불러오기는 비활성화되어 있습니다/);
  assert.match(adminDrawer, /최신 어르신정보 확인/);
  assert.match(adminDrawer, /최신정보 확인/);
  assert.match(adminDrawer, /serviceResidents\.map/);
  assert.doesNotMatch(adminDrawer, /careforResidents/);
  assert.doesNotMatch(adminDrawer, /확보한 가명 직원 명단 확인/);
  assert.match(adminDrawer, /residentSyncIsFullyComplete/);
  assert.match(adminDrawer, /직원정보 연결 승인 기준/);
  assert.match(adminDrawer, /가명 시험직원/);
  assert.match(
    adminDrawer,
    /휴직은 기록과 권한자료를 보존하지만 로그인과 업무 접근은 중지/,
  );
  assert.match(adminDrawer, /최신 직원정보 확인/);
  assert.match(adminDrawer, /확인 필요한 직원/);
  assert.match(adminDrawer, /따로 확인할 직원이 없습니다/);
  assert.match(adminDrawer, /가져온 곳: 케어포/);
  assert.match(adminDrawer, /지난 갱신 기록/);
  assert.match(adminDrawer, /admin-advanced-details/);
  assert.doesNotMatch(adminDrawer, /unique_name_count/);
  assert.match(adminDrawer, /새 계정 후보/);
  assert.match(adminDrawer, /생년 근거가 없으면 관리자가 확인/);
  assert.doesNotMatch(adminDrawer, /직위 확인 필요: 시설장은/);
  assert.match(adminDrawer, /assignment-preview/);
  assert.match(adminDrawer, /staffCandidateNeedsAttention/);
  assert.match(adminDrawer, /확인이 필요한 직원/);
  assert.match(adminDrawer, /동일인 확인 필요/);
  assert.match(adminDrawer, /개발 시험 조직표의 이름만 참고/);
  assert.match(adminDrawer, /staffAssignmentReviewLabels/);
  assert.match(adminDrawer, /관리자 검토 결정안/);
  assert.match(adminDrawer, /이 판단 저장/);
  assert.match(adminDrawer, /review-drafts/);
  assert.match(adminDrawer, /두 계정은 같은 사람/);
  assert.match(adminDrawer, /모두 각각 다른 사람/);
  assert.match(adminDrawer, /일부 계정만 같은 사람/);
  assert.doesNotMatch(adminDrawer, /주 사업부/);
  assert.doesNotMatch(adminDrawer, /주 직종/);
  assert.doesNotMatch(adminDrawer, /주 직위/);
  assert.match(adminDrawer, /실제 자격 직종/);
  assert.match(adminDrawer, /required_for_review/);
  assert.match(adminDrawer, /미지정 가능/);
  assert.match(adminDrawer, /자격 직종은 선택 사항/);
  assert.match(
    adminDrawer,
    /account\.assignment_required \?\? account\.employment_status !== "retired"/,
  );
  assert.match(adminDrawer, /staff-assignment-history-notice/);
  assert.match(adminDrawer, /과거 근무 이력/);
  assert.match(
    adminDrawer,
    /현재 조직·직종·직위 배정 대상이 아니며 별도 입력하지 않습니다/,
  );
  assert.doesNotMatch(adminDrawer, /실제 자격·주 직종/);
  assert.match(adminDrawer, /current\[candidateKey\] \?\? editor/);
  assert.match(adminDrawer, /아직 저장하지 않은 판단/);
  assert.match(adminDrawer, /beforeunload/);
  assert.match(adminDrawer, /data-app-update-dirty/);
  assert.match(adminDrawer, /미완료 확인대상/);
  assert.match(adminDrawer, /전체 검토대상/);
  assert.match(adminDrawer, /expandedStaffReviewCandidateKeys/);
  assert.match(adminDrawer, /disabled=\{locked\}/);
  assert.match(adminDrawer, /staffReviewSaveInFlightRef/);
  assert.match(adminDrawer, /staffReviewEditorIsDirty\(candidate, editor\)/);
  assert.match(adminDrawer, /검토 완료/);
  assert.match(adminDrawer, /실제 직원·로그인·권한에는 반영하지 않습니다/);
  assert.match(adminDrawer, /이름·생년 확인됨/);
  assert.match(adminDrawer, /생년 원문은 표시하지 않고 동일인 구분에만 사용/);
  assert.ok(
    adminDrawer.indexOf('<div className="staff-assignment-accounts">') <
      adminDrawer.indexOf("<StaffReviewDraftForm"),
    "직원 계정 근거가 판단 저장 화면보다 먼저 보여야 합니다.",
  );
  assert.match(globalStyles, /\.staff-review-draft/);
  assert.match(globalStyles, /\.staff-review-save/);
  assert.match(
    adminDrawer,
    /carefor-staff-sync\/batches\/.*\/application-plan/,
  );
  assert.match(adminDrawer, /읽기 전용 3단계/);
  assert.match(adminDrawer, /실제 반영 전 영향 확인/);
  assert.match(adminDrawer, /개별계획 준비/);
  assert.match(adminDrawer, /설계 필요/);
  assert.match(adminDrawer, />차단</);
  assert.match(adminDrawer, /계획 지문/);
  assert.match(
    adminDrawer,
    /행별 변경계획은 준비됐지만 실제 반영·복구 실행기는 아직 잠겨 있습니다/,
  );
  assert.match(adminDrawer, /실제 조직표/);
  assert.match(adminDrawer, /시험자료/);
  assert.match(adminDrawer, /먼저 해결할 조건/);
  assert.match(adminDrawer, /결정안: 자동 기본안/);
  assert.match(adminDrawer, /저장 완료/);
  assert.match(adminDrawer, /미완료/);
  assert.match(adminDrawer, /로그인 계정 변경/);
  assert.doesNotMatch(adminDrawer, /실제 반영 \(잠김\)/);
  assert.match(globalStyles, /\.staff-application-plan/);
  assert.match(globalStyles, /\.staff-application-state-summary/);
  assert.match(globalStyles, /\.organization-tree/);
  assert.match(
    globalStyles,
    /\.selected-member-chip[\s\S]*?min-height:\s*44px/,
  );
  assert.match(
    globalStyles,
    /\.organization-maintenance \.managed-chip button[\s\S]*?min-height:\s*48px/,
  );
  assert.match(
    globalStyles,
    /@media \(max-width: 720px\)[\s\S]*?\.member-picker\s*\{[\s\S]*?max-height:\s*none/,
  );
  assert.doesNotMatch(adminDrawer, /carefor-staff-sync\/batches\/.*\/apply/);
  assert.match(adminDrawer, /staffUnitTypes/);
  assert.match(adminDrawer, /사업부를 먼저 선택하세요/);
  assert.match(adminDrawer, /시설장은 직종이며, 대표는 직위로 선택합니다/);
  assert.match(adminDrawer, /creatableStaffUnitTypes/);
  assert.match(adminDrawer, /serviceAssignmentDraftsFromEmployee/);
  assert.doesNotMatch(adminDrawer, /예비 파일로 명단 확인하기/);
  assert.doesNotMatch(adminDrawer, /가명 2명 시험자료 열기/);
  assert.doesNotMatch(adminDrawer, /기존 연습자료 보기/);
  assert.match(adminDrawer, /바뀐 어르신이 없습니다/);
  assert.match(adminDrawer, /이 작업은 이미 끝났습니다/);
  assert.match(adminDrawer, /선택한.*명 저장하기/);
  assert.match(layout, /index:\s*false/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton|drizzle/);
  assert.equal(manifest.display, "standalone");
  assert.equal(manifest.short_name, "MESIL_Chat");
  assert.equal(manifest.share_target.action, "/share-target");
  assert.equal(manifest.share_target.method, "POST");
  assert.equal(manifest.share_target.enctype, "multipart/form-data");
  assert.equal(manifest.share_target.params.files[0].name, "files");
  for (const acceptedType of [
    "image/jpeg",
    "audio/wav",
    "video/mp4",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.hancom.hwp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/csv",
    ".wav",
    ".pdf",
    ".xlsx",
    ".hwp",
    ".docx",
    ".pptx",
    ".csv",
  ]) {
    assert.ok(
      manifest.share_target.params.files[0].accept.includes(acceptedType),
    );
    assert.ok(
      legacyManifest.share_target.params.files[0].accept.includes(acceptedType),
    );
  }
  assert.equal(manifest.share_target.params.title, undefined);
  assert.equal(manifest.share_target.params.text, undefined);
  assert.equal(manifest.share_target.params.url, undefined);
  assert.equal(manifest.icons.length, 2);
  assert.match(manifest.icons[0].src, /mesil-chat-192-v3\.png/);
  assert.match(manifest.icons[1].src, /mesil-chat-512-v3\.png/);
  assert.match(serviceWorker, /mesil-chat-shell-v13/);
  assert.match(serviceWorker, /request\.method === "POST"/);
  assert.match(serviceWorker, /url\.pathname === "\/share-target"/);
  assert.match(serviceWorker, /request\.formData\(\)/);
  assert.match(serviceWorker, /mesil-chat-shared-files-v1/);
  assert.match(serviceWorker, /const MAX_SHARED_FILES = 10/);
  assert.match(
    serviceWorker,
    /const MAX_SHARED_FILE_BYTES = 30 \* 1024 \* 1024/,
  );
  assert.match(
    serviceWorker,
    /const MAX_SHARED_TOTAL_BYTES = 100 \* 1024 \* 1024/,
  );
  assert.match(serviceWorker, /share_error=too-many/);
  assert.match(serviceWorker, /share_error=too-large/);
  assert.match(serviceWorker, /share_error=total-too-large/);
  assert.match(serviceWorker, /share_error=unsupported/);
  assert.match(serviceWorker, /url\.origin === self\.location\.origin/);
  assert.match(
    serviceWorker,
    /const SHARED_FILE_MAX_AGE_MS = 24 \* 60 \* 60 \* 1000/,
  );
  assert.match(serviceWorker, /cleanupExpiredSharedFiles/);
  assert.match(serviceWorker, /deleteSharedToken/);
  assert.match(
    serviceWorker,
    /JSON\.stringify\(\{ created_at: Date\.now\(\), entries \}\)/,
  );
  assert.match(
    serviceWorker,
    /key !== CACHE_NAME && key !== SHARED_FILE_CACHE/,
  );
  assert.match(
    legacyServiceWorker,
    /importScripts\("\/mesil-chat-sw-v6\.js"\)/,
  );
  assert.match(sharedTarget, /const MAX_SHARED_FILES = 10/);
  assert.match(
    sharedTarget,
    /const MAX_SHARED_TOTAL_BYTES = 100 \* 1024 \* 1024/,
  );
  assert.match(sharedTarget, /audio\/wav/);
  assert.match(sharedTarget, /application\/pdf/);
  assert.match(
    sharedTarget,
    /Array\.isArray\(\(payload as SharedFileMetadata\)\.entries\)/,
  );
  assert.match(serviceWorker, /url\.pathname\.startsWith\("\/assets\/"\)/);
  assert.match(serviceWorker, /offline\.html/);
  assert.match(serviceWorker, /showNotification/);
  assert.match(serviceWorker, /isTestNotification/);
  assert.match(serviceWorker, /payload\.kind !== "comment"/);
  assert.match(serviceWorker, /payload\.kind === "voice_call"/);
  assert.match(serviceWorker, /payload\.kind === "voice_call_cancel"/);
  assert.match(serviceWorker, /getNotifications/);
  assert.match(serviceWorker, /requireInteraction: isVoiceCall/);
  assert.match(serviceWorker, /notificationclick/);
  assert.match(chatApp, /new URLSearchParams\(window\.location\.search\)/);
  assert.match(chatApp, /searchParams\.get\("room"\)/);
  assert.match(chatApp, /searchParams\.get\("message"\)/);
  assert.match(chatApp, /searchParams\.get\("call"\)/);
  assert.doesNotMatch(chatApp, /searchParams\.get\("caller"\)/);
  assert.doesNotMatch(chatApp, /searchParams\.get\("caller_name"\)/);
  assert.match(chatApp, /\/api\/voice-calls\/pending/);
  assert.match(chatApp, /voiceCall\.presentIncomingCall/);
  assert.match(
    chatApp,
    /rooms\.find\(\(room\) => room\.id === target\.roomId\)/,
  );
  assert.match(chatApp, /message\.room_id === target\.roomId/);
  assert.match(chatApp, /`\/api\/messages\/\$\{target\.messageId\}`/);
  assert.match(chatApp, /잘못되었거나 접근 권한이 없는 알림 주소/);
  assert.match(chatApp, /window\.history\.replaceState/);
  assert.match(chatApp, /consumeSharedTargetFiles/);
  assert.match(chatApp, /const MAX_ATTACHMENTS_PER_MESSAGE = 10/);
  assert.match(chatApp, /const MAX_ATTACHMENT_BYTES = 30 \* 1024 \* 1024/);
  assert.match(chatApp, /attachmentSelectionError\(selected\)/);
  assert.doesNotMatch(chatApp, /slice\(0, 4\)/);
  assert.match(chatApp, /총 \{totalAttachmentMegabytes\(files\)\}MB/);
  assert.match(chatApp, /파일당 최대 30MB/);
  assert.match(chatApp, /파일 .*개를 어디에 보낼까요/);
  assert.match(chatApp, /보낼 방을 선택하면 파일이 첨부됩니다/);
  assert.match(chatApp, /sharedTargetErrorMessage/);
  assert.match(chatApp, /shareError === "too-many"|code === "too-many"/);
  assert.match(chatApp, /사진·음성·동영상·PDF/);
  assert.match(chatApp, /chooseRoomForSharedFiles/);
  assert.match(chatApp, /attachmentSelectionError\(sharedFiles\)/);
  assert.match(chatApp, /확인한 뒤 보내기를 눌러 주세요/);
  assert.match(globalStyles, /\.share-room-layer/);
  assert.match(globalStyles, /\.share-room-option/);
  assert.match(chatApp, /\/api\/messages\/\$\{message\.id\}/);
  assert.match(chatApp, /message_recalled/);
  assert.match(chatApp, /업무 감사기록에는 안전하게 보존됩니다/);
  assert.match(chatApp, /알림 설정/);
  assert.match(
    notificationSound,
    /typeof window === "undefined"\) return "all"/,
  );
  assert.match(pushNotifications, /synchronizeWebPushSubscription/);
  assert.match(pushNotifications, /registerSubscription\(subscription\)/);
  assert.match(pushNotifications, /resubscribe_required/);
  assert.match(pushNotifications, /subscription\.unsubscribe\(\)/);
  assert.match(pushNotifications, /registerOrReplaceSubscription/);
  assert.match(chatApp, /synchronizeWebPushSubscription/);
  assert.match(
    globalStyles,
    /\.room-sidebar\s*\{[\s\S]*?min-height:\s*0;[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(
    globalStyles,
    /\.room-list\s*\{[\s\S]*?min-height:\s*0;[\s\S]*?overflow-y:\s*auto;/,
  );
  assert.match(globalStyles, /\.sidebar-footer\s*\{[\s\S]*?flex:\s*0 0 auto;/);
  assert.match(notificationPanel, /휴대전화 알림 켜고 시험하기/);
  assert.match(notificationPanel, /휴대전화 알림 설정 열기/);
  assert.match(notificationPanel, /전화가 오면 바로 받기/);
  assert.match(notificationPanel, /통화 알림 켜기/);
  assert.match(notificationPanel, /openNativeFullScreenIntentSettings/);
  assert.match(notificationPanel, /readNativePushPermissionStatus/);
  assert.match(notificationPanel, /openNativeNotificationSettings/);
  assert.match(notificationPanel, /notification-sound-panel desktop-resizable-dialog/);
  assert.match(notificationPanel, /이 발표환경에서는 잠금화면 알림을 제공하지 않습니다/);
  assert.match(notificationPanel, /잠금화면 알림 사용 안 함/);
  assert.match(notificationPanel, /sendWebPushTest/);
  assert.match(notificationPanel, /메딕 시험 소리 듣기/);
  assert.match(notificationSound, /\/sounds\/mesil-medic-voice-v2\.wav/);
  assert.match(notificationSound, /playCommentNotification/);
  assert.match(
    chatApp,
    /isRecentWakeNotification\(message\.created_at, wakeSyncStartedAt\)/,
  );
  assert.match(chatApp, /<PeriodWorkDesk/);
  assert.match(
    chatApp,
    /className="icon-button workdesk-button"[\s\S]*?>\s*AI 돌봄 브리핑\s*<\/button>/,
  );
  assert.match(periodWorkDesk, /현장 기록형 돌봄 브리핑/);
  assert.match(chatApp, /발표 · 합성자료/);
  assert.match(chatApp, /결선 시연용 합성자료 · 실제 인물 및 기록과 무관/);
  assert.doesNotMatch(
    chatApp,
    /화면·검색·알림·AI·출력에는 코드 인물과 새로 만든 합성 사건만 사용합니다/,
  );
  assert.doesNotMatch(chatApp, /PwaInstallButton/);
  assert.doesNotMatch(loginScreen, /PwaInstallButton/);
  assert.match(globalStyles, /\.coded-synthetic-badge/);
  assert.match(periodWorkDesk, /DEV 비공식 자료 · 코드화된 합성 시험자료/);
  assert.match(periodWorkDesk, /이 기간으로 조회/);
  assert.match(periodWorkDesk, /<CareBriefingReader/);
  assert.match(periodWorkDesk, /sources=\{review.sources\}/);
  assert.doesNotMatch(
    periodWorkDesk,
    /<details className="period-ai-summary" open>/,
    "원문 근거는 한눈 요약보다 먼저 펼쳐지면 안 됩니다.",
  );
  assert.match(
    globalStyles,
    /@media \(max-width: 680px\)[\s\S]*?\.care-briefing-title\s*{[\s\S]*?flex-direction:\s*column[\s\S]*?\.care-briefing-title\s*>\s*\.button\s*{[\s\S]*?width:\s*100%[\s\S]*?min-height:\s*44px[\s\S]*?white-space:\s*nowrap/,
  );
  assert.match(
    globalStyles,
    /\.period-filter-card:not\(\[open\]\)\s*>\s*\.period-filter-fields\s*{[^}]*display:\s*none/,
  );
  assert.match(periodWorkDesk, /review.care_topics/);
  assert.match(periodWorkDesk, /reviewScope === loadedScope/);
  assert.match(periodWorkDesk, /role="status"/);
  assert.match(periodWorkDesk, /value\.getDate\(\) - 6/);
  assert.match(periodWorkDesk, /periodDescription/);
  assert.match(periodWorkDesk, /periodRangeError/);
  assert.match(periodWorkDesk, /setStartDate\(event\.currentTarget\.value\)/);
  assert.match(periodWorkDesk, /setEndDate\(event\.currentTarget\.value\)/);
  assert.match(periodWorkDesk, /한 번에 최대 6개월까지 정리할 수 있습니다/);
  assert.match(periodWorkDesk, /applyQuickRange\("today"\)/);
  assert.match(periodWorkDesk, /applyQuickRange\("week"\)/);
  assert.match(periodWorkDesk, /applyQuickRange\("month"\)/);
  assert.match(periodWorkDesk, /applyQuickRange\("quarter"\)/);
  assert.match(periodWorkDesk, /applyQuickRange\("half-year"\)/);
  assert.doesNotMatch(periodWorkDesk, /applyQuickRange\("yesterday"\)/);
  assert.match(periodWorkDesk, /truncated=\{review.truncated\}/);
  assert.match(periodWorkDesk, /truncated_periods\.join/);
  assert.match(periodWorkDesk, /구조화 사건을 병합하고/);
  assert.doesNotMatch(
    periodWorkDesk.replace(/const changed = \(\) => \{ setReview\(null\); void loadReview\(\{ saveHistory: false \}\); \};/, ""),
    /setReview\(null\)/,
    "브리핑 재생성 실패 시 직전 성공 결과를 화면에 유지해야 합니다.",
  );
  assert.match(periodWorkDesk, /careBriefingErrorMessage/);
  assert.match(periodWorkDesk, /다시 시도/);
  assert.match(periodWorkDesk, /오늘 예정·기한/);
  assert.match(periodWorkDesk, /지금 먼저 확인/);
  assert.match(periodWorkDesk, /계속 확인할 일/);
  assert.match(periodWorkDesk, /오늘 꼭 볼 것/);
  assert.match(periodWorkDesk, /미완료·후속/);
  assert.match(periodWorkDesk, /충돌·확인 필요/);
  assert.match(periodWorkDesk, /care-briefing-resident-group/);
  assert.match(periodWorkDesk, /care-briefing-resident-heading/);
  assert.match(periodWorkDesk, /care-briefing-row-status/);
  assert.match(periodWorkDesk, /세부·근거 보기/);
  assert.match(periodWorkDesk, /group\.residentName/);
  assert.match(
    periodWorkDesk,
    /compactBriefingText\(\s*card\.change_summary,\s*card\.resident_name/,
  );
  assert.match(periodWorkDesk, /비교할 최근 \\d\+일 기록이 없어/);
  assert.doesNotMatch(
    periodWorkDesk,
    /<details[\s\S]{0,120}className=\{`care-briefing-row[^>]*\sopen[=>]/,
    "사건 세부와 근거는 기본으로 접혀 있어야 합니다.",
  );
  assert.match(globalStyles, /\.care-briefing-row\s*>\s*summary/);
  assert.match(globalStyles, /\.care-briefing-resident-heading/);
  assert.match(periodWorkDesk, /무슨 일/);
  assert.match(periodWorkDesk, /현재 상태/);
  assert.match(periodWorkDesk, /확인 필요 판단 근거/);
  assert.match(periodWorkDesk, /발생/);
  assert.match(periodWorkDesk, /최근 갱신/);
  assert.match(periodWorkDesk, /이미 한 조치/);
  assert.match(periodWorkDesk, /다음 조치·확인/);
  assert.match(periodWorkDesk, /기한·재확인/);
  assert.match(periodWorkDesk, /원문과 후속 결과를 직원이 확인한 뒤 확정/);
  assert.match(periodWorkDesk, /care-briefing-glance/);
  assert.match(globalStyles, /\.care-briefing-glance/);
  assert.match(periodWorkDesk, /onSelectResident=\{changeResidentScope\}/);
  assert.match(periodWorkDesk, /작성할 서류 선택/);
  assert.match(periodWorkDesk, /서류 모두 선택/);
  assert.match(periodWorkDesk, /서류 선택 해제/);
  assert.match(periodWorkDesk, /alwaysAvailableDocumentCandidates\.map/);
  assert.doesNotMatch(periodWorkDesk, /\{documentCandidateOrder\.map/);
  assert.match(periodWorkDesk, /기존 작성본·수정 이력/);
  assert.match(periodWorkDesk, /showPlanningHelper \? <section/);
  assert.match(periodWorkDesk, /onCompare=/);
  assert.match(periodWorkDesk, /comparisonTopics=/);
  assert.match(globalStyles, /\.care-planning-target/);
  assert.match(periodWorkDesk, /급여제공기록지/);
  assert.match(periodWorkDesk, /상담일지/);
  assert.match(periodWorkDesk, /인지기능검사/);
  assert.match(periodWorkDesk, /낙상위험도/);
  assert.match(periodWorkDesk, /욕창위험도/);
  assert.match(periodWorkDesk, /욕구사정/);
  assert.match(periodWorkDesk, /장기요양급여 제공계획서/);
  assert.doesNotMatch(periodWorkDesk, /간호 기록 후보/);
  assert.doesNotMatch(periodWorkDesk, /프로그램 기록 후보/);
  assert.match(periodWorkDesk, /서류에 넣을 내용 선택/);
  assert.match(periodWorkDesk, /내용 모두 선택/);
  assert.match(periodWorkDesk, /내용 선택 해제/);
  assert.match(periodWorkDesk, /선택한 내용 요약 만들기/);
  assert.match(periodWorkDesk, /선택한 내용 원문 인쇄·PDF/);
  assert.match(periodWorkDesk, /record-group-action-status/);
  assert.match(periodWorkDesk, /서류에 넣을 대화와 기록을 한 건 이상 선택하면/);
  assert.match(periodWorkDesk, /선택한 내용 요약을 만들었습니다\. 자동 저장되지 않았습니다/);
  assert.match(periodWorkDesk, /id="record-ai-result"/);
  assert.match(periodWorkDesk, /ref=\{recordAiResultRef\}/);
  assert.match(periodWorkDesk, /tabIndex=\{-1\}/);
  assert.match(periodWorkDesk, /aria-controls="record-ai-result"/);
  assert.match(periodWorkDesk, /결과 확인하기/);
  assert.match(periodWorkDesk, /record-selection-status/);
  assert.match(periodWorkDesk, /이 서류에 넣을 대화와 기록을 아래에서 선택해 주세요/);
  assert.match(periodWorkDesk, /선택을 해제해도 원본 대화와 기록은 삭제되지 않습니다/);
  assert.match(periodWorkDesk, /서류 초안이나[\s\S]*공식 기록에 자동 저장되지 않습니다/);
  assert.match(periodWorkDesk, /직원이 원문과 대조해[\s\S]*최종 확인해 주세요/);
  assert.match(periodWorkDesk, /추천 근거 \{recommendationCount\}건/);
  assert.match(periodWorkDesk, /여부 확인 필요 \{reviewCount\}건/);
  assert.match(periodWorkDesk, /직원 수동 선택 · 자동 추천 아님/);
  assert.match(periodWorkDesk, /event\.document_candidate_review_types/);
  assert.match(periodWorkDesk, /event\.document_candidate_reasons/);
  assert.match(periodWorkDesk, /event\.document_candidate_review_reasons/);
  assert.match(periodWorkDesk, /eventSummary \|\| "사건 요약 확인 필요"/);
  assert.match(periodWorkDesk, /record-event-summary/);
  assert.match(periodWorkDesk, /record-event-selection-reason/);
  assert.match(periodWorkDesk, /recommendedReason\.startsWith\("확인 필요"\)/);
  assert.match(periodWorkDesk, /record-event-review-state/);
  assert.match(periodWorkDesk, /확인되지 않은 수치는 기록에 확정하지 않습니다/);
  assert.match(periodWorkDesk, /3단계 · 서류 초안에서 수정·확인/);
  assert.match(periodWorkDesk, /위 결과는 일반 업무 요약입니다/);
  assert.match(periodWorkDesk, /각각 별도 초안으로 엽니다/);
  assert.match(periodWorkDesk, /openRecordSummaryDraft/);
  assert.match(periodWorkDesk, /drafts: \[draft\]/);
  assert.match(periodWorkDesk, /편집 초안 열기/);
  assert.match(periodWorkDesk, /직원 확인 전[\s\S]*인쇄·PDF 버튼이 잠겨 있습니다/);
  assert.match(globalStyles, /\.record-event-summary[\s\S]*-webkit-line-clamp:\s*2/);
  assert.match(globalStyles, /\.record-event-review-state/);
  assert.match(globalStyles, /\.record-ai-draft-links/);
  const summarizeSelectedEventsSource = periodWorkDesk.slice(
    periodWorkDesk.indexOf("async function summarizeSelectedEvents"),
    periodWorkDesk.indexOf("function focusRecordSummaryResult"),
  );
  assert.doesNotMatch(
    summarizeSelectedEventsSource,
    /scrollIntoView/,
    "요약 완료만으로 사용자의 현재 스크롤 위치를 강제로 바꾸지 않아야 합니다.",
  );
  assert.doesNotMatch(
    summarizeSelectedEventsSource,
    /setError\(/,
    "기록 요약 실패는 위쪽 공용 오류를 삽입해 현재 위치를 움직이면 안 됩니다.",
  );
  assert.match(periodWorkDesk, /요약을 만들지 못했습니다/);
  assert.match(periodWorkDesk, /원본 대화·기록은 그대로 유지됩니다/);
  assert.match(periodWorkDesk, /function focusRecordSummaryResult/);
  assert.match(
    periodWorkDesk,
    /target\.scrollIntoView\(\{ behavior: "smooth", block: "start" \}\)/,
  );
  assert.match(periodWorkDesk, /target\.focus\(\{ preventScroll: true \}\)/);
  assert.match(periodWorkDesk, /record-summary/);
  assert.match(periodWorkDesk, /경과 참고/);
  assert.match(periodWorkDesk, /처리 완료/);
  assert.match(periodWorkDesk, /근거 대화 접기/);
  assert.match(periodWorkDesk, /toggleEvidence/);
  assert.match(periodWorkDesk, /답글로 확인한 진행상황/);
  assert.match(periodWorkDesk, /대화에서 확인·답글하기/);
  assert.match(periodWorkDesk, /onOpenSource\(source\.message\.room_id, source\.message\.id\)/);
  assert.match(chatApp, /async function openWorkdeskSource/);
  assert.match(chatApp, /onOpenSource=\{openWorkdeskSource\}/);
  assert.match(globalStyles, /\.care-briefing-evidence-replies/);
  assert.match(globalStyles, /\.care-briefing-evidence-open/);
  assert.doesNotMatch(periodWorkDesk, /전체 대화 AI 요약/);
  assert.doesNotMatch(periodWorkDesk, /이 기간의 업무대화/);
  assert.ok(
    periodWorkDesk.indexOf("지금 먼저 확인") <
      periodWorkDesk.indexOf("작성할 서류 선택"),
  );
  assert.doesNotMatch(periodWorkDesk, /작성 가능한 일지 초안/);
  assert.match(periodWorkDesk, /enhance_summary:\s*false/);
  assert.doesNotMatch(periodWorkDesk, /enhance_summary:\s*true/);
  assert.doesNotMatch(periodWorkDesk, /window\.open/);
  assert.match(periodWorkDesk, /document\.createElement\("iframe"\)/);
  assert.match(periodWorkDesk, /afterprint/);
  assert.match(periodWorkDesk, /브리핑 인쇄 미리보기를 열었습니다/);
  assert.match(periodWorkDesk, /선택한 내용 원문 인쇄 미리보기를 열었습니다/);
  assert.match(periodWorkDesk, /요약 인쇄 미리보기를 열었습니다/);
  assert.match(periodWorkDesk, /@page \{/);
  assert.match(periodWorkDesk, /size: A4/);
  assert.match(periodWorkDesk, /counter\(page\)/);
  assert.doesNotMatch(
    periodWorkDesk,
    /escapePrintText\(\s*recordSummary\.generator/,
  );
  // Preparation and generation now have separate deadlines. Executable
  // record-question-flow/period-summary-flow tests cover cancellation instead
  // of requiring the removed single 55-second source-text implementation.
  assert.match(periodWorkDesk, /Nemotron Ultra 550B/);
  assert.match(periodWorkDesk, /안전 정리 사용/);
  assert.doesNotMatch(periodWorkDesk, /<footer>MESIL_Chat/);
  assert.doesNotMatch(periodWorkDesk, /footer \{ position: fixed/);
  assert.doesNotMatch(periodWorkDesk, /period-workdesk-printing/);
  assert.match(globalStyles, /\.period-workdesk-card/);
  assert.match(chatApp, /function engagementLabel\(message: Message\)/);
  assert.match(chatApp, /\{engagementLabel\(message\)\}/);
  assert.match(chatApp, /message\.reply_user_count/);
  assert.match(globalStyles, /\.message-engagement/);
  assert.match(globalStyles, /\.care-briefing-card/);
  assert.match(chatApp, /대화 검색/);
  assert.match(chatApp, /인수인계/);
  assert.match(chatApp, /업무협조/);
  assert.match(chatApp, /보고/);
  assert.doesNotMatch(chatApp, /업무지정 해제/);
  assert.match(chatApp, /function MessageActionBadge/);
  assert.match(chatApp, /<small>유형<\/small>/);
  assert.match(chatApp, /<small>담당<\/small>/);
  assert.match(chatApp, /<small>상태<\/small>/);
  assert.match(chatApp, /assigned: "담당자 확인 전"/);
  assert.match(chatApp, /메시지 읽음 표시와는 별개입니다/);
  assert.match(chatApp, /message\.action_item \? \(/);
  assert.doesNotMatch(chatApp, /업무지정 ·/);
  assert.match(messageDetail, /<strong>업무 정보<\/strong>/);
  assert.match(messageDetail, /상태 ·/);
  assert.match(messageDetail, /메시지 읽음 수와는 다릅니다/);
  assert.doesNotMatch(messageDetail, /이전 방식 업무표시/);
  assert.match(globalStyles, /\.message-action-badge \.action-badge-part/);
  assert.match(messageDetail, /읽은 직원/);
  assert.doesNotMatch(messageDetail, /<h3>답글<\/h3>/);
  assert.doesNotMatch(messageDetail, /다른 방에 전달/);
  assert.match(chatApp, /관리자 코멘트/);
  assert.match(chatApp, /다른 방 전달/);
  assert.match(attachmentDisplay, /두 손가락으로 확대·축소/);
  assert.match(attachmentDisplay, /좌우로 밀어 다음 사진/);
  assert.match(attachmentDisplay, /event\.key === "ArrowLeft"/);
  assert.match(attachmentDisplay, /event\.key === "ArrowRight"/);
  assert.match(attachmentDisplay, /aria-label="이전 사진"/);
  assert.match(attachmentDisplay, /aria-label="다음 사진"/);
  assert.match(attachmentDisplay, /const touchPanStartRef = useRef/);
  assert.match(attachmentDisplay, /const clampImageOffset = useCallback/);
  assert.match(
    attachmentDisplay,
    /imageElement\.clientWidth \* scale - lightboxElement\.clientWidth/,
  );
  assert.match(
    attachmentDisplay,
    /imageElement\.clientHeight \* scale - lightboxElement\.clientHeight/,
  );
  assert.match(
    attachmentDisplay,
    /event\.touches\.length === 1 && imageScaleRef\.current > 1/,
  );
  assert.match(
    attachmentDisplay,
    /updateImageOffset\(\{[\s\S]*?panStart\.offsetX/,
  );
  assert.match(attachmentDisplay, /onPointerCancel=/);
  assert.match(attachmentDisplay, /window\.addEventListener\("resize"/);
  assert.match(attachmentDisplay, /imageGestureActive \? " is-manipulating"/);
  assert.match(
    globalStyles,
    /\.image-lightbox-image\.is-manipulating[\s\S]*?transition:\s*none/,
  );
  assert.match(
    attachmentDisplay,
    /const resetImageView = useCallback[\s\S]*?touchPanStartRef\.current = null/,
  );
  assert.match(chatApp, /galleryAttachments=\{message\.attachments\}/);
  assert.match(
    messageDetail,
    /galleryAttachments=\{detail\.message\.attachments\}/,
  );
  assert.match(chatApp, /notification_user_ids/);
  assert.match(chatApp, /playCommentNotification/);
  assert.match(attachmentDisplay, /<audio controls/);
  assert.match(attachmentDisplay, /<video controls playsInline/);
  assert.match(attachmentDisplay, /PDF_HISTORY_STATE_KEY/);
  assert.match(attachmentDisplay, /IMAGE_HISTORY_STATE_KEY/);
  assert.match(attachmentDisplay, /window\.history\.pushState/);
  assert.match(attachmentDisplay, /addEventListener\("popstate"/);
  assert.doesNotMatch(attachmentDisplay, /URL\.createObjectURL/);
  assert.match(attachmentDisplay, /<PdfCanvasViewer/);
  assert.match(pdfCanvasViewer, /pdfjs-dist/);
  assert.match(pdfCanvasViewer, /GlobalWorkerOptions\.workerSrc/);
  assert.match(pdfCanvasViewer, /credentials: "include"/);
  assert.match(pdfCanvasViewer, /getDocument/);
  assert.match(pdfCanvasViewer, /<canvas/);
  assert.match(pdfCanvasViewer, /이전/);
  assert.match(pdfCanvasViewer, /다음/);
  assert.match(packageJson, /pdfjs-dist/);
  assert.match(attachmentDisplay, /aria-label="PDF 닫기"/);
  assert.match(attachmentDisplay, /다른 앱으로 열기/);
  assert.match(globalStyles, /\.pdf-viewer/);
  assert.match(globalStyles, /\.pdf-canvas-viewer/);
  assert.match(attachmentDisplay, /음성 받아쓰기/);
  assert.match(attachmentDisplay, /이미지 글자 판독/);
  assert.match(attachmentDisplay, /수정 내용 저장/);
  assert.match(attachmentDisplay, /반영됨/);
  assert.match(attachmentDisplay, /extraction\?\.suggested_text/);
  assert.match(attachmentDisplay, /초안 반영/);
  assert.match(attachmentDisplay, /자동 반영 내역/);
  assert.match(attachmentDisplay, /수정 초안에만 적용/);
  assert.doesNotMatch(attachmentDisplay, /split\(candidate\.recognized\)/);
  assert.match(attachmentDisplay, /attachment-review-warnings/);
  assert.match(attachmentDisplay, /previous_attempts/);
  assert.match(attachmentDisplay, /판독 정보 불러오는 중/);
  assert.match(
    attachmentDisplay,
    /\/api\/attachments\/\$\{attachment\.id\}\/text-extraction/,
  );
  assert.match(attachmentDisplay, /retryExtraction/);
  assert.match(attachmentDisplay, /원본 확인/);
  assert.match(attachmentDisplay, /주간보호/);
  assert.match(globalStyles, /\.attachment-extraction-suggestion-meta/);
  assert.match(globalStyles, /\.attachment-auto-application-details/);
  assert.match(attachmentDisplay, /reviewed_by_name/);
  assert.match(attachmentDisplay, /formatReviewedAt/);
  assert.match(attachmentDisplay, /extraction\.status !== "reviewed"/);
  assert.match(messageDetail, /onMessageChanged/);
  assert.match(messageDetail, /void loadDetail\(\)/);
  assert.match(attachmentDisplay, /다른 앱으로 공유/);
  assert.match(attachmentDisplay, /prepareAttachmentShare/);
  assert.match(attachmentDisplay, /openAttachmentShare/);
  assert.match(attachmentDisplay, /savePreparedAttachment/);
  assert.match(attachmentDisplay, /공유창을 열었습니다/);
  assert.match(attachmentDisplay, /파일 저장/);
  assert.match(attachmentDisplay, /shareFile\(activeImage\)/);
  assert.match(globalStyles, /\.image-lightbox-share-feedback/);
  assert.match(attachmentShare, /credentials: "include"/);
  assert.match(attachmentShare, /navigator\.canShare\(\{ files: \[file\] \}\)/);
  assert.match(attachmentShare, /await navigator\.share\(\{/);
  assert.match(attachmentShare, /hasActiveShareGesture/);
  assert.match(attachmentShare, /extension === "\.m4a"/);
  assert.match(attachmentShare, /audio\/x-m4a/);
  assert.match(attachmentShare, /savePreparedAttachment/);
  assert.match(attachmentDisplay, /function editableExtractionText/);
  assert.match(attachmentDisplay, /extraction\?\.latest_confirmed_text/);
  assert.match(attachmentDisplay, /latest_confirmed_at/);
  assert.match(attachmentDisplay, /extraction\?\.original_extracted_text/);
  assert.match(attachmentDisplay, /loadAndOpenExtractionEditor/);
  assert.match(
    attachmentDisplay,
    /openExtractionEditor\(editableExtractionText\(next\.text_extraction\)\)/,
  );
  assert.match(messageDetail, /showExtraction/);
  assert.match(messageDetail, /canEditExtraction/);
  assert.match(messageDetail, /return attachment\.can_review_text === true/);
  assert.doesNotMatch(messageDetail, /attachment\.uploader_id === currentUserId/);
  assert.match(
    messageDetail,
    /ResidentLinkReview message=\{detail\.message\} canEdit=\{canProcessRecords\}/,
  );
  assert.match(messageDetail, /판독문 보기/);
  assert.match(adminDrawer, /function employeeOrganizationSummary/);
  assert.match(adminDrawer, /function serviceAssignmentJobLabel/);
  assert.match(adminDrawer, /직종 확인 필요/);
  assert.match(adminDrawer, /assignment\.is_current/);
  assert.match(adminDrawer, /\["facility", "daycare", "homecare"\] as const/);
  assert.match(adminDrawer, /employeeOrganizationSummary\(employee\)/);
  assert.match(adminDrawer, /서비스별 직종·직위/);
  assert.match(adminDrawer, /근무 중인 서비스를 선택하고 각 서비스에서 맡는 직종과/);
  assert.match(adminDrawer, /변경 전 배정은 지난 기록으로 보존됩니다/);
  assert.match(adminDrawer, /service-assignment-editor-card/);
  assert.match(adminDrawer, /facility_director/);
  assert.match(adminDrawer, /시설장은 직종이며, 대표는 직위로 선택합니다/);
  assert.match(
    adminDrawer,
    /staff-directory\/\$\{selectedEmployee\.staff_id\}\/service-assignments/,
  );
  assert.doesNotMatch(adminDrawer, /selectedUsesServiceAssignments/);
  assert.match(adminDrawer, /includeLegacyAssignment/);
  assert.match(adminDrawer, /직원정보 저장/);
  assert.match(adminDrawer, /로그인 계정이 없어도 서비스별 직종·직위는 저장/);
  assert.match(globalStyles, /\.service-assignment-editor-card\.selected/);
  assert.match(globalStyles, /\.service-assignment-fields/);
  assert.match(adminDrawer, /기존 자료 연결/);
  assert.match(adminDrawer, /첫 로그인 비밀번호 변경/);
  assert.doesNotMatch(
    adminDrawer,
    /서비스 배정 \$\{employee\.service_assignments\.length\}건/,
  );
  assert.match(roomSearch, /검색 결과 AI 요약/);
  assert.match(roomSearch, /전체 흐름 요약/);
  assert.match(roomSearch, /상세 경과 요약/);
  assert.match(roomSearch, /summary\.display_mode === "overview"/);
  assert.match(roomSearch, /summary\.summary_sentences\.map/);
  assert.match(roomSearch, /summary\.summary_evidence/);
  assert.match(roomSearch, /근거를 누르면 원래 대화를 볼 수 있습니다/);
  assert.match(roomSearch, /AI 처리/);
  assert.match(roomSearch, /저장된 요약/);
  assert.match(roomSearch, /match\.source_label/);
  assert.match(roomSearch, /match\.excerpt/);
  assert.doesNotMatch(roomSearch, /SEARCH_SUMMARY_PROVIDER_KEY|요약 방식|setProvider\(/);
  assert.match(roomSearch, /중앙 설정의 로컬 AI/);
  assert.match(roomSearch, /업무 상태/);
  assert.doesNotMatch(
    chatApp,
    /onOpenMessage=\{\(messageId\) => \{\s*setRoomSearchOpen\(false\)/,
  );
  assert.ok(
    chatApp.indexOf("<RoomSearchOverlay") <
      chatApp.indexOf("<MessageDetailOverlay"),
    "검색 상세는 검색창 위에 열리고 닫으면 검색창이 그대로 남아야 합니다.",
  );
  assert.match(globalStyles, /\.room-search-card/);
  assert.match(messageDetail, /message-detail-card desktop-resizable-dialog/);
  assert.match(aiAssist, /ai-assist-panel desktop-resizable-dialog/);
  assert.match(roomSearch, /room-search-card desktop-resizable-dialog/);
  assert.match(
    globalStyles,
    /@media \(min-width: 721px\)[\s\S]*?\.desktop-resizable-dialog[\s\S]*?resize: both/,
  );
  assert.match(
    globalStyles,
    /\.message-detail-card\.desktop-resizable-dialog[\s\S]*?width: min\(1040px/,
  );
  assert.match(
    globalStyles,
    /@media \(max-width: 720px\)[\s\S]*?\.desktop-resizable-dialog[\s\S]*?resize: none/,
  );
  assert.match(globalStyles, /height:\s*min\(820px,\s*calc\(100dvh - 48px\)\)/);
  assert.doesNotMatch(globalStyles, /body\.period-workdesk-printing/);
});

test("keeps Android Back ordered across room, detail, AI, PDF, and admin layers", async () => {
  const [
    chatApp,
    adminDrawer,
    attachmentDisplay,
    residentPicker,
    navigationHistory,
  ] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/AdminDrawer.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/ResidentPickerDialog.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/navigationHistory.ts", import.meta.url), "utf8"),
  ]);

  assert.match(navigationHistory, /__mesilChatNavigation/);
  assert.match(navigationHistory, /roomId\?: string/);
  assert.match(navigationHistory, /messageId\?: string/);
  assert.match(navigationHistory, /aiAssistOpen\?: true/);
  assert.match(navigationHistory, /adminOpen\?: true/);
  assert.match(
    navigationHistory,
    /settingsPanel\?: "security" \| "notification"/,
  );
  assert.match(navigationHistory, /window\.history\.pushState/);
  assert.match(navigationHistory, /window\.history\.replaceState/);
  assert.match(navigationHistory, /attachmentOverlayHistoryStateKeys/);
  assert.match(navigationHistory, /clearAttachmentOverlays/);
  assert.match(
    chatApp,
    /navigation\.roomId \? "replace" : "push",\s*\{ clearAttachmentOverlays: true \}/,
  );
  assert.match(
    chatApp,
    /useLayoutEffect\(\(\) => \{\s*updateNavigationHistoryState\([\s\S]*?clearAttachmentOverlays: true/,
  );

  assert.match(
    chatApp,
    /addEventListener\("popstate", synchronizeNavigation\)/,
  );
  assert.match(chatApp, /openRoom\(nextRoomId, false\)/);
  assert.match(
    chatApp,
    /setSecurityOpen\(navigation\.settingsPanel === "security"\)/,
  );
  assert.match(
    chatApp,
    /setNotificationSoundOpen\(navigation\.settingsPanel === "notification"\)/,
  );
  assert.match(chatApp, /openSettingsPanel\("security"\)/);
  assert.match(chatApp, /openSettingsPanel\("notification"\)/);
  assert.match(residentPicker, /value: "facility", label: "시설"/);
  assert.match(residentPicker, /value: "daycare", label: "주간보호"/);
  assert.match(residentPicker, /value: "homecare", label: "방문요양"/);
  assert.match(chatApp, /selectedResidentButtonLabel/);
  assert.match(
    chatApp,
    /function closeResidentPicker[\s\S]*?window\.history\.back\(\)/,
  );
  assert.match(
    chatApp,
    /function closeSettingsPanel[\s\S]*?window\.history\.back\(\)/,
  );
  assert.match(
    chatApp,
    /function closeMessageDetail\(\)[\s\S]*?window\.history\.back\(\)/,
  );
  assert.match(
    chatApp,
    /function closeMessageDetail\(\)[\s\S]*?selectedMessageRef\.current = null;[\s\S]*?setSelectedMessageId\(null\);[\s\S]*?window\.history\.back\(\)/,
  );
  assert.match(
    chatApp,
    /function closeAiAssist\(\)[\s\S]*?window\.history\.back\(\)/,
  );
  assert.match(
    chatApp,
    /if \(!nextAiAssistOpen\)[\s\S]*?closedAiMessageId === nextMessageId[\s\S]*?setDetailRefreshVersion/,
  );
  assert.match(
    chatApp,
    /function closeAiAssist\(\)[\s\S]*?closedAiMessageId === selectedMessageRef\.current[\s\S]*?setDetailRefreshVersion/,
  );
  assert.match(
    chatApp,
    /function closeActiveRoom\(\)[\s\S]*?window\.history\.back\(\)/,
  );

  assert.match(
    adminDrawer,
    /updateNavigationHistoryState\(\{ adminOpen: true \}, "push"\)/,
  );
  assert.match(
    adminDrawer,
    /addEventListener\("popstate", closeOnHistoryBack\)/,
  );
  assert.match(adminDrawer, /직원정보에 저장하지 않은 변경/);
  assert.match(adminDrawer, /어르신 표시 순서에 저장하지 않은 변경/);
  assert.match(adminDrawer, /hasUnsavedEmployeeChanges/);
  assert.match(adminDrawer, /hasUnsavedResidentOrderChanges/);

  assert.match(attachmentDisplay, /PDF_HISTORY_STATE_KEY/);
  assert.match(attachmentDisplay, /addEventListener\("popstate"/);
  assert.match(
    attachmentDisplay,
    /const closeExtractionEditor = useCallback[\s\S]*?if \(shouldStepBack\) \{[\s\S]*?window\.history\.back\(\);[\s\S]*?return;[\s\S]*?\}[\s\S]*?setEditingExtraction\(false\)/,
  );
});

test("opens incident-scoped care document drafts only after staff evidence review", async () => {
  const [periodWorkDesk, draftWorkspace, globalStyles, types] =
    await Promise.all([
      readFile(
        new URL("../app/components/PeriodWorkDesk.tsx", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL("../app/documentDraftWorkspace.ts", import.meta.url),
        "utf8",
      ),
      readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
      readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
    ]);

  assert.match(periodWorkDesk, /openDocumentDraftWorkspace/);
  assert.match(periodWorkDesk, /draft\.event_group_id === card\.event_group_id/);
  assert.match(periodWorkDesk, /AI가 연결한 기록·서류 후보/);
  assert.match(periodWorkDesk, /기록·서류 초안 만들기/);
  assert.match(draftWorkspace, /window\.open/);
  assert.match(
    draftWorkspace,
    /draft\.event_group_id !== card\.event_group_id/,
  );
  assert.match(draftWorkspace, /직원 확인 전 AI 초안/);
  assert.match(draftWorkspace, /DEV 비공식 자료 · 코드화된 합성 시험자료/);
  assert.match(draftWorkspace, /공식 기록 저장이나 SMCODI 전송을 하지 않습니다/);
  assert.match(draftWorkspace, /사건 발생 즉시 만든 편집용 초안/);
  assert.match(draftWorkspace, /draft\.draft_fields/);
  assert.match(draftWorkspace, /draft\.missing_fields/);
  assert.match(draftWorkspace, /draft\.human_verification_fields/);
  assert.match(draftWorkspace, /초안 문안 전체 선택/);
  assert.doesNotMatch(draftWorkspace, /navigator\.clipboard/);
  assert.doesNotMatch(draftWorkspace, /execCommand\("copy"\)/);
  assert.match(draftWorkspace, /문안 선택됨 · Ctrl\+C로 복사해 주세요/);
  assert.match(draftWorkspace, /printButton\.disabled = true/);
  assert.match(draftWorkspace, /checkboxes\.every/);
  assert.match(draftWorkspace, /draftWindow\.print\(\)/);
  assert.match(draftWorkspace, /createElement\("details"\)/);
  assert.match(draftWorkspace, /근거 보기 · 원문·답글·사진/);
  assert.match(draftWorkspace, /초안 본문에서 제외한 원문·답글·첨부도 삭제하지 않았습니다/);
  assert.doesNotMatch(draftWorkspace, /evidencePanel\.open = true/);
  assert.match(draftWorkspace, /extraction\.original_extracted_text/);
  assert.match(draftWorkspace, /음성 받아쓰기 원문/);
  assert.match(draftWorkspace, /이미지 글자 판독 원문/);
  assert.match(draftWorkspace, /source\.comments/);
  assert.match(draftWorkspace, /api\/workdesk\/attachments/);
  assert.match(draftWorkspace, /onOpenSource\(source\.message\.room_id/);
  assert.doesNotMatch(draftWorkspace, /apiFetch/);
  assert.match(types, /export type PeriodDocumentDraft = \{[\s\S]*event_group_id: string/);
  assert.match(
    globalStyles,
    /footer\.has-document-draft\s*\{[\s\S]*grid-template-columns: repeat\(2/,
  );
  assert.match(
    globalStyles,
    /@media \(max-width: 680px\)[\s\S]*footer\.has-document-draft[\s\S]*grid-template-columns: minmax\(0, 1fr\)/,
  );
});

test("preserves existing drafting guidance behind the reading-first briefing", async () => {
  const [periodWorkDesk, globalStyles, types, cycleWorkspace] = await Promise.all([
    readFile(new URL("../app/components/PeriodWorkDesk.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ResidentAssessmentCycleWorkspace.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(types, /SocialWorkerWritingGuidance/);
  assert.match(types, /scenario_notice: string \| null/);
  assert.match(periodWorkDesk, /사회복지사 작성 안내/);
  assert.match(periodWorkDesk, /이렇게 작성해 보세요/);
  assert.match(periodWorkDesk, /급여제공계획 검토 목록에 담기/);
  assert.match(periodWorkDesk, /guidance\.plan_review_notice/);
  assert.match(periodWorkDesk, /상태 비교와 판단 근거 보기/);
  assert.doesNotMatch(periodWorkDesk, /showSavedResults|care-saved-results|기존 작성본 열기/);
  assert.match(periodWorkDesk, /<ResidentCarePlanningPanel/);
  assert.match(periodWorkDesk, /disabled=\{!guidance\.based_on_confirmed_facts\}/);
  assert.match(globalStyles, /\.social-worker-guidance/);
  assert.match(globalStyles, /\.synthetic-scenario-notice/);
  assert.match(cycleWorkspace, /assessment-plan-review-control/);
  assert.doesNotMatch(cycleWorkspace, /급여제공계획 변경 검토에 반영/);
});

test("returns to the message that opened detail instead of jumping to the room bottom", async () => {
  const chatApp = await readFile(
    new URL("../app/components/ChatApp.tsx", import.meta.url),
    "utf8",
  );

  assert.match(chatApp, /const messageAreaRef = useRef/);
  assert.match(chatApp, /const messageReturnPositionRef = useRef/);
  assert.match(chatApp, /returnMessageId: saved\.messageId/);
  assert.match(chatApp, /returnScrollTop: saved\.scrollTop/);
  assert.match(chatApp, /navigation\.returnMessageId/);
  assert.match(chatApp, /data-message-id=\{message\.id\}/);
  assert.match(
    chatApp,
    /roomLoading \|\|[\s\S]*?selectedMessageRef\.current \|\|[\s\S]*?messageReturnPositionRef\.current/,
  );
  assert.match(chatApp, /anchorOffset/);
  assert.match(
    chatApp,
    /area\.scrollTop \+ currentOffset - saved\.anchorOffset/,
  );
});

test("opens a room at the latest message after loading finishes", async () => {
  const chatApp = await readFile(
    new URL("../app/components/ChatApp.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    chatApp,
    /useLayoutEffect\(\(\) => \{[\s\S]*?roomLoading[\s\S]*?area\.scrollTop = area\.scrollHeight;[\s\S]*?\}, \[activeRoomId, messages, roomLoading\]\);/,
  );
  assert.match(chatApp, /const keepRoomAtLatestRef = useRef\(false\)/);
  assert.match(chatApp, /addEventListener\("load", handleDeferredMedia, true\)/);
  assert.match(chatApp, /addEventListener\("loadedmetadata", handleDeferredMedia, true\)/);
  assert.match(chatApp, /addEventListener\("wheel", stopKeepingLatest/);
  assert.match(chatApp, /addEventListener\("pointerdown", stopKeepingLatest/);
  assert.match(chatApp, /addEventListener\("touchstart", stopKeepingLatest/);
});

test("keeps the legacy coordinate editor out while reusing only confirmed positions", async () => {
  const [attachmentDisplay, coordinateEditor, globalStyles, types] = await Promise.all(
    [
      readFile(
        new URL("../app/components/AttachmentDisplay.tsx", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL("../app/components/CoordinateTextEditor.tsx", import.meta.url),
        "utf8",
      ),
      readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
      readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
    ],
  );

  assert.doesNotMatch(attachmentDisplay, /<CoordinateTextEditor/);
  assert.match(attachmentDisplay, /className="handwriting-correction-dialog"/);
  assert.match(attachmentDisplay, /coordinate-review/);
  assert.match(attachmentDisplay, /placement_status !== "confirmed"/);
  assert.match(attachmentDisplay, /handwriting-correction-image-scroll/);
  assert.match(attachmentDisplay, /handwriting-confirmed-region/);
  assert.match(attachmentDisplay, /onFocusRegion=\{focusHandwritingRegion\}/);
  assert.match(attachmentDisplay, /직원이 확인한 글줄 위치를 강조했습니다/);
  assert.match(attachmentDisplay, /판독 입력 · \{extraction\.preprocessing\.input_label\}/);
  assert.match(attachmentDisplay, /원본 보존/);
  assert.match(attachmentDisplay, /직접 입력·마이크로 보완/);
  assert.match(attachmentDisplay, /extraction\.preprocessing\?\.output_blocked/);
  assert.match(types, /selected_variant: string/);
  assert.match(types, /output_blocked: boolean/);
  assert.match(globalStyles, /\.attachment-extraction-input-variant/);
  assert.match(globalStyles, /\.attachment-extraction-failure-actions/);
  assert.match(coordinateEditor, /글자 겹쳐 보기/);
  assert.match(
    coordinateEditor,
    /사진의 글자를 누르면 아래에서 바로 고칠 수 있습니다/,
  );
  assert.match(coordinateEditor, /올바른 글자/);
  assert.match(
    coordinateEditor,
    /Enter를 누르면 원문 위에 반영하고 다음 항목으로 이동합니다/,
  );
  assert.match(coordinateEditor, /applyAndGoNext/);
  assert.match(coordinateEditor, /review_required: false/);
  assert.match(coordinateEditor, /확인 필요 · 맞으면 Enter/);
  assert.match(coordinateEditor, /region\.review_required \? " uncertain"/);
  assert.match(coordinateEditor, /revealRegion/);
  assert.match(coordinateEditor, /placedRegions/);
  assert.match(coordinateEditor, /unplacedRegions/);
  assert.match(coordinateEditor, /사진에서 위치 지정/);
  assert.match(coordinateEditor, /synchronizeScroll/);
  assert.match(coordinateEditor, /\+ 상자 추가/);
  assert.match(coordinateEditor, /되돌리기/);
  assert.match(coordinateEditor, /상자 정리/);
  assert.match(coordinateEditor, /toolMenuOpen/);
  assert.match(coordinateEditor, /aria-expanded=\{toolMenuOpen\}/);
  assert.match(coordinateEditor, /onPointerDownCapture/);
  assert.match(coordinateEditor, /onClickCapture/);
  assert.match(coordinateEditor, /onKeyDownCapture/);
  assert.match(coordinateEditor, /onBlur/);
  assert.match(coordinateEditor, /closeToolMenuOnOutsidePointer/);
  assert.match(coordinateEditor, /event\.stopPropagation\(\)/);
  assert.match(coordinateEditor, /event\.preventDefault\(\)/);
  assert.match(coordinateEditor, /선택 상자 나누기/);
  assert.match(coordinateEditor, /splitRotatedBox/);
  assert.match(
    coordinateEditor,
    /기울기를 유지한 채 상자를 두 개로 나눴습니다/,
  );
  assert.match(coordinateEditor, /두 글자 이상인 상자를 선택해 주세요/);
  assert.doesNotMatch(
    coordinateEditor,
    /기울어진 상자는 0°로 맞춘 뒤 나눠 주세요/,
  );
  assert.match(coordinateEditor, /병합할 상자 선택/);
  assert.match(coordinateEditor, /상자 삭제/);
  assert.match(
    coordinateEditor,
    /글줄의 처음부터 끝까지 상자 안에 넣어 주세요/,
  );
  assert.match(coordinateEditor, /원본 보기/);
  assert.match(coordinateEditor, /글자 겹쳐 보기/);
  assert.match(coordinateEditor, /수정 내용 저장/);
  assert.match(coordinateEditor, /confirm_text: true/);
  assert.match(coordinateEditor, /coordinate-review\/auto-locate/);
  assert.match(coordinateEditor, /판독문이 적힌 위치를 자동으로 찾는 중입니다/);
  assert.match(coordinateEditor, /position_confidence/);
  assert.doesNotMatch(coordinateEditor, /resident_options/);
  assert.match(coordinateEditor, /tool !== "select"/);
  assert.match(
    coordinateEditor,
    /canvas\.setPointerCapture\(event\.pointerId\)/,
  );
  assert.match(coordinateEditor, /distance < 4/);
  assert.match(coordinateEditor, /글자 영역 위치를 옮겼습니다/);
  assert.match(coordinateEditor, /coordinate-editor-group-transform-5/);
  assert.match(coordinateEditor, /rotation_degrees/);
  assert.match(coordinateEditor, /coordinate-rotation-handle/);
  assert.match(coordinateEditor, /상자 기울기/);
  assert.match(coordinateEditor, /상자를 왼쪽으로 1도 기울이기/);
  assert.match(coordinateEditor, /상자를 오른쪽으로 1도 기울이기/);
  assert.match(coordinateEditor, /event\.ctrlKey \|\| event\.metaKey/);
  assert.match(coordinateEditor, /Ctrl\+클릭으로 여러 상자 선택/);
  assert.match(coordinateEditor, /개 상자를 같은 만큼 기울였습니다/);
  assert.match(coordinateEditor, /ROTATION_POINTER_SENSITIVITY = 0\.4/);
  assert.match(coordinateEditor, /safeSharedRotationDelta/);
  assert.match(coordinateEditor, /safeSharedMoveDelta/);
  assert.match(coordinateEditor, /moveOriginals/);
  assert.match(coordinateEditor, /개 상자를 함께 이동했습니다/);
  assert.match(coordinateEditor, /함께 이동·기울이도록 선택했습니다/);
  assert.match(globalStyles, /\.coordinate-region\.group-selected/);
  assert.match(coordinateEditor, /source: "manual"/);
  assert.match(coordinateEditor, /onPointerCancel=\{cancelGesture\}/);
  assert.match(
    globalStyles,
    /\.coordinate-editor-main[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/,
  );
  assert.match(globalStyles, /\.coordinate-quick-editor/);
  assert.match(globalStyles, /\.coordinate-unplaced button\.active/);
  assert.match(
    globalStyles,
    /@media \(max-width: 780px\)[\s\S]*grid-template-rows: minmax\(230px, 1fr\)/,
  );
  assert.match(
    globalStyles,
    /@media \(max-width: 780px\)[\s\S]*\.coordinate-region-editor[\s\S]*position: relative/,
  );
  assert.match(globalStyles, /\.overlay-canvas\.show-overlay img/);
  assert.match(
    globalStyles,
    /\.overlay-canvas\.show-overlay img\s*\{\s*opacity: 0\.82/,
  );
  assert.match(
    globalStyles,
    /\.coordinate-region\s*\{[\s\S]*background: rgb\(255 255 255 \/ 62%\)/,
  );
  assert.match(globalStyles, /\.coordinate-rotation-handle/);
  assert.match(
    globalStyles,
    /\.coordinate-resize-handle\s*\{[\s\S]*width: 24px/,
  );
  assert.match(globalStyles, /\.coordinate-tool-menu/);
  assert.match(globalStyles, /\.coordinate-box-actions/);
  assert.match(
    globalStyles,
    /\.coordinate-overlay\s*\{[\s\S]*?touch-action:\s*pan-x pan-y;/,
  );
  assert.match(
    globalStyles,
    /\.coordinate-overlay\.tool-add,[\s\S]*?\.coordinate-overlay\.tool-place\s*\{[\s\S]*?touch-action:\s*none;/,
  );
  assert.match(
    globalStyles,
    /\.coordinate-editor-tools\s*\{[\s\S]*?overflow:\s*visible;/,
  );
  assert.match(coordinateEditor, /마지막 상자는 삭제할 수 없습니다/);
  assert.match(coordinateEditor, /disabled=\{regions\.length <= 1\}/);
  assert.doesNotMatch(globalStyles, /\.coordinate-overlay\.tool-move/);
});

test("keeps admin connection management behind central model settings", async () => {
  const [panel, central, chatApp, styles] = await Promise.all([
    readFile(new URL("../app/components/AiConnectionPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/CentralModelsPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(panel, /type="password"/);
  assert.match(panel, /setCredential\(""\)/);
  assert.match(panel, /settings_write_allowed === false/);
  assert.match(panel, /\/connect/);
  assert.doesNotMatch(panel + central, /localStorage|sessionStorage|settings\/model-roles/);
  assert.match(central, /central-models\/catalog/);
  assert.match(central, /central-models\/verify-image/);
  assert.match(central, /현재 저장값, 목록 미확인/);
  assert.match(central, /기존 안전한 판독 경로/);
  assert.match(central, /showModal/);
  assert.match(central, /onCancel/);
  assert.match(chatApp, /showTechnicalDetails=\{me.role === "admin"\}/);
  assert.match(chatApp, /AI 설정 및 연결/);
  assert.match(styles, /ai-settings-simple/);
  assert.match(styles, /ai-model-dialog/);
});

test("shows model, processing location, external transfer, fallback, and review metadata", async () => {
  const [panel, types, globalStyles] = await Promise.all([
    readFile(new URL("../app/components/AiAssistPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(panel, /processingLocationLabels\[turn\.processing_location\]/);
  assert.match(panel, /외부 전송 \{turn\.external_transmission/);
  assert.match(panel, /Fallback 적용/);
  assert.match(panel, /규칙 기반 즉시 결과/);
  assert.match(panel, /showTechnicalDetails \? "전환 사유: " \+ turn\.fallback_reason/);
  assert.match(panel, /const latestResultKey = activeTurn\s*\? activeTurn\.id/);
  assert.match(panel, /사람의 최종 확인 필요/);
  assert.match(panel, /enhancementLabels\[turn\.enhancement_status\]/);
  assert.match(panel, /민감자료 외부 전송 기본 차단/);
  assert.match(types, /processing_location:/);
  assert.match(types, /deidentification_status:/);
  assert.match(types, /fallback_reason: string \| null/);
  assert.match(types, /human_review_required: true/);
  assert.match(globalStyles, /\.ai-assist-route-meta/);
  assert.match(globalStyles, /\.ai-assist-fallback-reason/);
});

test("selects a resident immediately while preserving the original list position and optional drafts", async () => {
  const periodWorkDesk = await readFile(new URL("../app/components/PeriodWorkDesk.tsx", import.meta.url), "utf8");
  const changeResidentScope = periodWorkDesk.slice(periodWorkDesk.indexOf("function changeResidentScope"), periodWorkDesk.indexOf("function togglePlanningHelper"));
  assert.match(changeResidentScope, /setResidentId\(nextResidentId\)/);
  assert.match(changeResidentScope, /loadReview\(\{ residentId: nextResidentId \}\)/);
  assert.match(changeResidentScope, /overallScrollRef.current/);
  assert.match(periodWorkDesk, /scrollPositionRef.current/);
  assert.match(periodWorkDesk, /if \(!review\) void loadReview\(\)/);
  assert.match(periodWorkDesk, /aria-label="도우미 작성 대상"/);
  assert.match(periodWorkDesk, /setPlanningResidentId\(event.target.value\)/);
  assert.match(periodWorkDesk, /resident=\{planningResident\}/);
});

test("shows a factual date-resident-time briefing and keeps official record selection out of the live flow", async () => {
  const [periodWorkDesk, types, globalStyles] = await Promise.all([
    readFile(
      new URL("../app/components/PeriodWorkDesk.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(periodWorkDesk, /현장 기록형 돌봄 브리핑/);
  assert.match(periodWorkDesk, /선택 기간 전체 요약/);
  assert.match(periodWorkDesk, /review\.daily_care_references\.map/);
  assert.match(periodWorkDesk, /day\.residents\.map/);
  assert.match(periodWorkDesk, /resident\.entries\.map/);
  assert.match(periodWorkDesk, /entry\.time_label/);
  assert.match(periodWorkDesk, /상담일지 참고 내용/);
  assert.match(periodWorkDesk, /review\.consultation_references\.length/);
  assert.match(periodWorkDesk, /저장된 브리핑 다시 열기/);
  assert.match(periodWorkDesk, /save_history: saveHistory/);
  assert.match(periodWorkDesk, /legacyDocumentSelectionVisible = false/);
  assert.match(
    periodWorkDesk,
    /legacyDocumentSelectionVisible[\s\S]*?renderFieldCareBriefing\(\)/,
  );
  assert.match(periodWorkDesk, /실제 공식 기록은 직원이 기존 업무 방식대로 직접 작성/);
  assert.match(periodWorkDesk, /공식 입력, 서명 또는 공단 전송은 실행하지 않습니다/);
  assert.match(types, /daily_care_references: FieldCareBriefingDay\[\]/);
  assert.match(types, /history_revision: number \| null/);
  assert.match(globalStyles, /\.field-briefing-resident/);
  assert.match(globalStyles, /\.field-briefing-line > p/);
  const fieldBriefing = periodWorkDesk.slice(
    periodWorkDesk.indexOf("function renderFieldCareBriefing"),
    periodWorkDesk.indexOf("if (!open) return null"),
  );
  assert.doesNotMatch(fieldBriefing, /renderFieldSocialWorkerGuidance/);
  assert.match(fieldBriefing, /renderFieldEvidence\(entry.evidence_ids\)/);
  assert.doesNotMatch(
    fieldBriefing,
    /pending_checks|final_status|다음 조치|완료·경과 관찰/,
  );
});

test("reuses the full DEV interface for mentor review while locking risky actions", async () => {
  const [chatApp, adminDrawer, aiPanel, securityPanel, notificationPanel, aiAssist, types, styles] =
    await Promise.all([
      readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/components/AdminDrawer.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/components/AiConnectionPanel.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/components/SecurityPanel.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/components/NotificationSoundPanel.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/components/AiAssistPanel.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    ]);

  assert.match(types, /"mentor_full"/);
  assert.match(chatApp, /멘토 전체 기능 검토 · DEV 가상자료/);
  assert.match(chatApp, /const canViewAdmin = me\?\.role === "admin" \|\| mentorFullReview/);
  assert.match(chatApp, /reviewOnly=\{mentorFullReview\}/);
  assert.match(chatApp, /AI 돌봄 브리핑/);
  assert.match(chatApp, /새 파일 업로드 · 검토 잠금/);
  assert.match(chatApp, /통화 · 검토 잠금/);
  assert.match(adminDrawer, /합성자료 읽기 전용/);
  assert.match(adminDrawer, /추가·변경·삭제·Carefor 동기화는 서버에서도 차단/);
  assert.match(aiPanel, /현재 계정에서는 확인만 할 수 있습니다/);
  assert.match(aiPanel, /reviewOnly \|\| status\?\.settings_write_allowed === false/);
  assert.match(aiPanel, /if \(readOnly\) return/);
  assert.match(securityPanel, /현재 적용된 보안 경계/);
  assert.match(securityPanel, /mentor-security-review desktop-resizable-dialog/);
  assert.match(securityPanel, /mandatory \? "" : "desktop-resizable-dialog"/);
  assert.match(securityPanel, /비밀번호·세션 식별자·내부 주소·인증 토큰 원문은 표시하지 않습니다/);
  assert.match(notificationPanel, /푸시 등록과 설정 변경은 멘토 계정에서 잠겨/);
  assert.match(aiAssist, /대화방 공유 · 검토 잠금/);
  assert.match(styles, /\.mentor-review-readonly-banner/);
  assert.match(styles, /\.mentor-policy-grid/);
  assert.doesNotMatch(chatApp, /멘토 전용 축약/);
});

test("guides staff through one evidence-first care-planning review", async () => {
  const [planningPanel, intakeWorkspace] = await Promise.all([
    readFile(
      new URL("../app/components/ResidentCarePlanningPanel.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../app/components/AssessmentDraftIntakeWorkspace.tsx", import.meta.url),
      "utf8",
    ),
  ]);

  for (const label of [
    "1. 자료 제출",
    "2. 근거·변화 확인",
    "3. 기초사정 검토",
    "4. 급여계획 검토",
    "5. 저장·출력",
  ]) {
    assert.match(planningPanel, new RegExp(label.replace(/[.]/g, "\\.")));
  }
  assert.match(planningPanel, /기초사정·급여제공계획 검토/);
  assert.match(planningPanel, /작성 기준과 안전 안내/);
  assert.match(planningPanel, /근거 요약 보기/);
  assert.match(planningPanel, /검토 순서 \{activeStepNumber\}\/\{workspace\.documents\.length\}/);
  assert.match(intakeWorkspace, /자료 분석하고 편집용 초안 열기/);
  assert.doesNotMatch(planningPanel, /종합보고서와 5종 양식 초안/);
  assert.doesNotMatch(planningPanel, /종합보고서와 5종 양식을 확인합니다/);
  assert.doesNotMatch(planningPanel, /5종 양식 보고서 인쇄·PDF/);
  assert.doesNotMatch(planningPanel, /건 후보<\/span>/);
  assert.match(planningPanel, /변경된 항목만 보기/);
  assert.match(planningPanel, /전체 항목 보기/);
  assert.match(planningPanel, /comparison_items/);
  assert.match(planningPanel, /baseline_evidence_refs/);
  assert.match(planningPanel, /current_evidence_refs/);
  assert.match(planningPanel, /직원 재검토/);
  assert.match(planningPanel, /workflowStage === 5/);
  assert.match(planningPanel, /모바일에서는 요약과 근거만 확인할 수 있습니다/);
  assert.match(planningPanel, /care-planning-pc-only-notice/);
});

test("persists selected care changes and opens concise evidence without losing history", async () => {
  const planningPanel = await readFile(
    new URL("../app/components/ResidentCarePlanningPanel.tsx", import.meta.url),
    "utf8",
  );

  assert.match(planningPanel, /selected_change_field_keys/);
  assert.match(planningPanel, /선택 내용 저장/);
  assert.match(planningPanel, /근거 상세 닫기/);
  assert.match(planningPanel, /evidence_summary/);
  assert.match(planningPanel, /reference_locator/);
  assert.match(planningPanel, /supersedes_revision: latestRevision\.revision/);
  assert.match(planningPanel, /item\.classification === "material_conflict"/);
});

test("keeps resident and staff spreadsheet transfer separate and preview-first", async () => {
  const [adminDrawer, styles] = await Promise.all([
    readFile(new URL("../app/components/AdminDrawer.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(adminDrawer, /function BulkTransferPanel/);
  assert.match(adminDrawer, /entity: BulkTransferEntity/);
  assert.match(adminDrawer, /1\. 예제 엑셀 받기/);
  assert.match(adminDrawer, /2\. 현재 자료 받기/);
  assert.match(adminDrawer, /3\. 수정한 엑셀 올리기/);
  assert.match(adminDrawer, /올린 내용은 바로 저장되지 않으며/);
  assert.match(adminDrawer, /새로 추가/);
  assert.match(adminDrawer, /정보 수정/);
  assert.match(adminDrawer, /이용 종료·퇴사/);
  assert.match(adminDrawer, /오류·충돌/);
  assert.match(adminDrawer, /5\. 선택한 내용 적용/);
  assert.match(adminDrawer, /item_ids: selectedIds/);
  assert.match(adminDrawer, /entity="staff"/);
  assert.match(adminDrawer, /entity="resident"/);
  assert.match(adminDrawer, /disabled=\{reviewOnly\}/);
  assert.doesNotMatch(adminDrawer, /비밀번호.*incoming_payload|incoming_payload.*비밀번호/);
  assert.match(styles, /\.bulk-transfer-panel/);
  assert.match(styles, /@media \(max-width: 720px\)[\s\S]*?\.bulk-transfer-row/);
});

test("opens a room file collection from the chat header without exposing edit controls", async () => {
  const [chatApp, roomFiles, styles] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/RoomFilesOverlay.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(chatApp, /room-files-open/);
  assert.match(chatApp, />\s*파일\s*</);
  assert.match(chatApp, /<RoomFilesOverlay/);
  assert.match(roomFiles, /\/message-search\?\$\{params\.toString\(\)\}/);
  assert.match(roomFiles, /사진/);
  assert.match(roomFiles, /음성/);
  assert.match(roomFiles, /문서·파일/);
  assert.match(roomFiles, /최신순/);
  assert.match(roomFiles, /오래된순/);
  assert.match(roomFiles, /대화로 이동/);
  assert.match(roomFiles, /showExtraction=\{false\}/);
  assert.match(roomFiles, /canEditExtraction=\{false\}/);
  assert.match(roomFiles, /showOriginalName=\{false\}/);
  assert.match(roomFiles, /showCompactShare/);
  assert.match(roomFiles, /date_from/);
  assert.match(roomFiles, /date_to/);
  assert.match(styles, /\.room-files-grid\.single/);
  assert.match(styles, /@media \(max-width: 720px\)[\s\S]*?\.room-files-card/);
});

test("keeps the processor queue focused on exceptional records", async () => {
  const workDesk = await readFile(
    new URL("../app/components/WorkDesk.tsx", import.meta.url),
    "utf8",
  );

  assert.match(workDesk, /reviewQueueOnly = true/);
  assert.match(workDesk, /"\/api\/work-items\?review_queue_only=true"/);
  assert.match(workDesk, /reviewQueueOnly[\s\S]*?"\/api\/work-items"/);
});

test("keeps the mobile chat title and message body readable at phone width", async () => {
  const styles = await readFile(
    new URL("../app/globals.css", import.meta.url),
    "utf8",
  );

  assert.match(
    styles,
    /@media \(max-width: 720px\)[\s\S]*?\.chat-header > div:first-of-type[\s\S]*?min-width: 0/,
  );
  assert.match(
    styles,
    /@media \(max-width: 720px\)[\s\S]*?\.chat-header h2[\s\S]*?text-overflow: ellipsis/,
  );
  assert.match(
    styles,
    /@media \(max-width: 720px\)[\s\S]*?\.chat-header-actions[\s\S]*?grid-column: 1 \/ -1/,
  );
  assert.match(
    styles,
    /@media \(max-width: 720px\)[\s\S]*?\.message-stack[\s\S]*?width: calc\(100% - 54px\)/,
  );
  assert.match(
    styles,
    /@media \(max-width: 720px\)[\s\S]*?\.message-bubble[\s\S]*?flex: 1 1 auto/,
  );
});

test("lets a room caller choose recipients and confirms the participant count", async () => {
  const [chatApp, callChoice, voiceCall, styles] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/CallChoiceSheet.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../app/voiceCall.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(chatApp, /\/api\/rooms\/\$\{activeRoom\.id\}\/members/);
  assert.match(chatApp, /member\.id !== currentUserId/);
  assert.match(chatApp, /setSelectedRoomCallMemberIds/);
  assert.match(chatApp, /\$\{participantCount\}명이 참여하는 \$\{callLabel\}/);
  assert.match(chatApp, /startVoiceCall\(room, mode, selectedRoomCallMemberIds\)/);
  assert.match(callChoice, /통화할 직원 선택/);
  assert.match(callChoice, /모두 선택/);
  assert.match(callChoice, /발신자를 포함해 \{participantCount\}명/);
  assert.match(callChoice, /통화할 직원을 줄여 주세요/);
  assert.match(voiceCall, /invitePayload\.recipient_user_ids = recipientUserIds/);
  assert.match(styles, /\.call-member-picker/);
});

test("does not offer a staff call action while voice calls are unavailable", async () => {
  const [chatApp, staffContactSheet] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/StaffContactSheet.tsx", import.meta.url),
      "utf8",
    ),
  ]);

  assert.match(chatApp, /callAvailable=\{voiceCall\.available\}/);
  assert.match(staffContactSheet, /callAvailable: boolean/);
  assert.match(staffContactSheet, /\{callAvailable \? \([\s\S]*?<strong>통화<\/strong>[\s\S]*?\) : null\}/);
});

test("renders recalled chat messages through one neutral early-return shell", async () => {
  const [chatApp, styles, adminReview] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(
      new URL("../app/components/AdminConversationReview.tsx", import.meta.url),
      "utf8",
    ),
  ]);

  const recalledStart = chatApp.indexOf(
    "if (message.is_recalled) {\n                    return (",
  );
  assert.ok(recalledStart >= 0, "회수 상태가 일반 메시지 종류보다 먼저 분기되어야 합니다.");
  const normalStart = chatApp.indexOf(
    'message.message_type === "notice"',
    recalledStart,
  );
  assert.ok(normalStart > recalledStart);
  const recalledBranch = chatApp.slice(recalledStart, normalStart);

  assert.match(recalledBranch, /recalled-message-row/);
  assert.match(recalledBranch, />회수한 메시지입니다\.<\/span>/);
  for (const forbidden of [
    "ResidentLinkReview",
    "MessageActionBadge",
    "AttachmentDisplay",
    "LatestCommentPreview",
    "reply-quote",
    "forwarded-label",
    "message-engagement",
    "openMessageDetail",
    "renderMessageActions",
    "<button",
  ]) {
    assert.doesNotMatch(recalledBranch, new RegExp(forbidden));
  }

  assert.match(chatApp, /function maskRecalledMessage\(/);
  assert.match(
    chatApp,
    /recalledIds\.has\(message\.id\)[\s\S]*?maskRecalledMessage\(message/,
  );
  assert.match(
    chatApp,
    /item\.id === message\.id[\s\S]*?maskRecalledMessage\(item/,
  );
  assert.match(
    chatApp,
    /messagesRef\.current\.some\([\s\S]*?message\.is_recalled[\s\S]*?\)\) return;/,
  );
  assert.match(
    styles,
    /\.message-bubble\.recalled\s*\{[\s\S]*?background:[\s\S]*?box-shadow:\s*none/,
  );
  assert.match(
    styles,
    /\.message-bubble\.recalled\s*\{[\s\S]*?flex:\s*0 1 auto/,
  );
  assert.match(
    styles,
    /\.message-stack\.recalled-message-stack\s*\{[\s\S]*?width:\s*fit-content/,
  );

  // The separately authenticated audit surface must keep rendering retained originals.
  assert.match(adminReview, /\/api\/admin\/conversation-access/);
  assert.match(adminReview, /회수됨 · 원본 보존/);
  assert.match(adminReview, /<AttachmentDisplay/);
});

test("starts the outgoing wait tone from the call gesture and stops it on every setup failure", async () => {
  const voiceCall = await readFile(
    new URL("../app/voiceCall.ts", import.meta.url),
    "utf8",
  );

  const startCallBody = voiceCall.match(
    /const startCall = useCallback\([\s\S]*?const acceptIncoming = useCallback/,
  )?.[0] ?? "";
  assert.match(
    startCallBody,
    /await prepareCallAudio\(\)[\s\S]*?startOutgoingCallTone\(\)[\s\S]*?await refreshConfig\(\)/,
  );
  assert.match(startCallBody, /if \(!currentConfig\.enabled\) \{[\s\S]*?stopCallTone\(\)/);
  assert.match(startCallBody, /catch \(reason\) \{\s*stopCallTone\(\)/);
  assert.match(voiceCall, /const outgoingCallWaiting =/);
  assert.doesNotMatch(voiceCall, /\.includes\(active\.status\)/);
});

test("keeps two-pane administration independently scrollable and preserves the selected item on mobile", async () => {
  const [adminDrawer, styles] = await Promise.all([
    readFile(new URL("../app/components/AdminDrawer.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(adminDrawer, /drawer-content-two-pane/);
  assert.match(adminDrawer, /mobilePane/);
  assert.match(adminDrawer, /직원 목록으로/);
  assert.match(adminDrawer, /setMobilePane\("employee"\)/);
  assert.match(adminDrawer, /setMobilePane\("room"\)/);
  assert.match(adminDrawer, /employeeDetailRef\.current\?\.scrollTo\(\{ top: 0/);
  assert.match(adminDrawer, /roomDetailRef\.current\?\.scrollTo\(\{ top: 0/);
  assert.match(styles, /@media \(min-width: 721px\)[\s\S]*?\.drawer-content-two-pane[\s\S]*?overflow:\s*hidden/);
  assert.match(styles, /@media \(min-width: 721px\)[\s\S]*?\.drawer-content-two-pane \.admin-master-pane,[\s\S]*?\.drawer-content-two-pane \.admin-detail-pane[\s\S]*?overflow-y:\s*auto/);
  assert.match(styles, /@media \(max-width: 720px\)[\s\S]*?\.admin-management-grid\.mobile-detail-open \.admin-master-pane[\s\S]*?display:\s*none/);
  assert.match(styles, /@media \(max-width: 720px\)[\s\S]*?\.employee-list-back[\s\S]*?display:\s*inline-flex/);
});

test("labels record-processing access accurately without changing administrator access", async () => {
  const [adminDrawer, chatApp] = await Promise.all([
    readFile(new URL("../app/components/AdminDrawer.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(adminDrawer, /돌봄기록 처리·AI 브리핑 사용/);
  assert.match(adminDrawer, /직원·어르신·기관·채팅방 관리는 관리자 계정만 사용할 수 있습니다/);
  assert.match(adminDrawer, /checked=\{editDraft\.can_process_records\}/);
  assert.match(chatApp, /const canViewAdmin = me\?\.role === "admin" \|\| mentorFullReview/);
  assert.match(chatApp, /me\.role === "admin" \|\| me\.can_process_records/);
});

test("keeps the ordinary-staff composer inside the available width without the long format guide", async () => {
  const [chatApp, styles] = await Promise.all([
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.doesNotMatch(chatApp, /SUPPORTED_ATTACHMENT_GUIDE|file-picker-supported-formats/);
  assert.match(chatApp, /composer-chat-first-note/);
  assert.doesNotMatch(styles, /\.composer-chat-first-note\s*\{[^}]*flex:\s*1 0 100%/);
  assert.match(styles, /\.composer-tools\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*flex-wrap:\s*wrap/);
  assert.match(styles, /\.composer-chat-first-note\s*\{[^}]*flex:\s*1 1 100%[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*overflow-wrap:\s*anywhere/);
});

test("manages all resident links through the clickable names and one accessible save", async () => {
  const [review, chatApp, styles] = await Promise.all([
    readFile(new URL("../app/components/ResidentLinkReview.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(review, /confirmed\.map\(\(resident\)/);
  assert.match(review, /canEdit \? \(/);
  assert.match(review, /<span key=\{resident\.id\} className="resident-chip confirmed">/);
  assert.match(review, /\$\{resident\.display_name\} 어르신 연결 관리 열기/);
  assert.match(review, /canEdit && confirmed\.length === 0/);
  assert.match(review, />\s*어르신 추가\s*</);
  assert.match(review, /decision,\s*resident_ids:/s);
  assert.match(review, />선택한 연결 저장</);
  assert.match(review, /aria-pressed=\{selected\}/);
  assert.doesNotMatch(review, />\s*어르신 연결 수정\s*</);
  assert.doesNotMatch(review, /자동 연결됨/);
  assert.match(chatApp, /metadataRefreshesRef/);
  assert.match(chatApp, /mesil-resident-links-changed/);
  assert.match(chatApp, /residentLinkPositionRef/);
  assert.match(chatApp, /onManagerOpen=\{captureResidentLinkPosition\}/);
  assert.match(chatApp, /onManagerClose=\{restoreResidentLinkPosition\}/);
  assert.doesNotMatch(chatApp, /canEdit=\{mine \|\| me\.role === "admin" \|\| me\.can_process_records\}/);
  assert.match(review, /focus\(\{ preventScroll: true \}\)/);
  assert.match(review, /reason\.status === 401/);
  assert.match(review, /reason\.status === 403/);
  assert.match(review, /reason\.status === 404/);
  assert.match(styles, /\.resident-review-options button[\s\S]*?min-height:\s*48px/);
});

test("updates unread badges immediately without marking a hidden active room as read", async () => {
  const chatApp = await readFile(
    new URL("../app/components/ChatApp.tsx", import.meta.url),
    "utf8",
  );

  assert.match(chatApp, /const roomRefreshRequestRef = useRef\(0\)/);
  assert.match(chatApp, /const requestId = \+\+roomRefreshRequestRef\.current/);
  assert.match(chatApp, /if \(requestId !== roomRefreshRequestRef\.current\) return roomsRef\.current/);
  assert.match(chatApp, /const isActiveRoomVisible =[^;]*document\.visibilityState === "visible"/s);
  assert.match(chatApp, /incrementRoomUnreadCount/);
  assert.match(chatApp, /incoming\.sender_id !== currentUserId/);
  assert.match(chatApp, /sleptFor > 0[\s\S]*?activeRoom\?\.unread_count \?\? 0[\s\S]*?markRead/);
});

test("increments only the target room unread count", () => {
  const rooms = [
    { id: "room-a", unread_count: 2, last_message_at: null },
    { id: "room-b", unread_count: 5, last_message_at: null },
  ];

  const next = incrementRoomUnreadCount(rooms, "room-a");

  assert.deepEqual(next.map((room) => room.unread_count), [3, 5]);
  assert.notEqual(next, rooms);
  assert.equal(incrementRoomUnreadCount(rooms, "missing"), rooms);
});

test("keeps test and browser artifacts out of the production frontend image", async () => {
  const dockerignore = await readFile(new URL("../.dockerignore", import.meta.url), "utf8");
  for (const entry of ["tests", "output", "test-results"]) {
    assert.match(dockerignore, new RegExp(`^${entry}$`, "m"));
  }
});

test("shows only verified local AI as an answer and keeps model failures honest", async () => {
  const reader = await readFile(
    new URL("../app/components/CareBriefingReader.tsx", import.meta.url),
    "utf8",
  );

  assert.match(reader, /로컬 AI/);
  assert.match(reader, /AI 답변 미완성/);
  assert.match(reader, /이번에는 AI 답변을 완성하지 못했습니다/);
  assert.doesNotMatch(reader, /기록 기반 요약/);
  assert.match(reader, /답변을 정리하고 있습니다/);
  assert.match(reader, /답변과 근거를 확인하고 있습니다/);
  assert.match(reader, /다시 질문하기/);
  assert.match(reader, /openEvidence\(sentence\.evidence_ids,\s*answer\.period_start,\s*answer\.period_end,\s*answer\.resident_id/);
  assert.match(reader, /openEvidence\(answer\.evidence_ids,\s*answer\.period_start,\s*answer\.period_end,\s*answer\.resident_id/);
});

test("uses backend attachment capabilities for document review on PC and Android", async () => {
  const [types, detail, display, staffReview] = await Promise.all([
    readFile(new URL("../app/types.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/components/MessageDetailOverlay.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/AttachmentDisplay.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/StaffReviewedAttachment.tsx", import.meta.url), "utf8"),
  ]);

  for (const capability of [
    "can_download_original",
    "can_view_reviewed_text",
    "can_review_text",
    "can_request_reading",
  ]) {
    assert.match(types, new RegExp(`${capability}\\?: boolean`));
  }
  assert.doesNotMatch(detail, /canProcessRecords \|\| attachment\.uploader_id === currentUserId/);
  assert.match(detail, /attachment\.can_review_text === true/);
  assert.match(display, /attachment\.can_download_original === false/);
  assert.match(display, /canRequestReading=\{attachment\.can_request_reading === true\}/);
  assert.match(display, /canViewReviewedText=\{attachment\.can_view_reviewed_text === true\}/);
  assert.match(staffReview, /canRequestReading/);
  assert.match(staffReview, /canViewReviewedText/);
  assert.match(staffReview, /로그인 정보를 다시 확인해 주세요\./);
  assert.match(staffReview, /이 문서의 내용을 확인·수정할 권한이 없습니다/);
  assert.match(staffReview, /현재 이 대화방에 접근할 수 없습니다\./);
});

test("exposes the existing record-candidate review desk only in coded synthetic presentation mode", async () => {
  const chatApp = await readFile(
    new URL("../app/components/ChatApp.tsx", import.meta.url),
    "utf8",
  );

  assert.match(chatApp, /import \{ WorkDesk \} from "\.\/WorkDesk"/);
  assert.match(chatApp, /const \[recordReviewOpen, setRecordReviewOpen\] = useState\(false\)/);
  assert.match(
    chatApp,
    /codedSyntheticMode && canViewAdmin[\s\S]*?기록 검토/,
  );
  assert.match(
    chatApp,
    /<WorkDesk[\s\S]*?open=\{recordReviewOpen\}[\s\S]*?reviewQueueOnly=\{false\}[\s\S]*?setRecordReviewOpen\(false\)/,
  );
});
