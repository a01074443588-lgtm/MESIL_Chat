import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const loaded = {exports: {}};
const code = ts.transpileModule(readFileSync(new URL('../app/roomComposer.ts', import.meta.url), 'utf8'),
  {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
new Function('require', 'module', 'exports', code)(require, loaded, loaded.exports);
const {RoomComposerStore} = loaded.exports;
const image = () => new File(['synthetic-only'], 'synthetic.png', {type: 'image/png'});
const wait = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject};};

test('back, room switching, and reopening preserve isolated text, image objects, and options', () => {
  const store = new RoomComposerStore(), file = image();
  store.patch('A', {body:'draft A', reportImage:true, noticeMode:true, residentIds:['synthetic'], replyTarget:{id:'reply-A'}});
  store.setFiles('A', [file]);
  const requestId = store.get('A').requestId;
  store.get(null); store.patch('B', {body:'draft B'}); store.get('A');
  assert.equal(store.get('A').body, 'draft A'); assert.equal(store.get('A').files[0], file);
  assert.equal(store.get('A').requestId, requestId); assert.equal(store.get('A').reportImage, true);
  assert.equal(store.get('B').body, 'draft B'); assert.equal(store.get('B').files.length, 0);
  assert.equal(store.hasPendingWork(), true);
});
test('pending preparation finishes in the original room after back; serial reads retain every file', async () => {
  const store=new RoomComposerStore(), first=image(), second=image(), pending=wait();
  const a=store.prepareFiles('A',[first],()=>pending.promise);
  await Promise.resolve(); store.get(null); store.patch('B',{body:'B'});
  const b=store.prepareFiles('A',[second],async(current,added)=>[...current,...added]);
  assert.equal(store.get('A').preparing,true);
  pending.resolve([first]); await a; await b;
  assert.deepEqual(store.get('A').files,[first,second]); assert.equal(store.get('A').preparing,false);
  assert.equal(store.get('B').body,'B'); assert.equal(store.get('B').files.length,0);
});
test('explicit file cancellation prevents a late read from resurrecting images', async () => {
  const store=new RoomComposerStore(), pending=wait();
  const preparation=store.prepareFiles('A',[image()],()=>pending.promise);
  await Promise.resolve(); store.setFiles('A',[]); pending.resolve([image()]); await preparation;
  assert.equal(store.get('A').files.length,0); assert.equal(store.get('A').preparing,false);
});
test('progress and success affect only source room and retain hidden-upload update blocker', () => {
  const store=new RoomComposerStore(); store.patch('A',{body:'A'}); store.setFiles('A',[image()]);
  const job=store.beginSend('A'); assert.ok(job); store.patch('B',{body:'B'}); const b=store.get('B');
  store.get(null); store.progress(job,64);
  assert.equal(store.get('A').progress,64); assert.equal(store.get('B'),b);
  assert.equal(store.activeUploads(),1); assert.equal(store.hasPendingWork(),true);
  assert.equal(store.beginSend('A'),null);
  assert.equal(store.succeed(job,'sent'),true); assert.equal(store.get('A').sending,false);
  assert.equal(store.get('A').files.length,0); assert.equal(store.get('B'),b);
  assert.notEqual(store.get('A').requestId,job.draft.requestId); assert.equal(store.activeUploads(),0);
});
test('failure restores original whitespace, images, reply, options and retry identity in source room only', () => {
  const store=new RoomComposerStore(), file=image();
  store.patch('A',{body:'  original A  ',residentIds:['r'],replyTarget:{id:'reply'},noticeMode:true,reportImage:true});
  store.setFiles('A',[file]); const job=store.beginSend('A'); store.patch('B',{body:'B'}); const b=store.get('B');
  store.fail(job,'SYNTHETIC_FAILURE');
  assert.equal(store.get('A').body,'  original A  '); assert.equal(store.get('A').files[0],file);
  assert.equal(store.get('A').error,'SYNTHETIC_FAILURE'); assert.equal(store.get('A').noticeMode,true);
  assert.equal(store.get('A').replyTarget.id,'reply'); assert.equal(store.get('A').requestId,job.draft.requestId);
  assert.equal(store.get('B'),b); assert.equal(store.get(null).files.length,0);
  const retry=store.beginSend('A'); assert.equal(retry.draft.requestId,job.draft.requestId);
});
test('separate rooms may send independently without cross-room progress/completion', () => {
  const store=new RoomComposerStore();
  for(const room of ['A','B']) {store.patch(room,{body:room});store.setFiles(room,[image()]);}
  const a=store.beginSend('A'),b=store.beginSend('B'); store.progress(a,10); store.progress(b,70);
  store.succeed(a,'sent A'); assert.equal(store.get('B').sending,true); assert.equal(store.get('B').progress,70);
  store.fail(b,'FAILED B'); assert.equal(store.get('A').feedback,'sent A'); assert.equal(store.get('A').error,'');
});
test('logout drops every draft and ignores stale preparation/progress/completion callbacks', async () => {
  const store=new RoomComposerStore(); store.patch('A',{body:'A'}); const job=store.beginSend('A');
  const pending=wait(),preparation=store.prepareFiles('B',[image()],()=>pending.promise);
  await Promise.resolve(); store.clear(); store.patch('A',{body:'new account draft'});
  pending.resolve([image()]); await preparation;
  store.progress(job,100); assert.equal(store.succeed(job,'old sent'),false); assert.equal(store.fail(job,'old error'),false);
  assert.equal(store.get('A').body,'new account draft'); assert.equal(store.get('B').files.length,0);
  store.clear(); assert.equal(store.hasPendingWork(),false);
});
test('loss of room access drops its draft and prevents stale callbacks restoring it', () => {
  const store=new RoomComposerStore(); store.patch('A',{body:'private draft'}); const job=store.beginSend('A');
  store.patch('B',{body:'keep'}); store.retainRooms(new Set(['B']));
  assert.equal(store.fail(job,'old error'),false); assert.equal(store.get('A').body,''); assert.equal(store.get('B').body,'keep');
});
test('native shared text outside a room is adopted once without overwriting another room draft', () => {
  const store=new RoomComposerStore(); store.setField(null,'body','shared synthetic text');
  store.patch('B',{body:'keep B'}); store.adoptSharedText('B');
  assert.equal(store.get('B').body,'keep B'); assert.equal(store.hasPendingWork(),true);
  store.adoptSharedText('A'); assert.equal(store.get('A').body,'shared synthetic text');
  assert.equal(store.get(null).body,''); store.adoptSharedText('C'); assert.equal(store.get('C').body,'');
  store.clear(); assert.equal(store.hasPendingWork(),false);
});

test('ChatApp back/open keep drafts; send completion guards source room, reset clears all, hidden drafts block updates', () => {
  const text=readFileSync(new URL('../app/components/ChatApp.tsx',import.meta.url),'utf8');
  const ast=ts.createSourceFile('x.tsx',text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  const functions={}; const visit=node=>{
    if(ts.isFunctionDeclaration(node)&&node.name) functions[node.name.text]=node.getText(ast);
    if(ts.isVariableDeclaration(node)&&node.name?.text==='openRoom')functions.openRoom=node.getText(ast);
    if(ts.isVariableDeclaration(node)&&node.name?.text==='synchronizeNavigation')functions.navigation=node.getText(ast);
    ts.forEachChild(node,visit);
  }; visit(ast);
  assert.doesNotMatch(functions.openRoom,/setFiles\(\[\]\)/);
  assert.doesNotMatch(functions.closeActiveRoom,/setFiles\(\[\]\)/);
  assert.doesNotMatch(functions.navigation,/setFiles\(\[\]\)/);
  assert.match(functions.sendMessage,/if \(activeRoomRef.current === roomId\)/);
  assert.match(functions.sendMessage,/composerStore.fail\(job/);
  assert.match(text,/composerStore.clear\(\)/);
  assert.match(text,/data-app-update-dirty=\{composerStore.hasPendingWork\(\)/);
  assert.doesNotMatch(readFileSync(new URL('../app/roomComposer.ts',import.meta.url),'utf8'),/localStorage|sessionStorage|indexedDB/);
});

// Execute the real ChatApp send handler with in-memory substitutes, not a
// rewritten model of its control flow. No API, real account or network is used.
function senderHarness() {
  const text=readFileSync(new URL('../app/components/ChatApp.tsx',import.meta.url),'utf8');
  const ast=ts.createSourceFile('x.tsx',text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  let handler;
  const visit=node=>{if(ts.isFunctionDeclaration(node)&&node.name?.text==='sendMessage')handler=node.getText(ast);ts.forEachChild(node,visit);};
  visit(ast); assert.ok(handler);
  const store=new RoomComposerStore(), pending=wait(), active={current:'A'}, reads=[], requests=[];
  let displayed=[];
  const fetch=async(path,options)=>{requests.push({path,options});return pending.promise;};
  const scope={composerStore:store,activeRoomId:'A',activeRoomRef:active,apiFetch:fetch,apiUpload:fetch,
    markRead:async(...args)=>{reads.push(args);throw Error('SYNTHETIC_READ_FAILURE');},
    setMessages:update=>{displayed=update(displayed);},keepRoomAtLatestRef:{current:false},
    setError:()=>{},attachmentSelectionError:()=>null,FormData};
  const js=ts.transpileModule(handler+'\nreturn sendMessage;',{
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const send=new Function(...Object.keys(scope),js)(...Object.values(scope));
  return {store,pending,active,reads,requests,send:()=>send({preventDefault(){}}),displayed:()=>displayed};
}

test('real send handler does not place room A completion into room B or mark hidden room read', async()=>{
  const h=senderHarness();h.store.patch('A',{body:'synthetic A'});
  const sent=h.send();h.active.current='B';h.store.patch('B',{body:'keep B'});
  h.pending.resolve({id:'synthetic-message-A'});await sent;
  assert.deepEqual(h.displayed(),[]);assert.deepEqual(h.reads,[]);
  assert.equal(h.store.get('B').body,'keep B');assert.equal(h.store.get('A').body,'');
  assert.equal(h.store.get('A').sending,false);assert.match(h.requests[0].path,/\/A\/messages$/);
});

test('real send handler never restores a successfully sent draft when only read acknowledgement fails', async()=>{
  const h=senderHarness();h.store.patch('A',{body:'synthetic A'});
  const requestId=h.store.get('A').requestId,sent=h.send();
  h.pending.resolve({id:'synthetic-message-A'});await sent;await Promise.resolve();
  assert.equal(h.displayed().length,1);assert.equal(h.reads.length,1);
  assert.equal(h.store.get('A').body,'');assert.equal(h.store.get('A').error,'');
  assert.notEqual(h.store.get('A').requestId,requestId);
});

test('real send failure keeps original draft and request identity for deduplicated retry', async()=>{
  const h=senderHarness();h.store.patch('A',{body:'  synthetic A  ',noticeMode:true});
  const requestId=h.store.get('A').requestId,sent=h.send();h.active.current='B';
  h.store.patch('B',{body:'keep B'});h.pending.reject(Error('SYNTHETIC_SEND_FAILURE'));await sent;
  assert.equal(h.store.get('A').body,'  synthetic A  ');assert.equal(h.store.get('A').requestId,requestId);
  assert.equal(h.store.get('A').noticeMode,true);assert.equal(h.store.get('B').body,'keep B');
  assert.deepEqual(h.displayed(),[]);
});

test('real send completion after logout cannot change a new account draft or display', async()=>{
  const h=senderHarness();h.store.patch('A',{body:'synthetic old account'});
  const sent=h.send();h.store.clear();h.store.patch('A',{body:'synthetic new account'});
  h.pending.resolve({id:'synthetic-message-old'});await sent;
  assert.equal(h.store.get('A').body,'synthetic new account');assert.deepEqual(h.displayed(),[]);
  assert.deepEqual(h.reads,[]);
});

test('real send snapshots notice and request identity before navigation', async()=>{
  const h=senderHarness();h.store.patch('A',{body:'synthetic notice',noticeMode:true});
  const requestId=h.store.get('A').requestId,sent=h.send();h.active.current='B';
  const payload=JSON.parse(h.requests[0].options.body);
  assert.equal(payload.message_type,'notice');assert.equal(payload.client_request_id,requestId);
  h.pending.resolve({id:'synthetic-message-A'});await sent;
});

test('real attachment send keeps files, reply, notice and target room in the captured request', async()=>{
  const h=senderHarness(),file=image();
  h.store.patch('A',{body:'synthetic attachment',noticeMode:true,replyTarget:{id:'synthetic-reply'}});
  h.store.setFiles('A',[file]);const sent=h.send();h.active.current='B';
  const request=h.requests[0];assert.match(request.path,/\/A\/messages-with-files$/);
  assert.equal(request.options.get('message_type'),'notice');
  assert.equal(request.options.get('reply_to_message_id'),'synthetic-reply');
  assert.equal(await request.options.get('files').text(),'synthetic-only');
  h.pending.resolve({id:'synthetic-message-A'});await sent;
  assert.equal(h.store.get('A').files.length,0);assert.deepEqual(h.displayed(),[]);
});
