import { useCallback, useState } from "react";

export interface Toast {
  message: string;
  error: boolean;
}

export function useToast(duration = 3200) {
  const [toast, setToast] = useState<Toast | null>(null);

  const notify = useCallback(
    (message: string, error = false) => {
      setToast({ message, error });
      window.setTimeout(() => setToast(null), duration);
    },
    [duration],
  );

  return { toast, notify };
}
