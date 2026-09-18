import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import React from 'react';
import ts from 'typescript';
import * as draft from '../app/handwritingVoiceDraft.mjs';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../app/components/HandwritingVoiceCorrectionPanel.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source + '\nexport { ApprovalWorkspace };', {
  compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
}).outputText;
const finalText = '합성 대상: 오후 2시 10분 물 200mL 제공\n오후 4시에 다시 확인 예정\n오후 4시 5분 총 200mL 섭취 완료';
const reviewItems = [
  {review_item_id: 'time-a', category: 'time', kind: 'conflict', ocr_values: ['오전 2시 10분', '오후 4시', '오후 4시 5분'], whisper_values: ['오후 2시 10분', '오후 4시', '오후 4시 5분'], ai_recommendation: null},
  {review_item_id: 'follow-a', category: 'follow_up', kind: 'one_sided_critical', ocr_values: [], whisper_values: ['오후 4시에 다시 확인 예정'], ai_recommendation: null},
];

function workspace(overrides = {}) {
  const states = [], refs = [], calls = [], edits = [];
  let si = 0, ri = 0;
  const dependencies = {
    react: {...React, useEffect() {}, useMemo: fn => fn(), useRef: value => refs[ri++] ??= {current: value}, useState(initial) {
      const i = si++;
      if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial;
      return [states[i], value => {states[i] = typeof value === 'function' ? value(states[i]) : value;}];
    }},
    '../api': {apiFetch: async (path, options) => {calls.push({path, options}); return {versions: []};}},
    '../handwritingVoiceDraft.mjs': draft,
  };
  const mod = {exports: {}};
  new Function('require', 'module', 'exports', compiled)(name => dependencies[name] ?? require(name), mod, mod.exports);
  const props = {imageId: 'image-a', messageId: 'message-a', audioId: 'audio-a', mode: 'full_reading', sourceText: '합성 OCR', evidenceText: '합성 음성', proposedText: finalText, warningLines: [], requiresConfirmation: true, reviewItems, canUse: true, onUserEdit: text => edits.push(text), ...overrides};
  const all = node => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(all)];
  const elements = () => {si = 0; ri = 0; return all(mod.exports.ApprovalWorkspace(props));};
  const label = node => React.Children.toArray(node.props?.children).filter(x => typeof x === 'string').join('');
  const controls = () => elements().filter(e => e.type === 'button' && /이 항목 확인/.test(label(e)));
  return {elements, edits, calls, controls,
    textarea: () => elements().find(e => e.type === 'textarea'),
    checkbox: () => elements().find(e => e.type === 'input' && e.props.type === 'checkbox'),
    save: () => elements().find(e => e.type === 'button' && /승인한 최종문 저장/.test(label(e))),
    async saveNow() {this.save().props.onClick(); await new Promise(resolve => setImmediate(resolve));},
  };
}

test('confirming time evidence never rewrites another event or marks text as an employee edit', () => {
  const view = workspace();
  // Legacy button fallback makes RED demonstrate the actual corruption, not a missing control.
  const legacy = view.elements().find(e => e.type === 'button' && React.Children.toArray(e.props.children).includes('오후 2시 10분'));
  const button = view.controls()[0] ?? legacy;
  assert.ok(button);
  button.props.onClick();
  assert.equal(view.textarea().props.value, finalText);
  assert.deepEqual(view.edits, []);
  assert.equal(view.controls().length, 2);
});

test('each item is explicitly confirmed before whole-document confirmation and save; repeated confirmation preserves every time', async () => {
  const view = workspace();
  assert.equal(view.checkbox().props.disabled, true);
  assert.equal(view.save().props.disabled, true);
  assert.equal(view.controls().length, 2);
  view.controls()[0].props.onClick();
  view.controls()[0].props.onClick();
  assert.equal(view.checkbox().props.disabled, true);
  view.controls()[1].props.onClick();
  assert.equal(view.textarea().props.value, finalText);
  assert.equal(view.checkbox().props.disabled, false);
  assert.equal(view.save().props.disabled, true);
  view.checkbox().props.onChange({target: {checked: true}});
  assert.equal(view.save().props.disabled, false);
  await view.saveNow();
  const body = JSON.parse(view.calls[0].options.body);
  assert.equal(body.sentences[0].final_text, finalText);
  assert.deepEqual(body.conflict_resolutions.map(r => [r.review_item_id, r.selected_source, r.selected_value]), [
    ['time-a', 'staff_manual', '직원 최종문 대조 확인 · 시간 (확정 내용은 함께 저장한 최종문 참조)'],
    ['follow-a', 'staff_manual', '직원 최종문 대조 확인 · 후속 확인 (확정 내용은 함께 저장한 최종문 참조)'],
  ]);
  // The running 18131 backend consumes choice/value (not selected_source/value).
  assert.deepEqual(body.conflict_resolutions.map(r => [r.review_item_id, r.choice, r.value]), [
    ['time-a', 'staff_manual', '직원 최종문 대조 확인 · 시간 (확정 내용은 함께 저장한 최종문 참조)'],
    ['follow-a', 'staff_manual', '직원 최종문 대조 확인 · 후속 확인 (확정 내용은 함께 저장한 최종문 참조)'],
  ]);
});

test('editing final text invalidates prior item and whole-document confirmations', () => {
  const view = workspace();
  assert.equal(view.controls().length, 2);
  for (let i = 0; i < 2; i++) view.controls()[i].props.onClick();
  view.checkbox().props.onChange({target: {checked: true}});
  const edited = finalText.replace('오후 2시 10분', '오후 2시 15분');
  view.textarea().props.onChange({target: {value: edited}});
  assert.equal(view.textarea().props.value, edited);
  assert.equal(view.checkbox().props.checked, false);
  assert.equal(view.checkbox().props.disabled, true);
  assert.equal(view.save().props.disabled, true);
  assert.deepEqual(view.edits, [edited]);
});

test('read-only and empty-text workspaces cannot confirm or save', () => {
  for (const props of [{canUse: false}, {proposedText: ''}]) {
    const view = workspace(props);
    assert.equal(view.controls().length, 2);
    assert.ok(view.controls().every(b => b.props.disabled));
    assert.equal(view.checkbox().props.disabled, true);
    assert.equal(view.save().props.disabled, true);
  }
});

test('long final text is stored in full without overflowing the existing 500-character confirmation field', async () => {
  const longText = finalText + '\n직원이 원문과 대조한 합성 기록입니다.'.repeat(40);
  const view = workspace({proposedText: longText});
  for (let i = 0; i < 2; i++) view.controls()[i].props.onClick();
  view.checkbox().props.onChange({target: {checked: true}});
  await view.saveNow();
  const body = JSON.parse(view.calls[0].options.body);
  assert.equal(body.sentences[0].final_text, longText);
  assert.ok(body.conflict_resolutions.every(r => r.selected_value.length <= 500));
});

test('direct typing and partial correction with no review items retain normal save flow', async () => {
  for (const mode of ['direct_typing', 'partial_correction']) {
    const view = workspace({mode, reviewItems: []});
    view.textarea().props.onChange({target: {value: '직원이 직접 확인한 합성 문장'}});
    assert.equal(view.checkbox().props.disabled, false);
    view.checkbox().props.onChange({target: {checked: true}});
    assert.equal(view.save().props.disabled, false);
    await view.saveNow();
    const body = JSON.parse(view.calls[0].options.body);
    assert.equal(body.mode, mode);
    assert.equal(body.sentences[0].final_text, '직원이 직접 확인한 합성 문장');
    assert.deepEqual(body.conflict_resolutions, []);
  }
});
