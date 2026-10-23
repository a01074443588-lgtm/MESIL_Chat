import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import test from 'node:test';
import ts from 'typescript';
const require = createRequire(import.meta.url);
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const source = readFileSync(new URL('../app/components/AdminDrawer.tsx',import.meta.url),'utf8');
const compiled = ts.transpileModule(source + '\nexport {directStaffRegistrationPayload, staffIdentityPayload, emptyEmployee};',{
  compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022},
}).outputText;
const product = {exports:{}};
let apiCalls = 0;
const dependencies = {
  react:React,
  'react/jsx-runtime':require('react/jsx-runtime'),
  '../api':{apiBase:'',apiErrorMessage:()=>'SYNTHETIC-ERROR',apiFetch:()=>{apiCalls++;throw Error('NETWORK_NOT_ALLOWED');}},
  '../navigationHistory':{pushUiHistory:()=>{},closeUiHistory:()=>{}},
  './AdminConversationReview':{AdminConversationReview:()=>null},
};
new Function('require','module','exports',compiled)(name=>{
  assert(name in dependencies, 'UNREVIEWED_RUNTIME_DEPENDENCY');return dependencies[name];
},product,product.exports);
const {AdminDrawer,directStaffRegistrationPayload,staffIdentityPayload,emptyEmployee} = product.exports;
const employee = (id,status,name)=>({staff_id:id,display_name:name,employee_code:id,
  employment_status:status,is_active:status==='active',login_user_id:null,login_status:'not_issued',
  service_assignments:[],legacy_assignment:{business:null,department:null,floor:null,team:null,job_name:null,position_title:null},
});
const base = {open:true,onClose(){},units:[],jobs:[],positionTitles:[],employees:[],managedRooms:[],residents:[],
  staffDirectory:[employee('SYNTHETIC-A','active','합성재직직원'),employee('SYNTHETIC-R','retired','합성퇴사직원')],
  currentUserId:'SYNTHETIC-ADMIN',onDataChanged:async()=>{}};
test('default employee view hides retired staff and does not render external roster controls',()=>{
  const html=renderToStaticMarkup(React.createElement(AdminDrawer,base));
  assert.match(html,/합성재직직원/);
  assert.doesNotMatch(html,/합성퇴사직원/);
  assert.match(html,/>직원 등록<\/button>/);
  assert.doesNotMatch(html,/직원정보 갱신|최신 직원정보 확인|직원정보 연결 승인 기준/);
  assert.equal(apiCalls,0);
});
test('internal direct-registration view remains usable without external roster prerequisites',()=>{
  // Simulates the state selected by the existing new-employee button; no browser/API request.
  const html=renderToStaticMarkup(React.createElement(AdminDrawer,{...base,initialTab:'new-employee'}));
  assert.match(html,/직원 직접 등록/);
  assert.match(html,/로그인 계정도 발급/);
  assert.match(html,/기관 직원을 이곳에서 직접 등록/);
  assert.doesNotMatch(html,/직원정보 갱신/);
  assert.equal(apiCalls,0);
});
test('direct registration payload separates optional login credentials from staff identity',()=>{
  const draft={...emptyEmployee,full_name:'합성직원',username:'synthetic-user',password:'SYNTHETIC-NOT-A-SECRET'};
  const withoutLogin=directStaffRegistrationPayload(draft,false);
  assert.equal(withoutLogin.full_name,'합성직원');
  assert.equal(withoutLogin.issue_login,false);
  assert.equal('username' in withoutLogin,false);
  assert.equal('temporary_password' in withoutLogin,false);
  assert.equal(directStaffRegistrationPayload(draft,true).issue_login,true);
  assert.deepEqual(Object.keys(staffIdentityPayload(draft)).sort(),['employee_code','full_name']);
});
