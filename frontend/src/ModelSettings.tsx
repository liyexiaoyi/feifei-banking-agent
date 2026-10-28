import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { api, sessionContext } from './api'
import './model-settings.css'

export const OFFICIAL_BASE = 'https://api.deepseek.com'
export type Mode = 'model' | 'rules'
export type Snapshot = { provider: 'deepseek'; base_url: string; model: string; mode: Mode; has_key: boolean; editable: boolean; restart_required: boolean }
export type SettingsInput = { model: string; mode: Mode; api_key: string; consent: boolean }
const MODEL_RE = /^[A-Za-z0-9_.:-]{1,100}$/
const KEY_RE = /^[\x21\x23-\x26\x28-\x7e]{12,512}$/
const LOAD_FAIL = '设置暂时无法读取，请稍后重试。'
const SAVE_FAIL = '保存未完成，请检查输入后重试。'
const SAVED = '保存成功，重启本机服务后生效。'

// Allowlist contract fields only; anything else from the server is dropped.
export function pickSnapshot(d: any): Snapshot | null {
  if (!d || d.provider !== 'deepseek' || d.base_url !== OFFICIAL_BASE || typeof d.model !== 'string' || !MODEL_RE.test(d.model) || (d.mode !== 'model' && d.mode !== 'rules')) return null
  return { provider: 'deepseek', base_url: OFFICIAL_BASE, model: d.model, mode: d.mode, has_key: d.has_key === true, editable: d.editable === true, restart_required: d.restart_required === true }
}

export function validateSettings(v: SettingsInput, hasKey: boolean): boolean {
  if (!MODEL_RE.test(v.model) || (v.mode !== 'model' && v.mode !== 'rules')) return false
  if (v.api_key !== '' && !KEY_RE.test(v.api_key)) return false
  if (v.mode === 'model' && (v.consent !== true || (v.api_key === '' && !hasKey))) return false
  return true
}

export function buildBody(v: SettingsInput): SettingsInput {
  return { model: v.model, mode: v.mode, api_key: v.api_key, consent: v.consent === true }
}

export default function ModelSettings({ blocked }: { blocked: boolean }) {
  const titleId = useId(), modelId = useId(), modeId = useId(), keyId = useId(), hintId = useId()
  const trigger = useRef<HTMLButtonElement>(null), first = useRef<HTMLButtonElement>(null), box = useRef<HTMLDivElement>(null)
  const run = useRef(0), wasOpen = useRef(false)
  const [open, setOpen] = useState(false)
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [model, setModel] = useState('')
  const [mode, setMode] = useState<Mode>('rules')
  const [key, setKey] = useState('')
  const [consent, setConsent] = useState(false)
  const [phase, setPhase] = useState<'idle' | 'loading' | 'saving'>('idle')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  function reset() { run.current++; setSnap(null); setModel(''); setMode('rules'); setKey(''); setConsent(false); setPhase('idle'); setMsg(''); setErr('') }
  function close() { reset(); setOpen(false) }
  // A result is rendered only if no newer run started and the session epoch is unchanged.
  function guard() {
    const token = ++run.current, epoch = sessionContext.snapshot().epoch
    return () => token === run.current && sessionContext.snapshot().epoch === epoch && sessionContext.snapshot().status !== 'blocked'
  }

  useEffect(() => {
    const off = sessionContext.subscribe(() => { reset(); setOpen(false) })
    return () => { off(); run.current++ }
  }, [])
  useEffect(() => {
    if (open) first.current?.focus()
    else if (wasOpen.current) trigger.current?.focus()
    wasOpen.current = open
  }, [open])

  function load() {
    const live = guard()
    setPhase('loading')
    api<unknown>('/model-settings')
      .then(d => { if (!live()) return; const s = pickSnapshot(d); if (!s) { setErr(LOAD_FAIL); return } setSnap(s); setModel(s.model); setMode(s.mode) })
      .catch(() => { if (live()) setErr(LOAD_FAIL) })
      .finally(() => { if (live()) setPhase('idle') })
  }
  function openDialog() { reset(); setOpen(true); load() }

  const s = sessionContext.snapshot()
  const editable = !!snap?.editable && s.status === 'owned' && s.owner === 'demo'

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!snap || !editable || phase !== 'idle' || blocked) return
    const body = buildBody({ model, mode, api_key: key, consent })
    setMsg(''); setErr('')
    if (!validateSettings(body, snap.has_key)) { setKey(''); setConsent(false); setErr(SAVE_FAIL); return }
    const live = guard()
    first.current?.focus()
    setPhase('saving')
    try {
      const d: any = await api<unknown>('/model-settings', body)
      if (!live()) return
      const next = pickSnapshot(d)
      if (!next || d.saved !== true) { setErr(SAVE_FAIL); return }
      setSnap(next); setModel(next.model); setMode(next.mode); setMsg(SAVED)
    } catch { if (live()) setErr(SAVE_FAIL) }
    finally { setKey(''); setConsent(false); if (live()) setPhase('idle') }
  }

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') { e.preventDefault(); close(); return }
    if (e.key !== 'Tab' || !box.current) return
    const f = Array.from(box.current.querySelectorAll<HTMLElement>('button,input,select')).filter(x => !(x as HTMLInputElement).disabled)
    if (!f.length) return
    const a = f[0], z = f[f.length - 1]
    if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus() }
    else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus() }
  }

  return <>
    <button ref={trigger} type="button" className="model-settings-trigger" aria-haspopup="dialog" disabled={blocked} onClick={openDialog}>模型/API设置</button>
    {open && <div className="model-settings-backdrop">
      <div ref={box} className="model-settings-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={onKey}>
        <div className="model-settings-head"><h2 id={titleId}>模型/API设置</h2><button ref={first} type="button" className="model-settings-close" onClick={close}>关闭</button></div>
        <form className="model-settings-form" onSubmit={e => { void save(e) }} noValidate>
          {snap && !editable && <p className="model-settings-note">当前为只读。仅演示所有者可修改：请在服务器环境设置 FF_ALLOW_MODEL_SETTINGS=1，并重启本机服务。</p>}
          <fieldset className="model-settings-fields" disabled={!snap || !editable || phase !== 'idle'}>
            <div className="model-settings-row"><span>接口地址（固定）</span><code>{OFFICIAL_BASE}</code></div>
            <label className="model-settings-row" htmlFor={modelId}>模型名称<input id={modelId} type="text" value={model} maxLength={100} autoComplete="off" spellCheck={false} onChange={e => setModel(e.target.value)} /></label>
            <label className="model-settings-row" htmlFor={modeId}>运行方式<select id={modeId} value={mode} onChange={e => setMode(e.target.value === 'model' ? 'model' : 'rules')}><option value="rules">规则模式（不调用模型）</option><option value="model">模型模式（DeepSeek 官方接口）</option></select></label>
            <label className="model-settings-row" htmlFor={keyId}>API 密钥<input id={keyId} type="password" value={key} maxLength={512} autoComplete="new-password" spellCheck={false} aria-describedby={hintId} onChange={e => setKey(e.target.value)} /></label>
            <p id={hintId} className="model-settings-hint">{snap?.has_key ? '已保存密钥。留空表示继续使用已保存的密钥。' : '尚未保存密钥。使用模型模式需要填写。'}</p>
            <label className="model-settings-check"><input type="checkbox" checked={consent} required={mode === 'model'} onChange={e => setConsent(e.target.checked)} />我同意将后续对话发送至DeepSeek官方接口</label>
            <button type="submit" className="model-settings-submit" disabled={blocked}>{phase === 'saving' ? '正在保存…' : '保存到本机'}</button>
          </fieldset>
          {snap?.restart_required && !msg && <p className="model-settings-hint">已有待生效的设置，重启本机服务后生效。</p>}
          <p className="model-settings-status" role="status">{phase === 'loading' ? '正在读取设置…' : msg}</p>
          {err && <p className="model-settings-error" role="alert">{err} 如尚未启用，请在本机.env设置 FF_ALLOW_MODEL_SETTINGS=1 后重启。</p>}
        </form>
      </div>
    </div>}
  </>
}
