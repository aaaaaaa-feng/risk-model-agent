import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/shared/lib/utils";

/*
 * 状态徽章:收敛原 .tag / .status 两套体系。
 * mono 10px pill(--radius-full 是冷峻座舱中唯一允许的 full 圆角);
 * 状态色仅表语义,不作装饰。
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-[11px] py-[5px] font-mono text-[10px] font-bold leading-none",
  {
    variants: {
      variant: {
        neutral: "border-[var(--line)] bg-background font-normal text-foreground",
        muted: "border-transparent bg-[var(--ground)] text-foreground",
        network: "border-[var(--amber-border)] bg-[var(--amber-soft)] text-[var(--amber-text)]",
        ok: "border-[var(--green-border)] bg-[var(--green-soft)] text-[var(--green-text)]",
        attention: "border-[var(--red-border)] bg-[var(--red-soft)] text-[var(--red-text)]",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  },
);

export type BadgeVariant = VariantProps<typeof badgeVariants>["variant"];

/* 运行/任务状态 → 徽章语义色(对齐原 .status.* 映射) */
export function statusVariant(status: string): BadgeVariant {
  if (["ready", "trained", "succeeded"].includes(status)) return "ok";
  if (["queued", "running", "awaiting_decision"].includes(status)) return "network";
  if (["failed", "blocked"].includes(status)) return "attention";
  return "muted";
}

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

/* 用 span 而非 div:徽章常出现在表格单元格与行文内,保持 phrasing content 合法 */
function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
