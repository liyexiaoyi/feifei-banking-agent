import type { CSSProperties } from 'react'
import type { GenerationView, Provenance } from './codegen-client'

// 无状态展示组件：状态与回调全部由 CodegenSection 提供；挂载时不触发任何请求。
export interface CodegenPanelProps {
  description: string; argsText: string; consent: boolean; available: boolean
  provenance: Provenance | null; view: GenerationView | null; error: string
  busy: boolean; needsCheck: boolean; blocked: boolean; inputId: string
  onDescription: (value: string) => void; onArgs: (value: string) => void; onConsent: (value: boolean) => void
  onRun: () => void; onCheck: () => void; onNew: () => void
}

const BUTTON: CSSProperties = { minHeight: 48, minWidth: 140, padding: '8px 20px', marginRight: 12, fontSize: 18 }
const FIELD: CSSProperties = { display: 'block', width: '100%', minHeight: 48, fontSize: 18, boxSizing: 'border-box', marginTop: 4 }
const LABEL: CSSProperties = { display: 'block', fontSize: 18, fontWeight: 600, marginTop: 16 }
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/
const GENERIC_ERROR = '操作未完成，请点击“核对结果”手动核实，切勿重复提交。'

function descriptionValid(text: string): boolean {
  return text.trim().length > 0 && text.length <= 300 && !LONE_SURROGATE.test(text)
}
// 与控制器相同规则：逗号、中文逗号或空格分隔，0 到 8 个安全整数（可为负）。
function argsValid(text: string): boolean {
  const parts = text.split(/[,，\s]+/).filter(Boolean)
  return parts.length <= 8 && parts.every(p => /^-?\d{1,16}$/.test(p) && Number.isSafeInteger(Number(p)))
}
function sourceLabel(p: Provenance | null | undefined): string {
  return p === 'fixture' ? '本机测试生成' : p === 'model' ? 'AI生成程序' : '未知'
}
// 只显示中文固定提示；含英文或代码的原始错误一律替换为固定文字。
function safeError(error: string): string {
  if (!error) return ''
  return /[\u4e00-\u9fff]/.test(error) && !/[A-Za-z]/.test(error.replace(/AI/g, '')) ? error : GENERIC_ERROR
}

export default function CodegenPanel(props: CodegenPanelProps) {
  const { description, argsText, consent, available, provenance, view, error, busy, needsCheck, blocked, inputId } = props
  const g = view?.generation
  const run = view?.run
  const known = g?.status === 'SUCCEEDED' || g?.status === 'FAILED'
  const terminal = !!view && known && !needsCheck
  const text = run?.result?.result_text
  // 仅当生成与运行都已成功时显示结果；未知/待定状态即使运行成功也不显示。
  const resultText = terminal && g?.status === 'SUCCEEDED' && run?.status === 'SUCCEEDED' && typeof text === 'string' ? text : null
  const locked = busy || blocked || needsCheck || !!view
  const canRun = available && consent && descriptionValid(description) && argsValid(argsText) && !locked
  const canCheck = !busy && !blocked
  const canNew = terminal && !busy && !blocked
  const id = (s: string) => `${inputId}-${s}`
  let status = ''
  if (busy) status = '正在处理，请稍候…'
  else if (resultText !== null) status = ''
  else if (terminal) status = '本次计算未成功，没有结果可显示。可以点击“开始新计算”。'
  else if (view || needsCheck) status = '结果待核对：生成或计算是否完成尚未确认。请点击“核对结果”查询，切勿重复提交。'
  const err = safeError(error)

  return <div>
    <p>只做算术参考：不会转账，不会代替金额确认，也不连接银行。</p>
    <p>{available ? `计算来源：${sourceLabel(provenance)}` : 'AI计算未连接'}</p>
    {g && <p>{`这条记录来源：${sourceLabel(g.provenance)}`}</p>}
    <label htmlFor={id('description')} style={LABEL}>计算方法</label>
    <textarea id={id('description')} value={description} maxLength={300} rows={3} disabled={locked} style={FIELD}
      aria-describedby={id('description-hint')} onChange={e => { if (!locked) props.onDescription(e.target.value) }} />
    <p id={id('description-hint')}>用中文写清要算什么，最多300字，例如：求两个整数的差。</p>
    <label htmlFor={id('args')} style={LABEL}>整数参数</label>
    <input id={id('args')} type="text" inputMode="text" autoComplete="off" value={argsText} disabled={locked} style={FIELD}
      aria-describedby={id('args-hint')} onChange={e => { if (!locked) props.onArgs(e.target.value) }} />
    <p id={id('args-hint')}>可留空；最多8个整数，用逗号或空格分隔，例如：13,7</p>
    <label htmlFor={id('consent')} style={{ ...LABEL, display: 'flex', alignItems: 'center', gap: 12 }}>
      <input id={id('consent')} type="checkbox" checked={consent} disabled={locked} style={{ width: 28, height: 28 }}
        onChange={e => { if (!locked) props.onConsent(e.target.checked) }} />
      同意本次调用AI生成并运行计算程序
    </label>
    <div style={{ marginTop: 16 }}>
      <button type="button" style={BUTTON} disabled={!canRun} onClick={() => { if (canRun) props.onRun() }}>生成并计算</button>
      <button type="button" style={BUTTON} disabled={!canCheck} onClick={() => { if (canCheck) props.onCheck() }}>核对结果</button>
      <button type="button" style={BUTTON} disabled={!canNew} onClick={() => { if (canNew) props.onNew() }}>开始新计算</button>
    </div>
    <div role="status" aria-live="polite">
      {status && <p>{status}</p>}
      {resultText !== null && <p>计算结果：<strong>{resultText}</strong></p>}
    </div>
    {err && <p role="alert">{err}</p>}
  </div>
}
