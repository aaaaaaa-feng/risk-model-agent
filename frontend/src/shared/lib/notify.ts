import { toast } from "sonner";
import { errorMessage } from "./errors";

/**
 * 全局轻提示:签名与原 useToast().notify 完全一致,
 * 各调用点直接 import 使用,不再经 props 逐级下传。
 */
export function notify(message: unknown, error = false) {
  if (error) {
    const text = errorMessage(message);
    // 轮询或流重连可能在短时间内报告同一问题；固定 id 只更新同一条提示。
    toast.error(text, { id: `error:${text}` });
    return;
  }
  toast.success(typeof message === "string" && message.trim() ? message : "操作已完成");
}
