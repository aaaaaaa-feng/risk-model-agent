import * as React from "react";

import { cn } from "@/shared/lib/utils";

/* 输入框:40px 高、16px 圆角(密集表单不用全胶囊);聚焦紫环由 base.css 全局规则提供 */
const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex h-10 w-full rounded-lg border border-[var(--line-strong)] bg-background px-3.5 py-2 text-sm text-foreground transition-colors placeholder:text-muted-foreground/70 focus-visible:outline-none disabled:cursor-not-allowed disabled:border-[var(--disabled-bg)] disabled:bg-[var(--disabled-bg)] disabled:text-[var(--disabled-text)]",
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";

export { Input };
