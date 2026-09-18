import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import React from 'react';
import ts from 'typescript';

const require=createRequire(import.meta.url);
function panel({registered=false, sendFails=false}={}) {
  const state=[],effects=[]; let index=0,mounted=false;
  const source=readFileSync(new URL('../app/components/NotificationSoundPanel.tsx',import.meta.url),'utf8');
  const compiled=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const deps={
    react:{...React,useState(initial){const n=index++; if(!(n in state))state[n]=initial; return [state[n],v=>{state[n]=v;}];},useEffect(fn){if(!mounted)effects.push(fn);}},
    '../nativeShare':{isNativeMesilApp:()=>false},'../nativePush':{},
    '../notificationSound':{},
    '../pushNotifications':{readPushEnvironment:()=>({}),readPushStatus:async()=>{
      if(!registered)throw new Error('registration failed');
      return {state:'ready',config:{enabled:true,public_key:'synthetic'}};
    },safeWebPushErrorMessage:()=> '알림 등록을 확인하지 못했습니다. 다시 연결해 주세요.',
    enableWebPush:async()=>({active:true}),sendWebPushTest:async()=>{if(sendFails)throw new Error('transport failed');return {message:'sent'};}},
  };
  const mod={exports:{}};
  new Function('require','module','exports',compiled)(n=>deps[n]??require(n),mod,mod.exports);
  const all=n=>!n||typeof n!=='object'?[]:[n,...React.Children.toArray(n.props?.children).flatMap(all)];
  const label=n=>typeof n==='string'?n:React.Children.toArray(n?.props?.children).map(label).join('');
  const render=()=>{index=0;const result=mod.exports.NotificationSoundPanel({mode:'all',onModeChanged(){},onClose(){}});mounted=true;return result;};
  return {render,all,label,effects,async mount(){render();effects.forEach(f=>f());await new Promise(r=>setImmediate(r));}};
}
test('registration failure shows a retry control instead of claiming the server disabled notifications',async()=>{
  const p=panel();await p.mount();
  const view=p.render();
  assert.doesNotMatch(p.label(view),/이 발표환경에서는 잠금화면 알림을 제공하지 않습니다/);
  const retry=p.all(view).find(n=>n.type==='button'&&/다시 연결/.test(p.label(n)));
  assert.ok(retry,'registration failure must allow a deliberate retry');
  assert.equal(retry.props.disabled,false);
});
test('delivery failure after successful registration keeps registration active',async()=>{
  const p=panel({registered:true,sendFails:true});await p.mount();
  const enable=p.all(p.render()).find(n=>n.type==='button'&&/알림 켜고/.test(p.label(n)));
  enable.props.onClick();await new Promise(r=>setImmediate(r));
  const view=p.render();
  assert.ok(p.all(view).some(n=>n.type==='button'&&p.label(n)==='시험 알림 보내기'));
  assert.ok(p.all(view).some(n=>n.type==='button'&&p.label(n)==='이 기기 알림 끄기'));
});
