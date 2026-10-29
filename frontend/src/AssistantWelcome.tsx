export interface AssistantWelcomeProps {
  name: string
  disabled: boolean
  onDraft: (text: string) => void
}

const EXAMPLES: readonly string[] = Object.freeze([
  '查看本月账单',
  '看看有哪些订阅',
  '给妈妈转300元',
  '妈妈生日预算500元帮我安排',
])

export default function AssistantWelcome({ name, disabled, onDraft }: AssistantWelcomeProps) {
  const displayName = typeof name === 'string' ? name.trim() : ''
  const greeting = displayName ? `你好，${displayName}` : '你好'
  const blocked = disabled === true
  const draft = (text: string) => {
    if (blocked || typeof onDraft !== 'function') return
    onDraft(text)
  }
  return (
    <section className="ff-welcome" aria-label="开始对话">
      <div className="ff-welcome__mark" aria-hidden="true">FF</div>
      <h3 className="ff-welcome__title">{greeting}</h3>
      <p className="ff-welcome__copy">我是菲菲，FF Agent 的银行业务助手。可以帮你查账单、看订阅、准备转账。</p>
      <div className="ff-welcome__examples" role="group" aria-label="试试这样说">
        {EXAMPLES.map(text => (
          <button
            key={text}
            type="button"
            className="ff-welcome__example"
            disabled={blocked}
            onClick={() => draft(text)}
          >
            {text}
          </button>
        ))}
      </div>
      <p className="ff-welcome__footnote">先填写，发送后再处理</p>
      <p className="ff-welcome__note">金额与结果以模拟银行记录为准</p>
    </section>
  )
}
