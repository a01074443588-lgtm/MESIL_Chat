import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import React from 'react';
import ts from 'typescript';
const require=createRequire(import.meta.url);
const compile=source=>ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;

function harness(transport){
  const flow={exports:{}};
  new Function('module','exports',compile(readFileSync(new URL('../app/recordQuestionFlow.ts',import.meta.url),'utf8')))(flow,flow.exports);
  const states=[],refs=[];let si=0,ri=0;
  const viewModule={exports:{}};
  const dependencies={
    react:{...React,useEffect(){},useState(initial){const i=si++;if(!(i in states))states[i]=initial;return[states[i],v=>{states[i]=v;}];},useRef(initial){return refs[ri++]??={current:initial};}},
    '../api':{apiFetch:transport,ApiError:class extends Error{}},
    '../recordQuestionFlow':flow.exports,
    './AttachmentDisplay':{AttachmentDisplay:()=>null},
  };
  new Function('require','module','exports',compile(readFileSync(new URL('../app/components/CareBriefingReader.tsx',import.meta.url),'utf8')))(name=>dependencies[name]??require(name),viewModule,viewModule.exports);
  const props={topics:[],sources:[],sourceIds:[],startDate:'2026-09-06',endDate:'2026-09-12',rangeMode:'default',residentId:'synthetic-resident',residentName:'합성대상',roomId:'synthetic-room',messageType:'report',truncated:false,onSelectResident(){},onOpenSource(){},onCompare(){}};
  const render=()=>{si=0;ri=0;return viewModule.exports.CareBriefingReader(props);};
  const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
  return{render,elements};
}

test('visible briefing action submits fixed current scope without replacing the user question draft',async()=>{
  const calls=[];
  const view=harness(async(path,request)=>{
    calls.push({path,body:request.body});
    return {question:'선택한 기간의 돌봄 기록을 요약해 주세요.',answer:'합성 검증 요약',period_start:'2026-09-06',period_end:'2026-09-12',resident_id:'synthetic-resident',evidence_ids:[],processing_method:'local_ai',generation_verified:true};
  });
  view.elements(view.render()).find(e=>e.type==='textarea').props.onChange({target:{value:'작성 중인 다른 질문'}});
  const action=view.elements(view.render()).find(e=>e.type==='button'&&e.props.children==='AI 브리핑 만들기');
  assert.ok(action,'currently visible CareBriefingReader must expose the LLM briefing action');
  const prior=globalThis.window;globalThis.window={setInterval,clearInterval};
  try{
    action.props.onClick();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls[0].path,'/api/workdesk/record-question');
    assert.deepEqual(JSON.parse(calls[0].body),{start_date:'2026-09-06',end_date:'2026-09-12',range_mode:'fixed',resident_id:null,default_resident_id:'synthetic-resident',room_id:'synthetic-room',message_type:'report',question:'선택한 기간의 돌봄 기록을 요약해 주세요.'});
    assert.equal(view.elements(view.render()).find(e=>e.type==='textarea').props.value,'작성 중인 다른 질문');
    assert.ok(view.elements(view.render()).some(e=>e.type==='p'&&e.props.children==='로컬 AI'));
  }finally{globalThis.window=prior;}
});

for (const scenario of [
  {name:'all residents despite a selected default', resident_id:null, resolved_resident_name:null, label:'전체 어르신'},
  {name:'another explicitly resolved resident', resident_id:'other-resident', resolved_resident_name:'합성대상02', label:'합성대상02'},
  {name:'same resident with no returned name', resident_id:'synthetic-resident', resolved_resident_name:null, label:'합성대상'},
  {name:'different resident without a name must not borrow the selected name', resident_id:'other-resident', resolved_resident_name:null, label:'대상 어르신'},
  {name:'ambiguous resident is not mislabeled as the selected resident', resident_id:null, resolved_resident_name:null, processing_method:'clarification', label:'대상 확인 필요'},
]) {
  test(`answer heading and copied text use response scope: ${scenario.name}`, async () => {
    const calls = [], copied = [];
    const view = harness(async (path, request) => {
      calls.push({path, body:JSON.parse(request.body)});
      return {question:'전체 어르신의 상태를 알려주세요.', answer:'합성 답변 본문', period_start:'2026-09-01', period_end:'2026-09-02',
        resident_id:scenario.resident_id, resolved_resident_name:scenario.resolved_resident_name,
        evidence_ids:[], processing_method:scenario.processing_method ?? 'rules'};
    });
    const previousWindow = globalThis.window;
    const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
    globalThis.window = {setInterval, clearInterval};
    Object.defineProperty(globalThis, 'navigator', {configurable:true, value:{clipboard:{writeText:async text => copied.push(text)}}});
    try {
      view.elements(view.render()).find(e => e.type === 'textarea').props.onChange({target:{value:'전체 어르신의 상태를 알려주세요.'}});
      view.elements(view.render()).find(e => e.type === 'form').props.onSubmit({preventDefault(){}});
      await new Promise(resolve => setImmediate(resolve));
      const answerCard = view.elements(view.render()).find(e => e.type === 'article' && e.props.className === 'care-record-answer');
      const heading = view.elements(answerCard).find(e => e.type === 'small');
      assert.equal(heading.props.children.join(''), `${scenario.label} · 2026-09-01 ~ 2026-09-02`);
      view.elements(answerCard).find(e => e.type === 'button' && e.props.children === '내용 복사').props.onClick();
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(copied[0].split('\n')[0], `${scenario.label} · 2026-09-01 ~ 2026-09-02`);
      assert.equal(calls[0].body.default_resident_id, 'synthetic-resident');
      assert.equal(calls[0].body.resident_id, null);
    } finally {
      globalThis.window = previousWindow;
      if (previousNavigator) Object.defineProperty(globalThis, 'navigator', previousNavigator);
      else delete globalThis.navigator;
    }
  });
}

test('briefing action cold retry keeps its fixed scope and sends no records to preparation',async()=>{
  const calls=[];let questions=0;
  const view=harness(async(path,request)=>{
    calls.push({path,body:request.body});
    if(path.endsWith('model-ready'))return {status:'ready'};
    if(++questions===1)return {error_type:'model_preparing'};
    return {question:'선택한 기간의 돌봄 기록을 요약해 주세요.',answer:'합성 요약',evidence_ids:[],period_start:'2026-09-06',period_end:'2026-09-12',processing_method:'local_ai',generation_verified:true};
  });
  const action=view.elements(view.render()).find(e=>e.type==='button'&&e.props.children==='AI 브리핑 만들기');
  assert.ok(action);
  const prior=globalThis.window;globalThis.window={setInterval,clearInterval};
  try{
    action.props.onClick();action.props.onClick();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(calls.length,3);
    assert.equal(calls[1].body,undefined);
    assert.equal(calls[0].body,calls[2].body);
    assert.equal(view.elements(view.render()).find(e=>e.type==='button'&&e.props.children==='AI 브리핑 만들기').props.disabled,false);
  }finally{globalThis.window=prior;}
});

test('failed briefing retries the submitted briefing rather than an unrelated typed question',async()=>{
  const bodies=[];
  const view=harness(async(path,request)=>{
    bodies.push(request.body);
    if(bodies.length===1)throw new Error('synthetic connection failure');
    return {question:'선택한 기간의 돌봄 기록을 요약해 주세요.',answer:'합성 요약',evidence_ids:[],period_start:'2026-09-06',period_end:'2026-09-12',processing_method:'local_ai',generation_verified:true};
  });
  view.elements(view.render()).find(e=>e.type==='textarea').props.onChange({target:{value:'다른 질문 초안'}});
  const prior=globalThis.window;globalThis.window={setInterval,clearInterval};
  try{
    view.elements(view.render()).find(e=>e.type==='button'&&e.props.children==='AI 브리핑 만들기').props.onClick();
    await new Promise(resolve=>setImmediate(resolve));
    view.elements(view.render()).find(e=>e.type==='button'&&e.props.children==='질문 다시 시도').props.onClick();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(bodies.length,2);
    assert.equal(bodies[1],bodies[0]);
    assert.equal(JSON.parse(bodies[1]).range_mode,'fixed');
  }finally{globalThis.window=prior;}
});
