import { FileText, Wallet, CreditCard, BookOpen, ArrowUpRight } from 'lucide-react';

interface TaskShortcutsProps {
  onSend: (text: string) => void;
  disabled: boolean;
}

const SHORTCUTS = [
  { text: '查本月账单', label: '查询本月账单', icon: FileText },
  { text: '查询余额', label: '查询账户余额', icon: Wallet },
  { text: '看看有哪些订阅', label: '查看订阅与代扣', icon: CreditCard },
  { text: '查看记忆', label: '查看已保存的记忆与偏好', icon: BookOpen },
] as const;

export default function TaskShortcuts({ onSend, disabled }: TaskShortcutsProps) {
  return (
    <div className="task-shortcuts" role="group" aria-label="常用查询">
      {SHORTCUTS.map(({ text, label, icon: Icon }) => (
        <button
          key={text}
          type="button"
          className="task-shortcuts__button"
          aria-label={label}
          disabled={disabled}
          onClick={() => onSend(text)}
        >
          <span className="task-shortcuts__icon"><Icon size={17} aria-hidden="true" /></span>
          <span className="task-shortcuts__label">{text}</span>
          <ArrowUpRight size={14} aria-hidden="true" className="task-shortcuts__arrow" />
        </button>
      ))}
    </div>
  );
}
