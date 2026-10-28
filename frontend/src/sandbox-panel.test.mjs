import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'
let Panel;try{Panel=(await loadComponent('SandboxPanel.tsx')).default}catch(e){if(e.code!=='ENOENT')throw e}
const base={first:'13',second:'7',busy:false,blocked:false,needsCheck:false,error:'',receipt:null,inputId:'sandbox-unit',onFirst(){},onSecond(){},onRun(){},onCheck(){},onNew(){}}
function tree(v={}){assert.ok(Panel,'SandboxPanel not implemented');return Panel({...base,...v})}
function button(t,name){const b=elements(t,'button').find(b=>textContent(b).includes(name));assert.ok(b);return b}

test('large labels and explicit calculation button without a payment control',()=>{const t=tree();assert.equal(elements(t,'input').length,2);assert.equal(elements(t,'label').length,2);assert.equal(button(t,'计算平均值').props.disabled,false);assert.match(textContent(t),/不会.*转账|不.*转账/);assert(!elements(t,'button').some(b=>/转账|付款|申购/.test(textContent(b))))})

test('busy blocked unknown state and bad numbers all prevent new run',()=>{for(const flags of [{busy:true},{blocked:true},{needsCheck:true},{first:''},{first:'1.5'},{first:'9007199254740992'}])assert.equal(button(tree(flags),'计算平均值').props.disabled,true)})

test('unknown result gives read-only check without resubmitting',()=>{let runs=0,checks=0;const t=tree({needsCheck:true,onRun(){runs++},onCheck(){checks++},receipt:{id:'sbx_x',status:'UNKNOWN',result:null,error:{code:'UNKNOWN_OUTCOME',message:'RAW_PRIVATE'}}});assert.match(textContent(t),/核实|核对/);assert.doesNotMatch(textContent(t),/RAW_PRIVATE/);button(t,'核对结果').props.onClick();assert.equal(checks,1);assert.equal(runs,0)})

test('result uses decimal text including zero, never rounds to a JSON number',()=>{for(const result_text of ['0','9223372036854775807']){const t=tree({receipt:{id:'sbx_x',status:'SUCCEEDED',result:{result_text},error:null}});assert(textContent(t).includes(result_text));assert.doesNotMatch(textContent(t),/MFA|CAS|fuel/)}})


test('busy or unresolved inputs look disabled instead of silently ignoring edits',()=>{for(const flags of [{busy:true},{blocked:true},{needsCheck:true}])assert(elements(tree(flags),'input').every(i=>i.props.disabled===true))})
