import { useEffect, useId, useRef } from 'react'
import type { KeyboardEvent } from 'react'

export type DraftReplaceDialogProps = {
  suggestion: string
  blocked: boolean
  onConfirm: () => void
  onCancel: () => void
}

// Mount only while a pending suggestion is waiting for the user's choice.
export default function DraftReplaceDialog({ suggestion, blocked, onConfirm, onCancel }: DraftReplaceDialogProps) {
  const titleId = useId()
  const descId = useId()
  const box = useRef<HTMLDivElement>(null)
  const keep = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const prev = document.activeElement instanceof HTMLElement ? document.activeElement : null
    keep.current?.focus()
    return () => {
      if (prev && prev.isConnected) prev.focus()
    }
  }, [])

  function confirm() {
    if (blocked) return
    onConfirm()
  }

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onCancel()
      return
    }
    if (e.key !== 'Tab' || !box.current) return
    const f = Array.from(box.current.querySelectorAll<HTMLButtonElement>('button')).filter(x => !x.disabled)
    if (!f.length) { e.preventDefault(); return }
    const a = f[0], z = f[f.length - 1]
    const active = document.activeElement
    if (!f.includes(active as HTMLButtonElement)) { e.preventDefault(); (e.shiftKey ? z : a).focus() }
    else if (e.shiftKey && active === a) { e.preventDefault(); z.focus() }
    else if (!e.shiftKey && active === z) { e.preventDefault(); a.focus() }
  }

  return <div className="draft-dialog-backdrop">
    <div ref={box} className="draft-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descId} onKeyDown={onKey}>
      <h2 id={titleId}>替换已输入的内容？</h2>
      <p id={descId}>输入框里已有内容。选择替换后，已输入的内容会换成下面这条建议。</p>
      <p>{suggestion}</p>
      <div className="draft-dialog-actions">
        <button ref={keep} type="button" onClick={onCancel}>保留草稿</button>
        <button type="button" disabled={blocked} onClick={confirm}>替换草稿</button>
      </div>
    </div>
  </div>
}
