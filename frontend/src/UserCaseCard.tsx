import './operator-cases.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createUserCaseClient } from './operator-client'
import type { UserApi, CaseFacts, OperatorCase } from './operator-client'
import type { LocalHandoff } from './task-plan'
import { UserCasePanel } from './OperatorPanels'

export interface UserCaseCardProps {
  summary: LocalHandoff
  blocked: boolean
  api: UserApi
  runMutation?: (task: () => Promise<void>) => Promise<void>
}

const READ_ERROR = '暂时无法读取分享状态，请稍后刷新。请勿认为没有分享记录。'
const UNSURE = '操作结果未确认，可能已经保存，请刷新核对。'

export default function UserCaseCard({ summary, blocked, api, runMutation }: UserCaseCardProps) {
  const client = useMemo(() => createUserCaseClient(api), [api])
  const plan = summary.plan_id
  const [preview, setPreview] = useState<CaseFacts | null>(null)
  const [caseView, setCase] = useState<OperatorCase | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [consent, setConsent] = useState(false)
  const busyRef = useRef(false)
  const gen = useRef(0)
  const alive = useRef(true)

  // Read-only: lookup exact plan; preview only when no case exists (CANCELLED never gets a new preview).
  const load = useCallback(async () => {
    const g = ++gen.current
    setLoading(true); setConsent(false); setError('')
    try {
      const c = await client.lookup(plan)
      const p = c === null ? await client.preview(plan) : null
      if (!alive.current || g !== gen.current) return
      setCase(c); setPreview(p)
    } catch {
      if (!alive.current || g !== gen.current) return
      setCase(null); setPreview(null); setError(READ_ERROR)
    } finally {
      if (alive.current && g === gen.current) setLoading(false)
    }
  }, [client, plan])

  useEffect(() => {
    alive.current = true
    void load()
    return () => { alive.current = false; gen.current++ }
  }, [load])

  // Single write path: sync ref lock + blocked guard + global runMutation. No retry, no automatic POST.
  const mutate = async (task: () => Promise<OperatorCase>) => {
    if (busyRef.current || blocked || loading) return
    busyRef.current = true; setBusy(true); setNotice('')
    const run = runMutation ?? ((t: () => Promise<void>) => t())
    let failed = false
    try {
      await run(async () => {
        const c = await task()
        if (!alive.current) return
        gen.current++ // drop any in-flight stale read
        setCase(c); setPreview(null); setConsent(false)
      })
    } catch {
      failed = true
    } finally {
      busyRef.current = false
      if (alive.current) setBusy(false)
    }
    if (failed && alive.current) { setNotice(UNSURE); void load() }
  }

  const onShare = () => {
    const p = preview
    if (!p || consent !== true || caseView !== null || error) return
    void mutate(() => client.share(plan, p.plan_version, true, crypto.randomUUID()))
  }
  const onWithdraw = () => {
    const c = caseView
    if (!c || c.status === 'CANCELLED' || error) return
    void mutate(() => client.withdraw(c.id, c.version, crypto.randomUUID()))
  }
  const onRefresh = () => { if (!busyRef.current) { setNotice(''); void load() } }

  return (
    <UserCasePanel
      preview={preview} caseView={caseView} loading={loading} busy={busy} blocked={blocked}
      error={error} notice={notice} consent={consent} onConsent={v => setConsent(v === true)}
      onShare={onShare} onWithdraw={onWithdraw} onRefresh={onRefresh}
    />
  )
}
