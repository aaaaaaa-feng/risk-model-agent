import * as React from "react";
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

/* 勾选框:17px(对齐原 accent-color 控件尺寸),选中态钴蓝 */
const Checkbox = React.forwardRef<
  React.ElementRef<typeof CheckboxPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
>(({ className, title, "aria-label": ariaLabel, ...props }, ref) => (
  <CheckboxPrimitive.Root
    ref={ref}
    className={cn(
      "peer h-[17px] w-[17px] shrink-0 rounded-sm border border-[var(--line-strong)] bg-background focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-[var(--blue)] data-[state=checked]:bg-[var(--blue)] data-[state=checked]:text-white",
      className,
    )}
    title={title || (typeof ariaLabel === "string" ? ariaLabel : "勾选或取消此选项")}
    aria-label={ariaLabel}
    {...props}
  >
    <CheckboxPrimitive.Indicator className="flex items-center justify-center text-current">
      <Check className="h-3.5 w-3.5" strokeWidth={3} />
    </CheckboxPrimitive.Indicator>
  </CheckboxPrimitive.Root>
));
Checkbox.displayName = CheckboxPrimitive.Root.displayName;

export { Checkbox };
