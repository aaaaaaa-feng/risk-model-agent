import { useRef } from "react";
import { useFocusTrap } from "../../hooks/useFocusTrap";

interface DialogProps {
  open: boolean;
  titleId: string;
  onClose: () => void;
  children: React.ReactNode;
  className?: string;
}

export function Dialog({ open, titleId, onClose, children, className = "modal" }: DialogProps) {
  const dialogRef = useRef<HTMLElement>(null);
  useFocusTrap(dialogRef, { active: open, onClose });

  if (!open) return null;
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className={className}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        {children}
      </section>
    </div>
  );
}
