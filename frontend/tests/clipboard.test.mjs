import assert from 'node:assert/strict';
import test from 'node:test';

const policy = await import('../app/composerAttachments.ts').catch(error => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {};
  throw error;
});
const file = (name, data='synthetic', type='text/plain', lastModified=1) => new File([data], name, {type,lastModified});
const clip = (files=[], text='', types=[]) => ({files, items:files.map(f=>({kind:'file',getAsFile:()=>f})), types, getData:t=>t==='text/plain'?text:''});

test('clipboard leaves text/HTML paste to the plain textarea with no attachments', () => {
  assert.equal(typeof policy.readComposerClipboard, 'function');
  assert.deepEqual(policy.readComposerClipboard(clip([], '한글\nEnglish 123', ['text/plain','text/html'])), {kind:'text'});
});
test('clipboard files take priority over HTML and file path text without duplicating items/files', () => {
  const png=file('image.png','png','image/png');
  const result=policy.readComposerClipboard(clip([png], 'C:\\private\\image.png', ['Files','text/html']));
  assert.equal(result.kind,'files'); assert.deepEqual(result.files,[png]);
});
test('clipboard item-only files and unavailable file data are distinguished from normal text', () => {
  const png=file('image.png','png','image/png');
  assert.deepEqual(policy.readComposerClipboard({...clip(),items:[{kind:'file',getAsFile:()=>png}]}),{kind:'files',files:[png]});
  assert.equal(policy.readComposerClipboard(clip([], '', ['Files'])).kind,'unavailable');
  assert.equal(policy.readComposerClipboard(clip([], 'C:\\ordinary text', ['text/plain'])).kind,'text');
});
test('selection reuses allowed formats, per-file/total/count limits and rejects invalid files atomically', async () => {
  assert.equal(typeof policy.prepareComposerFiles, 'function');
  const existing=file('existing.txt');
  for (const bad of [file('bad.exe'),file('empty.txt',''),file('bad.exe.png'),file('big.txt','x'.repeat(30*1024*1024+1))]) {
    await assert.rejects(policy.prepareComposerFiles([existing],[bad]), Error);
  }
  await assert.rejects(policy.prepareComposerFiles([],Array.from({length:11},(_,i)=>file(`f${i}.txt`,`${i}`))),/10/);
  await assert.rejects(policy.prepareComposerFiles([],Array.from({length:4},(_,i)=>file(`f${i}.txt`,String(i).repeat(26*1024*1024)))),/100MB/);
  assert.equal(existing.name,'existing.txt');
});
test('file selection then repeated clipboard content adds one copy despite changed name and timestamp', async () => {
  const original=file('report.png','same image','image/png');
  const repeated=file('image.png','same image','image/png',99);
  const other=file('image.png','another image','image/png',99);
  const selected=await policy.prepareComposerFiles([original],[repeated,other],{clipboard:true});
  assert.equal(selected.length,2); assert.equal(selected[0],original);
  assert.match(selected[1].name,/^clipboard-[a-f0-9]+\.png$/);
  assert.equal(await selected[1].text(),'another image');
});
test('multiple captured images receive distinct safe names; same metadata but different bytes is not dropped', async () => {
  const selected=await policy.prepareComposerFiles([], [file('image.png','one','image/png'),file('image.png','two','image/png')],{clipboard:true});
  assert.equal(selected.length,2); assert.notEqual(selected[0].name,selected[1].name);
});
test('unnamed browser screenshots receive a safe MIME-derived extension instead of being rejected', async () => {
  const selected=await policy.prepareComposerFiles([], [file('', 'capture', 'image/png')],{clipboard:true});
  assert.equal(selected.length,1); assert.match(selected[0].name,/^clipboard-[a-f0-9]+\.png$/);
});
test('distinct files with identical metadata keep distinct stable preview keys', () => {
  assert.equal(typeof policy.attachmentSelectionKey, 'function');
  const first=file('report.png','abc','image/png');
  const second=file('report.png','def','image/png');
  assert.notEqual(policy.attachmentSelectionKey(first),policy.attachmentSelectionKey(second));
  assert.equal(policy.attachmentSelectionKey(first),policy.attachmentSelectionKey(first));
});
