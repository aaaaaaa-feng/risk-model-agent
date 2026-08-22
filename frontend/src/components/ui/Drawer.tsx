import { useRef } from "react";
import { useFocusTrap } from "../../hooks/useFocusTrap";

interface DrawerProps {
  open: boolean;
  titleId: string;
  onClose: () => void;
  children: React.ReactNode;
  initialFocusRef?: React.RefObject<HTMLElement>;
}

export function Drawer({ open, titleId, onClose, children, initialFocusRef }: DrawerProps) {
  const drawerRef = useRef<HTMLElement>(null);
  useFocusTrap(drawerRef, { active: open, onClose, initialFocusRef });

  if (!open) return null;
  return (
    <>
      <div
        className="drawer-scrim"
        role="presentation"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      />
      <aside
        ref={drawerRef}
        className="settings-drawer open"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        {children}
      </aside>
    </>
  );
}
