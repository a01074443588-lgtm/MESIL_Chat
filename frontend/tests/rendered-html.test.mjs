import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

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
  assert.match(html, /채팅방을 준비하고 있습니다/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|taking shape/i);
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

test("keeps product metadata, security flow, and PWA assets aligned", async () => {
  const [page, layout, globalStyles, api, loginScreen, reviewerPage, reviewerExperience, reviewerLanding, chatApp, periodWorkDesk, messageDetail, attachmentDisplay, roomSearch, securityPanel, adminDrawer, notificationSound, pushNotifications, notificationPanel, packageJson, manifestText, serviceWorker] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/api.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/components/LoginScreen.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/reviewer/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ReviewerExperience.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/reviewerLanding.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/components/ChatApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/PeriodWorkDesk.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/MessageDetailOverlay.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/AttachmentDisplay.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/RoomSearchOverlay.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/SecurityPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/AdminDrawer.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/notificationSound.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/pushNotifications.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/components/NotificationSoundPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../public/mesil-chat.webmanifest", import.meta.url), "utf8"),
    readFile(new URL("../public/mesil-chat-sw-v6.js", import.meta.url), "utf8"),
  ]);

  const manifest = JSON.parse(manifestText);
  assert.match(page, /<ChatApp \/>/);
  assert.match(layout, /MESIL_Chat/);
  assert.match(layout, /interactiveWidget:\s*"resizes-content"/);
  assert.match(chatApp, /window\.visualViewport/);
  assert.match(chatApp, /--app-viewport-height/);
  assert.match(globalStyles, /height:\s*var\(--app-viewport-height,\s*100dvh\)/);
  assert.match(globalStyles, /\.composer\s*\{[\s\S]*?flex:\s*0 0 auto/);
  assert.match(chatApp, /must_change_password/);
  assert.match(chatApp, /loginNotice/);
  assert.match(chatApp, /파일을 보내는 중/);
  assert.match(chatApp, /서버에 안전하게 저장하는 중/);
  assert.match(api, /XMLHttpRequest/);
  assert.match(api, /apiUpload/);
  assert.match(api, /"\/api\/auth\/login"/);
  assert.match(api, /"\/api\/auth\/logout"/);
  assert.match(loginScreen, /sessionNotice/);
  assert.match(loginScreen, /내부 데모\(가명 자료\)/);
  assert.match(loginScreen, /href="\/reviewer"/);
  assert.match(globalStyles, /\.login-demo-label/);
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
  assert.match(securityPanel, /\/api\/auth\/sessions/);
  assert.match(adminDrawer, /reset-password/);
  assert.match(adminDrawer, /재직 복구/);
  assert.match(adminDrawer, /직원 삭제/);
  assert.match(adminDrawer, /method: "DELETE"/);
  assert.match(adminDrawer, /active_staff_count/);
  assert.match(adminDrawer, /직위로 옮길 직원/);
  assert.match(adminDrawer, /legacyPositionByJobCode/);
  assert.match(adminDrawer, /\/api\/position-titles/);
  assert.match(adminDrawer, /직위 추가/);
  assert.match(adminDrawer, /완전 삭제/);
  assert.match(adminDrawer, /roomKindLabels/);
  assert.match(adminDrawer, /resident-sync/);
  assert.match(adminDrawer, /케어포 명단 갱신/);
  assert.match(adminDrawer, /최신 명단 확인/);
  assert.match(adminDrawer, /확보한 가명 직원 명단 확인/);
  assert.match(adminDrawer, /명단 갱신으로 이동/);
  assert.match(adminDrawer, /staffUnitTypes/);
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
  assert.equal(manifest.icons.length, 2);
  assert.match(manifest.icons[0].src, /mesil-chat-192-v3\.png/);
  assert.match(manifest.icons[1].src, /mesil-chat-512-v3\.png/);
  assert.match(serviceWorker, /mesil-chat-shell-v10/);
  assert.match(serviceWorker, /url\.pathname\.startsWith\("\/assets\/"\)/);
  assert.match(serviceWorker, /offline\.html/);
  assert.match(serviceWorker, /showNotification/);
  assert.match(serviceWorker, /isTestNotification/);
  assert.match(serviceWorker, /notificationclick/);
  assert.match(chatApp, /알림 설정/);
  assert.match(notificationSound, /typeof window === "undefined"\) return "all"/);
  assert.match(pushNotifications, /synchronizeWebPushSubscription/);
  assert.match(pushNotifications, /registerSubscription\(subscription\)/);
  assert.match(chatApp, /synchronizeWebPushSubscription/);
  assert.match(notificationPanel, /휴대전화 알림 켜고 시험하기/);
  assert.match(notificationPanel, /sendWebPushTest/);
  assert.match(notificationPanel, /메딕 시험 소리 듣기/);
  assert.match(notificationSound, /\/sounds\/mesil-medic-voice-v2\.wav/);
  assert.match(chatApp, /<PeriodWorkDesk/);
  assert.match(
    chatApp,
    /className="icon-button workdesk-button"[\s\S]*?>\s*AI 돌봄 브리핑\s*<\/button>/,
  );
  assert.match(periodWorkDesk, /오늘의 돌봄 브리핑/);
  assert.match(periodWorkDesk, /이 범위로 브리핑 만들기/);
  assert.match(periodWorkDesk, /먼저 확인할 어르신/);
  assert.match(periodWorkDesk, /무엇이 달라졌나요/);
  assert.match(periodWorkDesk, /왜 확인해야 하나요/);
  assert.match(periodWorkDesk, /이미 한 일/);
  assert.match(periodWorkDesk, /아직 확인할 일/);
  assert.match(periodWorkDesk, /기록 활용 후보/);
  assert.match(periodWorkDesk, /선택 대화 AI 정리/);
  assert.match(periodWorkDesk, /선택 원문 인쇄·PDF/);
  assert.match(periodWorkDesk, /record-summary/);
  assert.match(periodWorkDesk, /그밖의 어르신/);
  assert.match(periodWorkDesk, /근거 대화 접기/);
  assert.match(periodWorkDesk, /toggleEvidence/);
  assert.doesNotMatch(periodWorkDesk, /전체 대화 AI 요약/);
  assert.doesNotMatch(periodWorkDesk, /이 기간의 업무대화/);
  assert.ok(
    periodWorkDesk.indexOf("먼저 확인할 어르신") <
      periodWorkDesk.indexOf("기록 활용 후보"),
  );
  assert.doesNotMatch(periodWorkDesk, /작성 가능한 일지 초안/);
  assert.match(periodWorkDesk, /enhance_summary:\s*false/);
  assert.doesNotMatch(periodWorkDesk, /enhance_summary:\s*true/);
  assert.doesNotMatch(periodWorkDesk, /window\.open/);
  assert.match(periodWorkDesk, /document\.createElement\("iframe"\)/);
  assert.match(periodWorkDesk, /afterprint/);
  assert.match(periodWorkDesk, /@page \{/);
  assert.match(periodWorkDesk, /size: A4/);
  assert.match(periodWorkDesk, /counter\(page\)/);
  assert.doesNotMatch(
    periodWorkDesk,
    /escapePrintText\(\s*recordSummary\.generator/,
  );
  assert.match(periodWorkDesk, /controller\.abort\(\), 55_000/);
  assert.match(periodWorkDesk, /Nemotron Ultra 550B/);
  assert.match(periodWorkDesk, /안전 정리 사용/);
  assert.doesNotMatch(periodWorkDesk, /<footer>MESIL_Chat/);
  assert.doesNotMatch(periodWorkDesk, /footer \{ position: fixed/);
  assert.doesNotMatch(periodWorkDesk, /period-workdesk-printing/);
  assert.match(globalStyles, /\.period-workdesk-card/);
  assert.match(chatApp, /읽음 \{message\.read_count\}/);
  assert.match(chatApp, /message\.reply_user_count/);
  assert.match(globalStyles, /\.message-engagement/);
  assert.match(globalStyles, /\.care-briefing-card/);
  assert.match(chatApp, /대화 검색/);
  assert.match(chatApp, /인수인계/);
  assert.match(chatApp, /업무협조/);
  assert.match(chatApp, /보고/);
  assert.doesNotMatch(chatApp, /업무지정 해제/);
  assert.match(messageDetail, /읽은 직원/);
  assert.match(messageDetail, /답글한 직원/);
  assert.match(messageDetail, /다른 방에 전달/);
  assert.match(attachmentDisplay, /두 손가락으로 확대·축소/);
  assert.match(attachmentDisplay, /<audio controls/);
  assert.match(attachmentDisplay, /<video controls playsInline/);
  assert.match(attachmentDisplay, /음성 받아쓰기/);
  assert.match(attachmentDisplay, /이미지 글자 판독/);
  assert.match(attachmentDisplay, /확인한 내용 저장/);
  assert.match(messageDetail, /showExtraction/);
  assert.match(messageDetail, /canEditExtraction/);
  assert.match(roomSearch, /검색 결과 AI 요약/);
  assert.match(roomSearch, /업무 상태/);
  assert.match(globalStyles, /\.room-search-card/);
  assert.match(globalStyles, /height:\s*min\(820px,\s*calc\(100dvh - 48px\)\)/);
  assert.doesNotMatch(globalStyles, /body\.period-workdesk-printing/);
});
