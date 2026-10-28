import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { createCodegenClient } from './codegen-client'
import type { Provenance } from './codegen-client'
import { createCodegenController } from './codegen-controller'
import type { CodegenState } from './codegen-controller'
import CodegenPanel from './CodegenPanel'

// 挂载时只读：GET 会话与可用性，再由控制器 load 读取待核对记录；不会调用付费生成。
interface Props { blocked: boolean; runMutation: (task: () => Promise<void>) => Promise<void> }
type Availability = { available: boolean; provenance: Provenance | null }
const PENDING: CodegenState = { view: null, error: '', busy: true, needsCheck: true }
const INIT_FAIL = '暂时无法核对会话或AI计算状态，请点击“核对结果”重试。'
const START_FAIL = '操作未完成或已有任务在处理，请点击“核对结果”核实，切勿重复提交。'

export default function CodegenSection({ blocked, runMutation }: Props) {
  const client = useMemo(() => createCodegenClient(api), [])
  const controller = useRef<ReturnType<typeof createCodegenController> | null>(null)
  const alive = useRef(true), epoch = useRef(0)
  // 仅在本地 start 进行中为 true；其余时刻收到的记录来自只读恢复，不能与当前输入关联。
  const localStart = useRef(false)
  const [state, setState] = useState<CodegenState>(PENDING)
  const [availability, setAvailability] = useState<Availability>({ available: false, provenance: null })
  const [description, setDescription] = useState(''), [argsText, setArgsText] = useState('')
  const [consent, setConsent] = useState(false), [restored, setRestored] = useState(false)
  const inputId = useId()

  const apply = useCallback((value: CodegenState) => {
    setState(value)
    if (value.view && !localStart.current) {
      setDescription(''); setArgsText(''); setConsent(false); setRestored(true)
    }
  }, [])

  const initialize = useCallback(async () => {
    const generation = ++epoch.current
    const fresh = () => alive.current && generation === epoch.current
    controller.current?.dispose(); controller.current = null
    setState(PENDING)
    try {
      const [session, status] = await Promise.all([api<{ user: string }>('/session'), client.status()])
      if (!fresh()) return
      if (session.user !== 'demo' && session.user !== 'guest') throw new Error('unsupported session')
      setAvailability({ available: status.available, provenance: status.provenance })
      const next = createCodegenController({
        client, storage: sessionStorage, user: session.user,
        onChange(value) { if (fresh()) apply(value) },
      })
      controller.current = next
      await next.load() // 只读恢复；挂载绝不生成或运行。
      if (fresh()) apply(next.snapshot())
    } catch {
      if (fresh()) {
        controller.current?.dispose(); controller.current = null
        setAvailability({ available: false, provenance: null })
        setState({ view: null, error: INIT_FAIL, busy: false, needsCheck: true })
      }
    }
  }, [client, apply])

  useEffect(() => {
    alive.current = true
    void initialize()
    return () => { alive.current = false; ++epoch.current; controller.current?.dispose(); controller.current = null }
  }, [initialize])

  const locked = blocked || state.busy || state.needsCheck || !!state.view

  async function start() {
    const c = controller.current
    if (!c || locked || !availability.available || !consent) return
    try {
      await runMutation(async () => {
        if (c !== controller.current) return
        setRestored(false)
        localStart.current = true
        try { await c.start(description, argsText, consent) } finally { localStart.current = false }
      })
    } catch {
      if (alive.current) setState(v => ({ ...v, error: START_FAIL }))
    }
  }

  function check() {
    if (blocked || state.busy) return
    // 初始化失败时重新读取会话、可用性与记录，而不是直接解除锁定。
    if (controller.current) void controller.current.check()
    else void initialize()
  }

  function newRound() {
    if (blocked || state.busy || !controller.current?.newRound()) return
    setDescription(''); setArgsText(''); setConsent(false); setRestored(false)
  }

  return <section aria-label="AI生成计算">
    <h2>AI生成计算</h2>
    {restored && <p role="note">已读取最近一次AI计算记录。原输入没有保存在页面，下方空白输入与这条记录无关。</p>}
    <CodegenPanel description={description} argsText={argsText} consent={consent}
      available={availability.available} provenance={availability.provenance}
      view={state.view} error={state.error} busy={state.busy} needsCheck={state.needsCheck}
      blocked={blocked} inputId={inputId}
      onDescription={value => { if (!locked) setDescription(value) }}
      onArgs={value => { if (!locked) setArgsText(value) }}
      onConsent={value => { if (!locked) setConsent(value) }}
      onRun={() => { void start() }}
      onCheck={check}
      onNew={newRound} />
  </section>
}
