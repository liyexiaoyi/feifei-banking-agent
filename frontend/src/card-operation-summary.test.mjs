import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,textContent} from './component-test-loader.mjs'
import {renderToStaticMarkup} from 'react-dom/server'
const module=await loadComponent('CardOperationSummary.tsx').catch(()=>null),Summary=module?.default
const payload={card_id:'card_a_daily',account_id:'a_daily',card_name:'日常演示卡',masked_number:'DEMO ···· 0826',amount_cents:12345,merchant_name:'演示超市',simulated:true}
function text(type,p=payload){assert.ok(Summary,'CardOperationSummary.tsx must exist');return textContent(Summary({op:{id:'op_summary',type,risk:'RED',status:'WAITING_CONFIRMATION',payload:p,result:null,created_at:1,expires_at:301}}))}
test('spend summary binds clear merchant amount and masked card with local-only notice',()=>{const s=text('card_spend');assert.match(s,/演示超市/);assert.match(s,/123.45/);assert.match(s,/0826/);assert.match(s,/模拟/);assert.doesNotMatch(s,/已到账|真实银行已执行/)})
test('control summary states actual lock/limit changes in plain language',()=>{const s=text('card_controls_update',{...payload,action:'set_single_limit',before:{locked:false,single_limit_cents:200000,daily_limit_cents:500000},after:{locked:false,single_limit_cents:100000,daily_limit_cents:500000}});assert.match(s,/单笔/);assert.match(s,/2,?000.00/);assert.match(s,/1,?000.00/)})
test('credential summary never dumps reference secret or hash fields',()=>{const s=text('card_credential_change',{...payload,challenge_id:'cc_never_show',new_secret:'SECRET_NEVER_SHOW',verifier:'HASH_NEVER_SHOW'});assert.match(s,/演示口令/);assert(!/cc_never_show|SECRET_NEVER_SHOW|HASH_NEVER_SHOW/.test(s));assert.match(s,/真实银行卡密码/)})
test('unverified data yields a receipt-only instruction',()=>{assert.ok(Summary);assert.match(renderToStaticMarkup(Summary({op:null})),/核对|回执/)})
