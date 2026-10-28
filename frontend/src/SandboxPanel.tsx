import type { Receipt } from './sandbox-client'
import './sandbox.css'

export interface SandboxPanelProps {
  first: string
  second: string
  busy: boolean
  blocked: boolean
  needsCheck: boolean
  error: string
  receipt: Receipt | null
  inputId: string
  onFirst: (s: string) => void
  onSecond: (s: string) => void
  onRun: () => void
  onCheck: () => void
  onNew: () => void
}

// UI-side restriction only: plain decimal integer within Number's safe range.
// The server still accepts and computes over the full i64 range.
const INTEGER_RE = /^-?\d+$/

export function isSafeIntegerText(s: string): boolean {
  if (!INTEGER_RE.test(s)) return false
  return Number.isSafeInteger(Number(s))
}

// Fixed status labels; plain text so state is never conveyed by color alone.
const STATUS_TEXT: Record<string, string> = {
  RUNNING: '计算中，结果尚未确认',
  UNKNOWN: '结果未确认',
  SUCCEEDED: '计算完成',
  FAILED: '计算未成功',
}

// Fixed Chinese mapping for error codes; raw server messages are never shown.
const ERROR_TEXT: Record<string, string> = {
  UNKNOWN_OUTCOME: '暂时无法确认这次计算的结果，请点击“核对结果”核实，不要重复提交。',
  INVALID_INPUT: '输入的数字无法计算，请检查后重新开始。',
  OVERFLOW: '数字超出可计算范围，请换用较小的数字。',
  TIMEOUT: '计算超时，未得到结果。',
  DENIED: '计算程序被安全规则拒绝运行。',
}
const GENERIC_ERROR_TEXT = '计算未能完成，请稍后重新开始。'

function receiptStatus(receipt: Receipt | null): string | null {
  if (!receipt) return null
  const s = (receipt as { status?: unknown }).status
  return typeof s === 'string' ? s : null
}

function receiptResultText(receipt: Receipt | null): string | null {
  if (!receipt) return null
  const result = (receipt as { result?: { result_text?: unknown } | null }).result
  const text = result ? result.result_text : undefined
  return typeof text === 'string' ? text : null
}

function receiptErrorText(receipt: Receipt | null): string | null {
  if (!receipt) return null
  const err = (receipt as { error?: { code?: unknown } | null }).error
  if (!err) return null
  const code = typeof err.code === 'string' ? err.code : ''
  return Object.prototype.hasOwnProperty.call(ERROR_TEXT, code) ? ERROR_TEXT[code] : GENERIC_ERROR_TEXT
}

export default function SandboxPanel(props: SandboxPanelProps) {
  const {
    first, second, busy, blocked, needsCheck, error, receipt, inputId,
    onFirst, onSecond, onRun, onCheck, onNew,
  } = props

  const firstId = `${inputId}-first`
  const secondId = `${inputId}-second`
  const hintId = `${inputId}-hint`
  const statusId = `${inputId}-status`

  const firstOk = isSafeIntegerText(first)
  const secondOk = isSafeIntegerText(second)
  const status = receiptStatus(receipt)
  const unresolved = status === 'RUNNING' || status === 'UNKNOWN'
  const terminal = status === 'SUCCEEDED' || status === 'FAILED'

  // Any receipt blocks a new run: the root must clear it (via onNew) first.
  const runDisabled = !firstOk || !secondOk || busy || blocked || needsCheck || receipt !== null
  const showCheck = needsCheck || unresolved
  const checkDisabled = busy || blocked
  const newDisabled = !terminal || needsCheck || busy || blocked

  const resultText = status === 'SUCCEEDED' ? receiptResultText(receipt) : null
  const receiptError = status === 'SUCCEEDED' ? null : receiptErrorText(receipt)

  let statusText = '请输入两个整数，然后点击“计算平均值”。'
  if (busy) statusText = '正在处理，请稍候。'
  else if (status && STATUS_TEXT[status]) statusText = STATUS_TEXT[status]
  else if (needsCheck) statusText = '结果未确认'

  const invalidHint =
    (first !== '' && !firstOk) || (second !== '' && !secondOk)
      ? '请输入不带小数点的整数，且数字不能过大。'
      : '只能输入整数，例如 13 和 7。'

  return (
    <section className="ff-sandbox" aria-labelledby={`${inputId}-title`}>
      <h2 id={`${inputId}-title`} className="ff-sandbox__title">平均值计算演示</h2>

      <div className="ff-sandbox__notice">
        <p>本机计算演示，不会转账或扣款。</p>
        <p>平均值小数部分舍去（只保留整数部分）。</p>
        <p>使用预先生成的计算程序，不会临时生成新程序。</p>
      </div>

      <div className="ff-sandbox__fields">
        <div className="ff-sandbox__field">
          <label htmlFor={firstId}>第一个数</label>
          <input
            id={firstId}
            type="text"
            inputMode="numeric"
            autoComplete="off"
            disabled={busy || blocked || needsCheck || receipt !== null}
            value={first}
            aria-invalid={first !== '' && !firstOk}
            aria-describedby={hintId}
            onChange={(e) => onFirst(e.currentTarget.value)}
          />
        </div>
        <div className="ff-sandbox__field">
          <label htmlFor={secondId}>第二个数</label>
          <input
            id={secondId}
            type="text"
            inputMode="numeric"
            autoComplete="off"
            disabled={busy || blocked || needsCheck || receipt !== null}
            value={second}
            aria-invalid={second !== '' && !secondOk}
            aria-describedby={hintId}
            onChange={(e) => onSecond(e.currentTarget.value)}
          />
        </div>
      </div>
      <p id={hintId} className="ff-sandbox__hint">{invalidHint}</p>

      <div className="ff-sandbox__actions">
        <button
          type="button"
          className="ff-sandbox__btn ff-sandbox__btn--primary"
          disabled={runDisabled}
          onClick={() => onRun()}
        >
          计算平均值
        </button>
        {showCheck ? (
          <button
            type="button"
            className="ff-sandbox__btn"
            disabled={checkDisabled}
            onClick={() => onCheck()}
          >
            核对结果
          </button>
        ) : null}
        {receipt !== null ? (
          <button
            type="button"
            className="ff-sandbox__btn"
            disabled={newDisabled}
            onClick={() => onNew()}
          >
            开始另一次计算
          </button>
        ) : null}
      </div>

      <div id={statusId} className="ff-sandbox__status" role="status" aria-live="polite">
        <p className="ff-sandbox__status-line">状态：{statusText}</p>
        {showCheck ? (
          <p>结果尚未确认，请点击“核对结果”核实。核对只会查询，不会重新提交计算。</p>
        ) : null}
        {resultText !== null ? (
          <p className="ff-sandbox__result">
            平均值：<strong className="ff-sandbox__result-value">{resultText}</strong>
          </p>
        ) : null}
        {receiptError !== null ? <p className="ff-sandbox__error">{receiptError}</p> : null}
      </div>

      {error !== '' ? (
        <p className="ff-sandbox__error" role="alert">{error}</p>
      ) : null}
    </section>
  )
}
