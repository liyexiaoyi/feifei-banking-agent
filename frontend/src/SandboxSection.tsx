import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { PROVIDER_AVERAGE_WAT, createSandboxClient } from './sandbox-client'
import { createSandboxController } from './sandbox-controller'
import type { ControllerState } from './sandbox-controller'
import SandboxPanel from './SandboxPanel'

interface Props { blocked: boolean; runMutation: (task: () => Promise<void>) => Promise<void> }
export default function SandboxSection({ blocked, runMutation }: Props) {
  const client = useMemo(() => createSandboxClient(api), [])
  const controller = useRef<ReturnType<typeof createSandboxController> | null>(null)
  const alive = useRef(true), epoch = useRef(0)
  // Provenance: true only while a direct local run() is in flight. Any receipt
  // emitted outside that span came from a read-only recovery (load/check) and
  // cannot be tied to the visible inputs, so the inputs are cleared.
  const localRun = useRef(false)
  const [view, setView] = useState<ControllerState>({ receipt: null, error: '', busy: true, needsCheck: true })
  const [first, setFirst] = useState('13'), [second, setSecond] = useState('7')
  const [restored, setRestored] = useState(false)
  const inputId = useId()

  const initialize = useCallback(async () => {
    const generation = ++epoch.current
    setView({ receipt: null, error: '', busy: true, needsCheck: true })
    try {
      const [session, digest] = await Promise.all([
        api<{ user: string }>('/session'),
        crypto.subtle.digest('SHA-256', new TextEncoder().encode(PROVIDER_AVERAGE_WAT)),
      ])
      if (!alive.current || generation !== epoch.current) return
      if (session.user !== 'demo' && session.user !== 'guest') throw new Error('unsupported session')
      const sourceHash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
      const next = createSandboxController({
        client, storage: sessionStorage, user: session.user, sourceHash, wat: PROVIDER_AVERAGE_WAT,
        onChange(value) {
          if (!alive.current || generation !== epoch.current) return
          setView(value)
          // Clear-on-read: any receipt not produced by a direct local run is restored.
          if (value.receipt && !localRun.current) {
            setFirst(''); setSecond(''); setRestored(true)
          }
        },
      })
      controller.current?.dispose()
      controller.current = next
      await next.load() // Only reads; mounting never runs a program.
    } catch {
      if (alive.current && generation === epoch.current) {
        setView({ receipt: null, error: '暂时无法核对会话或计算记录，请点击“核对结果”。', busy: false, needsCheck: true })
      }
    }
  }, [client])

  useEffect(() => {
    alive.current = true
    void initialize()
    return () => { alive.current = false; ++epoch.current; controller.current?.dispose(); controller.current = null }
  }, [initialize])

  async function run() {
    if (blocked || view.busy || view.needsCheck || view.receipt || !controller.current) return
    try {
      await runMutation(async () => {
        setRestored(false)
        localRun.current = true
        try { await controller.current?.run(first, second) } finally { localRun.current = false }
      })
    } catch {
      if (alive.current) setView(v => ({ ...v, error: '已有任务正在处理，请稍后再计算。' }))
    }
  }

  return <div>
    {restored && <p role="note">已读取最近一次计算记录。原输入没有保存在页面，下方空白输入不会改变这条结果。</p>}
    <SandboxPanel first={first} second={second} {...view} inputId={inputId} blocked={blocked}
      onFirst={value => { if (!view.busy && !view.needsCheck && !view.receipt && !blocked) setFirst(value) }}
      onSecond={value => { if (!view.busy && !view.needsCheck && !view.receipt && !blocked) setSecond(value) }}
      onRun={() => { void run() }}
      onCheck={() => { if (!blocked && !view.busy) { if (controller.current) void controller.current.check(); else void initialize() } }}
      onNew={() => { if (!blocked && controller.current?.newRound()) { setFirst(''); setSecond(''); setRestored(false) } }} />
  </div>
}
