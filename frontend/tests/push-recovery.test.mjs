import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const ownerMessage = '다른 사용자의 알림 구독은 변경할 수 없습니다. 이 기기의 알림을 해제한 뒤 다시 등록해 주세요.';
const config = {enabled:true, public_key:'AQID'};
class ApiError extends Error { constructor(message, status) {super(message); this.status=status;} }

function fixture({failure=new ApiError(ownerMessage,403), unsubscribe=true, sameEndpoint=false}={}) {
  const calls=[];
  let current;
  const subscription = endpoint => ({endpoint, toJSON:()=>({endpoint, keys:{p256dh:'synthetic',auth:'synthetic'}}),
    async unsubscribe() {calls.push('unsubscribe'); if (unsubscribe) current=null; return unsubscribe;}});
  current=subscription('https://push.invalid/old');
  const registration={update:async()=>{},pushManager:{getSubscription:async()=>current,
    subscribe:async()=>{calls.push('subscribe'); return current=subscription(sameEndpoint?'https://push.invalid/old':'https://push.invalid/new');}}};
  const apiFetch=async(path,options={})=>{
    if(path==='/api/push/config') return config;
    assert.equal(options.method,'POST');
    const endpoint=JSON.parse(options.body).endpoint;
    calls.push(endpoint.endsWith('/old')?'post-old':'post-new');
    if(endpoint.endsWith('/old') && failure) throw failure;
    return {enabled:true,active:true,message:'registered'};
  };
  const source=readFileSync(new URL('../app/pushNotifications.ts',import.meta.url),'utf8');
  const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const mod={exports:{}};
  const context={exports:mod.exports,module:mod,require:()=>({apiFetch,ApiError}),Uint8Array,
    window:{isSecureContext:true,PushManager:{},Notification:{},atob:s=>Buffer.from(s,'base64').toString('binary')},
    Notification:{permission:'granted',requestPermission:async()=>'granted'},
    navigator:{userAgent:'Android',serviceWorker:{register:async()=>registration,ready:Promise.resolve(registration)}}};
  vm.runInNewContext(compiled,context);
  return {api:mod.exports,calls};
}

test('explicit enable repairs only the current browser owner-conflicting subscription',async()=>{
  const f=fixture();
  const result=await f.api.enableWebPush(config);
  assert.equal(result.active,true);
  assert.deepEqual(f.calls,['post-old','unsubscribe','subscribe','post-new']);
});
test('background status reading never unsubscribes an owner-conflicting registration',async()=>{
  const f=fixture();
  await assert.rejects(f.api.readPushStatus(),/다른 사용자의/);
  assert.deepEqual(f.calls,['post-old']);
});
test('unrelated access denial never causes subscription replacement',async()=>{
  const f=fixture({failure:new ApiError('접근이 차단된 계정입니다.',403)});
  await assert.rejects(f.api.enableWebPush(config),/접근이 차단/);
  assert.deepEqual(f.calls,['post-old']);
});
test('failed unsubscribe stops recovery before a new subscription is made',async()=>{
  const f=fixture({unsubscribe:false});
  await assert.rejects(f.api.enableWebPush(config),/해제/);
  assert.deepEqual(f.calls,['post-old','unsubscribe']);
});
test('recovery refuses an unchanged endpoint rather than retrying a foreign registration',async()=>{
  const f=fixture({sameEndpoint:true});
  await assert.rejects(f.api.enableWebPush(config),/새.*주소/);
  assert.deepEqual(f.calls,['post-old','unsubscribe','subscribe']);
});
test('healthy subscription is not replaced',async()=>{
  const f=fixture({failure:null});
  assert.equal((await f.api.enableWebPush(config)).active,true);
  assert.deepEqual(f.calls,['post-old']);
});
