export type SessionUser = 'demo' | 'guest'

export function isSessionUser(value: unknown): value is SessionUser {
  return value === 'demo' || value === 'guest'
}

export class SessionChangedError extends Error {
  code = 'SESSION_CHANGED' as const
  constructor() {
    super('会话已变更，请重新加载页面后核对原记录。')
    this.name = 'SessionChangedError'
  }
}

export type SessionSnapshot =
  | { readonly status: 'unknown'; readonly epoch: number }
  | { readonly status: 'owned'; readonly owner: SessionUser; readonly epoch: number }
  | { readonly status: 'blocked'; readonly epoch: number }

export type SessionListener = (snapshot: SessionSnapshot) => void

export interface SessionContext {
  snapshot(): SessionSnapshot
  epoch(): number
  observe(user: unknown): void
  block(): void
  assertCurrent(epoch: number): void
  subscribe(listener: SessionListener): () => void
}

export function createSessionContext(): SessionContext {
  let status: 'unknown' | 'owned' | 'blocked' = 'unknown'
  let owner: SessionUser | null = null
  let epoch = 0
  const listeners = new Set<SessionListener>()

  function snapshot(): SessionSnapshot {
    if (status === 'owned' && owner !== null) return Object.freeze({ status: 'owned' as const, owner, epoch })
    if (status === 'blocked') return Object.freeze({ status: 'blocked' as const, epoch })
    return Object.freeze({ status: 'unknown' as const, epoch })
  }

  function notify(): void {
    for (const listener of Array.from(listeners)) {
      try {
        listener(snapshot())
      } catch {
        // Observer faults must not undo committed state or block other subscribers.
      }
    }
  }

  function block(): void {
    if (status === 'blocked') return
    status = 'blocked'
    owner = null
    epoch += 1
    notify()
  }

  return {
    snapshot,
    epoch: () => epoch,
    observe(user: unknown): void {
      if (status === 'blocked' || !isSessionUser(user)) return
      if (status === 'unknown') {
        status = 'owned'
        owner = user
        notify()
        return
      }
      if (owner !== user) block()
    },
    block,
    assertCurrent(expected: number): void {
      if (status === 'blocked' || expected !== epoch) throw new SessionChangedError()
    },
    subscribe(listener: SessionListener): () => void {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
  }
}
