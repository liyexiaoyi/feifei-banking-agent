import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import vm from 'node:vm'
const src = readFileSync(new URL('./ModelSettings.tsx', import.meta.url), 'utf8')
const css = readFileSync(new URL('./model-settings.css', import.meta.url), 'utf8')
const unit = {exports:{}}
const js = ts.transpileModule(src.replace(/^import .*$/gm, ''), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText
vm.runInNewContext(js, {exports:unit.exports,require:()=>({})})
const m = unit.exports
const need = () => { assert(m?.pickSnapshot && m?.validateSettings && m?.buildBody, 'model settings helpers missing'); return m }
const base = { provider: 'deepseek', base_url: 'https://api.deepseek.com', model: 'deepseek-flash', mode: 'rules', has_key: false, editable: true, restart_required: false }

test('no browser persistence, logging, query strings or raw network calls', () => {
  for (const re of [/localStorage/, /sessionStorage/, /indexedDB/, /document\.cookie/, /console\./, /URLSearchParams|location\.|\?api_key/, /\bfetch\(/, /XMLHttpRequest/]) assert.doesNotMatch(src, re)
})

test('exactly one GET and one explicit POST against the fixed contract path', () => {
  const calls = [...src.matchAll(/api<[^>]*>\(([^)]*)\)/g)].map(x => x[1].replace(/\s/g, ''))
  assert.deepEqual(calls, ["'/model-settings'", "'/model-settings',body"])
  assert.equal(src.match(/\bload\(\)/g).length, 2, 'one declaration and one GET invocation from openDialog')
  assert.match(src, /function openDialog\(\)\s*\{[^}]*load\(\)/)
  assert.match(src, /onSubmit=/)
})

test('key field is password, never filled from server, cleared after save', () => {
  assert.match(src, /type="password"/)
  assert.match(src, /autoComplete="new-password"/)
  const args = [...src.matchAll(/setKey\(([^)]*)\)/g)].map(x => x[1])
  assert(args.length > 0 && args.every(a => a === "''" || a === 'e.target.value'), 'setKey only blank or user input')
  assert.match(src, /finally\s*\{\s*setKey\(''\);\s*setConsent\(false\)/)
})

test('required copy, dialog semantics and fixed official URL', () => {
  for (const t of ['模型/API设置', '我同意将后续对话发送至DeepSeek官方接口', '保存到本机', '保存成功，重启本机服务后生效。', 'FF_ALLOW_MODEL_SETTINGS=1', "'https://api.deepseek.com'"]) assert(src.includes(t), t)
  assert.match(src, /role="dialog"/); assert.match(src, /aria-modal="true"/); assert.match(src, /aria-labelledby=\{titleId\}/)
  assert.match(src, /'Escape'/); assert.match(src, /trigger\.current\?\.focus\(\)/); assert.match(src, /sessionContext\.subscribe/)
  assert.match(src, /return \{ provider: 'deepseek', base_url: OFFICIAL_BASE/)
})

test('pickSnapshot allowlists contract fields and rejects unofficial endpoints', () => {
  const { pickSnapshot } = need()
  const s = pickSnapshot({ ...base, has_key: true, api_key: '[REDACTED]', OTHER_ENV: 'x' })
  assert.deepEqual(Object.keys(s).sort(), ['base_url', 'editable', 'has_key', 'mode', 'model', 'provider', 'restart_required'])
  assert.equal(JSON.stringify(s).includes('sk-should'), false)
  assert.equal(pickSnapshot({ ...base, base_url: 'https://evil.example' }), null)
  assert.equal(pickSnapshot({ ...base, mode: 'other' }), null)
  assert.equal(pickSnapshot({ ...base, model: 'a b' }), null)
})

test('validateSettings enforces model regex, key charset/length, consent and key presence', () => {
  const { validateSettings: v } = need()
  const ok = { model: 'deepseek-flash', mode: 'rules', api_key: '', consent: false }
  assert.equal(v(ok, false), true)
  assert.equal(v({ ...ok, model: '' }, false), false)
  assert.equal(v({ ...ok, model: 'x'.repeat(101) }, false), false)
  assert.equal(v({ ...ok, model: 'a\nb' }, false), false)
  assert.equal(v({ ...ok, api_key: 'a'.repeat(11) }, false), false)
  assert.equal(v({ ...ok, api_key: 'a'.repeat(12) }, false), true)
  assert.equal(v({ ...ok, api_key: 'a'.repeat(513) }, false), false)
  assert.equal(v({ ...ok, api_key: 'abcdefghijk\rl' }, false), false)
  assert.equal(v({ ...ok, api_key: 'abcdef ghijkl' }, false), false)
  assert.equal(v({ ...ok, mode: 'model', api_key: 'a'.repeat(20), consent: false }, false), false)
  assert.equal(v({ ...ok, mode: 'model', consent: true }, false), false)
  assert.equal(v({ ...ok, mode: 'model', consent: true }, true), true)
})

test('buildBody sends exactly the contract fields', () => {
  const { buildBody } = need()
  const b = buildBody({ model: 'm', mode: 'rules', api_key: '', consent: false, path: '/etc/x', base_url: 'https://x' })
  assert.deepEqual(Object.keys(b).sort(), ['api_key', 'consent', 'mode', 'model'])
})

test('styles are scoped, readable and large enough for touch', () => {
  const selectors = css.match(/[^{}]+(?=\{)/g).flatMap(x => x.split(',')).map(x => x.trim()).filter(Boolean)
  assert(selectors.every(x => x.startsWith('.model-settings')), 'all selectors scoped')
  assert.match(css, /min-height: 44px/)
  assert.match(css, /font-size: 16px/)
})
