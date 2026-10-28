export type BirthdayFlowCardProps = {
  flowId: string | null
  blocked?: boolean
  onOpen: (id: string) => void
}

const FLOW_ID = /^[0-9a-f]{32}$/

function safeId(v: unknown): string | null {
  return typeof v === 'string' && FLOW_ID.test(v) ? v : null
}

// Pure chat reference card: no cached amounts/status, opens only on explicit click.
export default function BirthdayFlowCard(props: BirthdayFlowCardProps) {
  const id = safeId(props.flowId)
  const disabled = !id || props.blocked === true
  const onClick = () => {
    const current = safeId(props.flowId)
    if (!current || props.blocked === true) return
    props.onOpen(current)
  }
  return (
    <div role="group" aria-label="生日安排引用">
      {id ? (
        <>
          <p>已保存生日安排引用</p>
          <p>请打开核对当前步骤和付款回执</p>
        </>
      ) : (
        <p role="status">无法核对引用</p>
      )}
      <button type="button" className="outline-button" style={{ minHeight: 44 }} disabled={disabled} aria-disabled={disabled} onClick={onClick}>查看生日安排</button>
    </div>
  )
}
