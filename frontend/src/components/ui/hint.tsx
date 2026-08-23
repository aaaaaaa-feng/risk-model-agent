import { useState } from "react";
import { CircleHelp } from "lucide-react";

/*
 * 说明提示:替代以往写在标题下方的说明小字。
 * hover 即显示气泡;点击问号可锁定展开(触屏/键盘可用),再点或 Esc 以外区域由用户自行关闭。
 */
export function Hint({ text, label = "查看说明" }: { text: string; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="hint">
      <button
        type="button"
        className="hint-trigger"
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <CircleHelp aria-hidden />
      </button>
      <span className={`hint-bubble ${open ? "open" : ""}`} role="tooltip">
        {text}
      </span>
    </span>
  );
}
