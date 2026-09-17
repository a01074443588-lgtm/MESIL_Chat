import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
// Render the real view code. Network and attachment widgets are outside this
// server-render test; their live behavior is checked in the isolated browser.
function viewModule(name, overrides = {}) {
  const filename = new URL(`../app/components/${name}.tsx`, import.meta.url);
  const compiled = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const compiledModule = { exports: {} };
  const scopedRequire = (specifier) => {
    if (specifier in overrides) return overrides[specifier];
    if (specifier === "./CareBriefingReader") return viewModule("CareBriefingReader");
    if (specifier.startsWith(".")) return new Proxy({}, { get: () => () => null });
    return require(specifier);
  };
  new Function("require", "module", "exports", compiled)(scopedRequire, compiledModule, compiledModule.exports);
  return compiledModule.exports;
}

test("briefing starts with a short title and directly available resident selection", () => {
  const { PeriodWorkDesk } = viewModule("PeriodWorkDesk");
  const html = renderToStaticMarkup(React.createElement(PeriodWorkDesk, { open: true, rooms: [], onClose() {}, onOpenSource() {} }));
  assert.match(html, /<h2[^>]*>돌봄브리핑<\/h2>/);
  assert.match(html, /aria-label="어르신 선택"/);
  assert.doesNotMatch(html, /업무 전체 요약|어르신별 요약|기초사정·급여계획 작성 도우미|작성 사유/);
});

test("record questions use inclusive date selections rather than the review UTC interval", () => {
  const source = readFileSync(new URL("../app/components/PeriodWorkDesk.tsx", import.meta.url), "utf8");
  const readerProps = source.slice(source.indexOf("<CareBriefingReader"), source.indexOf("/> : <p", source.indexOf("<CareBriefingReader")));
  assert.match(readerProps, /startDate=\{startDate\}/);
  assert.match(readerProps, /endDate=\{endDate\}/);
  assert.doesNotMatch(readerProps, /startDate=\{review.period_start\}|endDate=\{review.period_end\}/);
});

test("record question submit waits for readiness and suppresses duplicate submission", async () => {
  // Exercise the real component's event handler + flow. Hooks only provide a
  // deterministic render host; transport is synthetic and no browser/network runs.
  const flowSource = readFileSync(new URL('../app/recordQuestionFlow.ts', import.meta.url), 'utf8');
  const flowModule = { exports: {} };
  new Function('module', 'exports', ts.transpileModule(flowSource, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText)(flowModule, flowModule.exports);
  const states = [], refs = []; let stateIndex = 0, refIndex = 0;
  const calls = []; let ready; let questions = 0;
  const waiting = new Promise(resolve => { ready = resolve; });
  const { CareBriefingReader } = viewModule('CareBriefingReader', {
    react: { ...React, useEffect() {},
      useState(initial) { const i = stateIndex++; if (!(i in states)) states[i] = initial;
        return [states[i], value => { states[i] = value; }]; },
      useRef(initial) { const i = refIndex++; return refs[i] ??= { current: initial }; },
    },
    '../recordQuestionFlow': flowModule.exports,
    '../api': { ApiError: class extends Error {}, apiFetch: async (path, options) => {
      calls.push({ path, body: options.body });
      return path.endsWith('model-ready') ? waiting : ++questions === 1
        ? { error_type: 'model_preparing' } : { answer: '합성 답변', evidence_ids: [] };
    } },
  });
  const props = { topics: [], sources: [], startDate: '2026-09-01', endDate: '2026-09-09',
    residentId: '', residentName: '', roomId: '', truncated: false,
    onSelectResident() {}, onOpenSource() {}, onCompare() {} };
  const render = () => { stateIndex = 0; refIndex = 0; return CareBriefingReader(props); };
  const elements = node => !node || typeof node !== 'object' ? [] :
    [node, ...React.Children.toArray(node.props?.children).flatMap(elements)];
  elements(render()).find(e => e.type === 'textarea').props.onChange({ target: { value: '합성 질문' } });
  const priorWindow = globalThis.window;
  globalThis.window = { setInterval, clearInterval };
  try {
    const form = elements(render()).find(e => e.type === 'form');
    form.props.onSubmit({ preventDefault() {} });
    form.props.onSubmit({ preventDefault() {} });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.length, 2);
    assert.equal(JSON.parse(calls[0].body).question, '합성 질문');
    assert.deepEqual(calls[1], { path: '/api/workdesk/record-question/model-ready', body: undefined });
    ready({ status: 'ready' });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls.length, 3);
    assert.equal(calls[2].body, calls[0].body);
    const html = renderToStaticMarkup(render());
    assert.match(html, /합성 답변/);
  } finally { globalThis.window = priorWindow; }
});

test("selected resident is sent as a default context and remains visibly scoped", async () => {
  const flowSource = readFileSync(new URL('../app/recordQuestionFlow.ts', import.meta.url), 'utf8');
  const flowModule = { exports: {} };
  new Function('module', 'exports', ts.transpileModule(flowSource, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText)(flowModule, flowModule.exports);
  const states = [], refs = []; let stateIndex = 0, refIndex = 0;
  const calls = [];
  const { CareBriefingReader } = viewModule('CareBriefingReader', {
    react: { ...React, useEffect() {},
      useState(initial) { const i = stateIndex++; if (!(i in states)) states[i] = initial;
        return [states[i], value => { states[i] = value; }]; },
      useRef(initial) { const i = refIndex++; return refs[i] ??= { current: initial }; },
    },
    '../recordQuestionFlow': flowModule.exports,
    '../api': { ApiError: class extends Error {}, apiFetch: async (path, options = {}) => {
      if (path.endsWith('model-ready')) return { status: 'ready' };
      calls.push({ path, body: options.body });
      return { answer: '합성 답변', evidence_ids: [], performance: {} };
    } },
  });
  const props = { topics: [], sources: [], startDate: '2026-09-01', endDate: '2026-09-09',
    residentId: 'resident-0001', residentName: '어르0001', roomId: '', truncated: false,
    onSelectResident() {}, onOpenSource() {}, onCompare() {} };
  const render = () => { stateIndex = 0; refIndex = 0; return CareBriefingReader(props); };
  const elements = node => !node || typeof node !== 'object' ? [] :
    [node, ...React.Children.toArray(node.props?.children).flatMap(elements)];
  elements(render()).find(e => e.type === 'textarea').props.onChange({ target: { value: '오늘 어디가 아파서 쉬신다고 하시던가요?' } });
  const priorWindow = globalThis.window;
  globalThis.window = { setInterval, clearInterval };
  try {
    elements(render()).find(e => e.type === 'form').props.onSubmit({ preventDefault() {} });
    await new Promise(resolve => setImmediate(resolve));
    const payload = JSON.parse(calls[0].body);
    assert.equal(payload.default_resident_id, 'resident-0001');
    assert.equal(payload.resident_id, null);
    assert.match(renderToStaticMarkup(render()), /기본 대상: 어르0001/);
    assert.match(renderToStaticMarkup(render()), /2026-09-01 ~ 2026-09-09/);
  } finally { globalThis.window = priorWindow; }
});

test("record question Enter submits once while preserving Korean composition and Shift Enter", () => {
  const source = readFileSync(new URL("../app/components/CareBriefingReader.tsx", import.meta.url), "utf8");
  const textarea = source.match(/<textarea id="care-record-question"[\s\S]*?aria-describedby="care-record-question-keyboard-hint" \/>/)?.[0];
  assert.ok(textarea, "record question textarea should keep its keyboard contract");
  assert.match(textarea, /event\.isComposing === true\) \|\| nativeEvent\.isComposing \|\| nativeEvent\.keyCode === 229/);
  assert.match(textarea, /event\.key !== "Enter" \|\| event\.shiftKey \|\| isComposing/);
  assert.match(textarea, /if \(!question\.trim\(\) \|\| questionLoading \|\| questionFlightRef\.current\) return/);
  assert.match(textarea, /event\.currentTarget\.form\?\.requestSubmit\(\)/);
  assert.match(source, /Enter로 질문 · Shift\+Enter로 줄바꿈/);
});

test("record answer keeps sentence evidence but does not render the candidate timeline", () => {
  const source = readFileSync(new URL("../app/components/CareBriefingReader.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /경과 기록 펼쳐보기|answer\.timeline\?\.length/);
  assert.match(source, /openEvidence\(sentence\.evidence_ids, answer\.period_start, answer\.period_end, answer\.resident_id\)/);
  assert.match(source, /근거 기록 \{answer\.evidence_ids\.length\}건 보기/);
  assert.match(source, /openEvidence\(answer\.evidence_ids, answer\.period_start, answer\.period_end, answer\.resident_id\)/);
});

const topic = (overrides = {}) => ({
  key: "r1:mobility:event1", resident_id: "r1", resident_name: "어르0003", topic: "이동",
  summary: "08:20 보행 도움을 받음. 10:40 통증 없고 보행 안정 확인.",
  kinds: ["event", "followup"], evidence_ids: ["m1"],
  first_at: "2026-08-10T08:20:00+09:00", latest_at: "2026-08-10T10:40:00+09:00",
  entries: [
    { message_id: "m1", comment_id: null, occurred_at: "2026-08-10T08:20:00+09:00", summary: "08:20 보행 도움을 받음.", kind: "event", event_key: "m1" },
    { message_id: "m1", comment_id: "c1", occurred_at: "2026-08-10T10:40:00+09:00", summary: "10:40 통증 없고 보행 안정 확인.", kind: "followup", event_key: "m1" },
  ], ...overrides,
});

test("compact output preserves follow-up and evidence while omitting empty assessment and editing controls", () => {
  const { CareTopicList } = viewModule("CareBriefingReader");
  const html = renderToStaticMarkup(React.createElement(CareTopicList, { topics: [topic()], residentId: "", onSelectResident() {}, onEvidence() {} }));
  assert.match(html, /08:20 보행 도움을 받음. 10:40 통증 없고 보행 안정 확인./);
  assert.match(html, /관련 기록 1건/);
  assert.doesNotMatch(html, /textarea|checkbox|행정정보 미확인|현재 관찰 필요|점수|작성 사유/);
});

test("resident timeline groups a topic without merging separate incident evidence or losing conflict text", () => {
  const { CareTopicList } = viewModule("CareBriefingReader");
  const conflict = topic({ key: "r1:mobility:event2", evidence_ids: ["m2"], summary: "14:00 120/80, 같은 측정 160/90으로 기록되어 충돌.", entries: [{ message_id: "m2", comment_id: null, occurred_at: "2026-08-15T14:00:00+09:00", summary: "14:00 120/80, 같은 측정 160/90으로 기록되어 충돌.", kind: "conflict", event_key: "m2" }] });
  const html = renderToStaticMarkup(React.createElement(CareTopicList, { topics: [topic(), conflict], residentId: "r1", onSelectResident() {}, onEvidence() {} }));
  assert.equal((html.match(/<h4>이동<\/h4>/g) ?? []).length, 1);
  assert.equal((html.match(/관련 기록 1건/g) ?? []).length, 2);
  assert.match(html, /후속 기록/);
  assert.match(html, /통증 없고 보행 안정 확인/);
  assert.match(html, /120\/80, 같은 측정 160\/90/);
  assert.match(html, /자료 충돌/);
});

test("a bounded first list discloses remaining content and an empty result stays empty", () => {
  const { CareTopicList } = viewModule("CareBriefingReader");
  const props = { residentId: "", onSelectResident() {}, onEvidence() {} };
  const html = renderToStaticMarkup(React.createElement(CareTopicList, { ...props, topics: Array.from({ length: 13 }, (_, index) => topic({ key: `event-${index}` })) }));
  assert.equal((html.match(/class="care-topic-row"/g) ?? []).length, 12);
  assert.match(html, /남은 1개 내용 더 보기/);
  const restored = renderToStaticMarkup(React.createElement(CareTopicList, { ...props, initialVisibleCount: 24, topics: Array.from({ length: 13 }, (_, index) => topic({ key: `event-${index}` })) }));
  assert.equal((restored.match(/class="care-topic-row"/g) ?? []).length, 13);
  assert.doesNotMatch(restored, /내용 더 보기/);
  const empty = renderToStaticMarkup(React.createElement(CareTopicList, { ...props, topics: [] }));
  assert.match(empty, /확인할 돌봄 기록이 없습니다/);
  assert.doesNotMatch(empty, /care-topic-row|변화 없음|행정정보 미확인/);
});

test("evidence renders verbatim original and reply with author, time and true message link", () => {
  const { CareEvidenceSources } = viewModule("CareBriefingReader");
  const sources = [{ message: { id: "m1", room_id: "room1", sender_name: "직원0001", created_at: "2026-08-10T08:20:00+09:00", body: "08:20 보행도움 1회. 낙상은 없었음.\n물 120mL.", attachments: [] }, room_name: "돌봄 합성방", comments: [{ id: "c1", author_name: "직원0002", created_at: "2026-08-10T10:40:00+09:00", body: "10:40 통증 없고 안정 확인." }] }];
  const html = renderToStaticMarkup(React.createElement(CareEvidenceSources, { sources, onOpenSource() {} }));
  assert.match(html, /08:20 보행도움 1회. 낙상은 없었음.\n물 120mL./);
  assert.match(html, /10:40 통증 없고 안정 확인./);
  assert.match(html, /직원0001/);
  assert.match(html, /직원0002/);
  assert.match(html, /돌봄 합성방/);
  assert.match(html, /원래 대화로 이동/);
  const outside = renderToStaticMarkup(React.createElement(CareEvidenceSources, { sources, startDate: "2026-08-11", endDate: "2026-08-12", onOpenSource() {} }));
  assert.match(outside, /선택 기간 밖의 원문/);
  assert.match(outside, /선택 기간 밖의 답글/);
  assert.match(outside, /10:40 통증 없고 안정 확인./);
});

test("optional document comparison shows only relevant valued fields and no unsaved confirmation controls", () => {
  const { ChangeComparisonPanel } = viewModule("ResidentCarePlanningPanel");
  const item = (field_key, label, previous_value, current_value) => ({ field_key, label, previous_value, current_value, classification: "changed", baseline_evidence_refs: ["doc1"], current_evidence_refs: ["m1"], conflict_values: [], reason: "저장된 문서와 관찰 근거" });
  const draftRecord = { id: "d1", current_revision: 2, revisions: [{ revision: 2, bundle: { selected_change_field_keys: [], evidence_sources: [], comparison_items: [item("mobility", "이동 상태", "독립 보행", "보행 도움"), item("nutrition", "식사 상태", "일반식", "죽"), item("fall_score", "낙상 점수", null, null)] } }] };
  const html = renderToStaticMarkup(React.createElement(ChangeComparisonPanel, { draftRecord, residentId: "r1", relevantTopics: ["이동"], onSaved() {} }));
  assert.match(html, /독립 보행/);
  assert.match(html, /보행 도움/);
  assert.match(html, /근거 2건/);
  assert.doesNotMatch(html, /식사 상태|낙상 점수|checkbox|선택 내용 저장/);
});
