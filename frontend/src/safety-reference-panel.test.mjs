import test from 'node:test'
import assert from 'node:assert/strict'
import {loadComponent,elements,textContent} from './component-test-loader.mjs'

const reference={schema_version:'1',reference_only:true,items:[{code:'LOAN_ADVANCE_FEE',title:'借款前先付费',why:'仅说明线索',checks:['自行打开官方应用核实'],reference_kind:'case_pattern',cases:[{id:'C01',title:'公开案例',publisher:'发布机构',url:'https://www.bypc.gov.cn/case.html',archived_date:'2026-10-25'}]}]}

test('panel presents archived data without authorization or payment controls',async()=>{
 const {default:Panel}=await loadComponent('SafetyReferencePanel.tsx')
 const tree=Panel({codes:['LOAN_ADVANCE_FEE','LOAN_ADVANCE_FEE'],reference,loading:false,error:false})
 const text=textContent(tree)
 assert.ok(text.includes('归档'));assert.ok(text.includes('借款前先付费'));assert.equal(elements(tree,'button').length,0)
 const anchors=elements(tree,'a');assert.equal(anchors.length,1)
 assert.equal(anchors[0].props.target,'_blank');assert.ok(anchors[0].props.rel.includes('noopener'));assert.ok(anchors[0].props.rel.includes('noreferrer'))
 assert.equal(elements(tree,'form').length,0)
})

test('panel explains unavailable and unknown references without guessed case evidence',async()=>{
 const {default:Panel}=await loadComponent('SafetyReferencePanel.tsx')
 for(const props of [{codes:['UNKNOWN'],reference,loading:false,error:false},{codes:['LOAN_ADVANCE_FEE'],reference:null,loading:false,error:true}]){
  const tree=Panel(props);assert.equal(elements(tree,'a').length,0);assert.ok(textContent(tree).includes('暂不可用'))
 }
})

test('panel supports all three approved government hosts',async()=>{
 const {default:Panel}=await loadComponent('SafetyReferencePanel.tsx')
 for(const host of ['www.huadu.gov.cn','nxca.miit.gov.cn']){
  const value=structuredClone(reference);value.items[0].cases[0].url='https://'+host+'/case.html'
  assert.equal(elements(Panel({codes:['LOAN_ADVANCE_FEE'],reference:value,loading:false}),'a').length,1)
 }
})

test('new archived sources render external links while lookalike hosts stay hidden',async()=>{
 const {default:Panel}=await loadComponent('SafetyReferencePanel.tsx')
 for(const host of ['gaj.wuhan.gov.cn','www.jiyuan.gov.cn','gaj.pds.gov.cn']){
  const value=structuredClone(reference)
  value.items[0].cases[0].url='https://'+host+'/case.html'
  const anchors=elements(Panel({codes:['LOAN_ADVANCE_FEE'],reference:value,loading:false}),'a')
  assert.equal(anchors.length,1,host)
  assert.equal(anchors[0].props.href,'https://'+host+'/case.html')
  assert.equal(anchors[0].props.target,'_blank')
  assert.ok(anchors[0].props.rel.includes('noopener'))
  value.items[0].cases[0].url='https://'+host+'.evil.example/case.html'
  assert.equal(elements(Panel({codes:['LOAN_ADVANCE_FEE'],reference:value,loading:false}),'a').length,0)
 }
})
