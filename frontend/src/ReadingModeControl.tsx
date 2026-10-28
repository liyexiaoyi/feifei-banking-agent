import type { ReadingMode } from './reading-mode'

type Props = {
  mode: ReadingMode
  onChange: (mode: ReadingMode) => void
}

export default function ReadingModeControl({ mode, onChange }: Props) {
  return <div className="reading-mode-control" role="group" aria-label="阅读方式">
    <span>阅读方式</span>
    <button type="button" aria-pressed={mode === 'normal'} onClick={() => onChange('normal')}>标准</button>
    <button type="button" aria-pressed={mode === 'comfortable'} onClick={() => onChange('comfortable')}>舒适大字</button>
  </div>
}
