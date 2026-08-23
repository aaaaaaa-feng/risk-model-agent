import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

/*
 * 视觉对齐"Tofu 嘉年华":胶囊按钮(rounded-full)、字重 700、默认高 42px / 紧凑 34px;
 * 主按钮黑色行动层(hover --black-hover),禁用态走 --disabled-* token。
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-sm font-bold transition-colors outline-none disabled:pointer-events-none [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "border border-primary bg-primary text-primary-foreground hover:bg-[var(--black-hover)] hover:border-[var(--black-hover)] disabled:bg-[var(--disabled-bg)] disabled:border-[var(--disabled-bg)] disabled:text-[var(--disabled-text)]",
        destructive:
          "border border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:bg-[var(--disabled-bg)] disabled:border-[var(--disabled-bg)] disabled:text-[var(--disabled-text)]",
        outline:
          "border border-[var(--line-strong)] bg-background text-foreground hover:bg-[var(--ground-hover)] hover:border-[var(--ink)] disabled:bg-[var(--disabled-bg)] disabled:border-[var(--disabled-bg)] disabled:text-[var(--disabled-text)]",
        secondary:
          "border border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:bg-[var(--disabled-bg)] disabled:text-[var(--disabled-text)]",
        ghost: "hover:bg-accent hover:text-accent-foreground disabled:opacity-50",
        link: "text-[var(--blue)] font-bold underline-offset-4 hover:underline disabled:text-[var(--quiet)]",
        destructiveOutline:
          "border border-[var(--red-border)] bg-background text-[var(--red-text)] hover:bg-[var(--red-soft)] disabled:opacity-50",
      },
      size: {
        default: "h-[42px] px-[18px]",
        sm: "h-[34px] px-3.5 text-[11px]",
        lg: "h-10 px-8",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
