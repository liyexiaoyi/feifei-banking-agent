import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { createInvestmentClient } from './investment-profile'
import type { Answers, Comparison, ProfileStatus, ProfileView, QuestionId, Questionnaire } from './investment-profile'
import { api, ApiError, requestKey } from './api'
import Panel from './InvestmentProfilePanel'
import './investment-profile.css'

interface Props {
  blocked: boolean
  runMutation?: (task: () => Promise<void>) => Promise<void>
  onStatus?: (status: ProfileStatus | null) => void
}

// Known backend codes mapped to plain Chinese; raw server text is never shown.
const CODES: Record<string, string> = {
  VERSION_CONFLICT: '测评记录已发生变化，请点击刷新查看最新结果。',
  PROFILE_VERSION_CONFLICT: '测评记录已发生变化，请点击刷新查看最新结果。',
  SURVEY_VERSION_MISMATCH: '问卷已更新，请点击刷新后重新填写。',
  INVALID_ANSWERS: '答案不完整或无效，请刷新后重新填写。',
  CONFIRMATION_REQUIRED: '需要先勾选确认后才能提交。',
  IDEMPOTENCY_CONFLICT: '该次提交状态不确定，请点击刷新确认。',
  REQUEST_KEY_CONFLICT: '该次提交状态不确定，请点击刷新确认。',
  RATE_LIMITED: '操作过于频繁，请稍后点击刷新。',
  UNAUTHORIZED: '登录状态已失效，请重新进入页面。',
  CSRF_INVALID: '页面会话已过期，请重新进入页面。',
  ACCOUNT_LOCKED: '账户当前受限，暂时不能提交。',
  SAFETY_HOLD: '安全保护生效中，暂时不能提交。',
}
const describe = (e: unknown): string =>
  e instanceof ApiError ? CODES[e.code] ?? '请求未完成，请点击刷新确认最新结果。' : '暂时无法完成，请点击刷新重试。'
const valid = (v: unknown): v is number => Number.isInteger(v) && (v as number) >= 0 && (v as number) <= 3

export default function InvestmentProfileSection({ blocked, runMutation, onStatus }: Props) {
  const client = useMemo(() => createInvestmentClient(api), [])
  const inputId = useId()
  const [survey, setSurvey] = useState<Questionnaire | null>(null)
  const [view, setView] = useState<ProfileView | null>(null)
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [needsCheck, setNeedsCheck] = useState(false)
  // Ephemeral draft: React state only, never persisted, logged or sent anywhere but submit.
  const [active, setActive] = useState(false)
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState<Partial<Answers>>({})
  const [consent, setConsent] = useState(false)
  const lockRef = useRef(false), genRef = useRef(0), aliveRef = useRef(true)
  const wrapRef = useRef<HTMLDivElement>(null)
  const statusRef = useRef(onStatus)
  useEffect(() => { statusRef.current = onStatus }, [onStatus])

  const clearDraft = useCallback(() => {
    setActive(false); setStep(0); setAnswers({}); setConsent(false)
  }, [])

  // Read-only load; a newer generation or unmount discards late results. No auto retry.
  const load = useCallback(async (): Promise<boolean> => {
    const gen = ++genRef.current
    const current = () => aliveRef.current && gen === genRef.current
    setLoading(true); setError(''); statusRef.current?.(null)
    try {
      const r = await client.load()
      if (!current()) return false
      setSurvey(r.survey); setView(r.view); setComparison(r.comparison)
      statusRef.current?.(r.view.status)
      return true
    } catch (e) {
      if (!current()) return false
      setSurvey(null); setView(null); setComparison(null)
      statusRef.current?.(null)
      setError(describe(e))
      return false
    } finally {
      if (current()) setLoading(false)
    }
  }, [client])

  useEffect(() => {
    aliveRef.current = true
    void load()
    return () => { aliveRef.current = false; genRef.current += 1 }
  }, [load])

  useEffect(() => {
    if (active) wrapRef.current?.querySelector<HTMLFieldSetElement>('fieldset')?.focus()
  }, [active, step])

  const onStart = () => {
    if (lockRef.current || blocked || busy || loading || needsCheck || !survey) return
    setError(''); setNotice(''); setAnswers({}); setStep(0); setConsent(false); setActive(true)
  }
  const onCancel = () => {
    if (lockRef.current || busy) return
    clearDraft(); void load()
  }
  const onRefresh = async () => {
    if (lockRef.current || busy) return
    if (await load() && aliveRef.current) { setNeedsCheck(false); setNotice('') }
  }
  const onAnswer = (id: QuestionId, value: number | null) => setAnswers(prev => {
    const next = { ...prev }
    if (valid(value)) next[id] = value; else delete next[id]
    return next
  })
  const onBack = () => setStep(s => (s > 0 ? s - 1 : s))
  const onNext = () => {
    const q = survey?.questions[step]
    if (survey && q && valid(answers[q.id]) && step < survey.questions.length - 1) setStep(step + 1)
  }

  const onSubmit = async () => {
    if (lockRef.current || blocked || busy || loading || needsCheck || !active || !consent || !survey || !view) return
    if (!survey.questions.every(q => valid(answers[q.id]))) return
    lockRef.current = true
    const payload = Object.fromEntries(survey.questions.map(q => [q.id, answers[q.id]])) as Answers
    const expected = view.profile?.version ?? 0
    const key = requestKey() // held for this single attempt only; never regenerated automatically
    genRef.current += 1 // drop any in-flight read so it cannot overwrite post-submit state
    setBusy(true); setError(''); setNotice('')
    const task = async () => { await client.submit(payload, survey.survey_version, expected, key, true) }
    try {
      try {
        await (runMutation ? runMutation(task) : task())
      } catch (e) {
        if (!aliveRef.current) return
        setView(null); setComparison(null); statusRef.current?.(null)
        clearDraft(); setNeedsCheck(true); setError(describe(e))
        setNotice('本次提交结果尚未核对，可能已保存。请先刷新核对，不会自动重发。')
        return
      }
      if (!aliveRef.current) return
      clearDraft()
      const ok = await load() // fresh read decides the current result, not the receipt
      if (!aliveRef.current) return
      if (ok) setNotice('演示测评已提交，下方为重新读取的当前结果。')
      else { setNeedsCheck(true); setNotice('提交可能已完成，但最新结果读取失败，请点击刷新确认。') }
    } finally {
      lockRef.current = false
      if (aliveRef.current) setBusy(false)
    }
  }

  return (
    <div ref={wrapRef} className="investment-profile">
      <Panel
        survey={survey} view={view} comparison={comparison} loading={loading} busy={busy}
        blocked={blocked || needsCheck} error={error} notice={notice} active={active} step={step}
        answers={answers} consent={consent} inputId={inputId}
        onStart={onStart} onCancel={onCancel} onAnswer={onAnswer} onBack={onBack} onNext={onNext}
        onConsent={v => setConsent(v === true)} onSubmit={() => void onSubmit()} onRefresh={() => void onRefresh()}
      />
    </div>
  )
}
