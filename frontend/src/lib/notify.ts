import { toast } from "sonner";

/**
 * 全局轻提示:签名与原 useToast().notify 完全一致,
 * 各调用点直接 import 使用,不再经 props 逐级下传。
 */
export function notify(message: string, error = false) {
  if (error) toast.error(message);
  else toast.success(message);
}
