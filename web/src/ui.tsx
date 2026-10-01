import * as Select from "@radix-ui/react-select";
import * as Tooltip from "@radix-ui/react-tooltip";
import clsx from "clsx";
import { Check, ChevronDown } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { forwardRef } from "react";

export const cn = clsx;

export function Tip({ label, keys, children, side = "bottom" }: {
  label: ReactNode;
  keys?: string;
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
}) {
  return (
    <Tooltip.Root delayDuration={350}>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          side={side}
          sideOffset={6}
          className="pop z-50 flex max-w-72 items-center gap-2 rounded-lg bg-ink px-2.5 py-1.5 text-[11.5px] leading-snug text-paper shadow-lg"
        >
          <span>{label}</span>
          {keys && <Kbd inverted>{keys}</Kbd>}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function Kbd({ children, inverted }: { children: ReactNode; inverted?: boolean }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[5px] px-1 font-sans text-[10.5px] font-medium",
        inverted ? "bg-paper/15 text-paper/80" : "border border-line-strong bg-surface text-muted",
      )}
    >
      {children}
    </kbd>
  );
}

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean;
  tone?: "default" | "accent";
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { className, active, tone = "default", children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      {...rest}
      className={cn(
        "inline-flex h-8 items-center justify-center gap-1.5 rounded-lg px-2 text-[12.5px] font-medium transition-colors outline-none",
        "focus-visible:ring-2 focus-visible:ring-accent/40 disabled:pointer-events-none disabled:opacity-35",
        tone === "accent"
          ? "bg-accent text-accent-ink shadow-sm hover:brightness-110"
          : active
            ? "bg-accent-soft text-accent"
            : "text-ink-2 hover:bg-sunken hover:text-ink",
        className,
      )}
    >
      {children}
    </button>
  );
});

/** A labelled pill that opens a list - the query bar is built from these. */
export function PillSelect<T extends string | null>({
  label, value, options, onChange, width,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; hint?: string }[];
  onChange: (v: T) => void;
  width?: number;
}) {
  const enc = (v: T) => (v === null ? "__none__" : String(v));
  const current = options.find((o) => o.value === value);
  return (
    <Select.Root value={enc(value)} onValueChange={(v) => onChange((v === "__none__" ? null : v) as T)}>
      <Select.Trigger
        className="group inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-left outline-none transition-colors hover:bg-sunken focus-visible:ring-2 focus-visible:ring-accent/40 data-[state=open]:bg-sunken"
        style={width ? { minWidth: width } : undefined}
      >
        {label && <span className="text-[11px] font-medium uppercase tracking-[0.06em] text-muted">{label}</span>}
        <span className="truncate text-[12.5px] font-semibold text-ink">
          <Select.Value>{current?.label ?? "—"}</Select.Value>
        </span>
        <ChevronDown className="size-3.5 shrink-0 text-muted transition-transform group-data-[state=open]:rotate-180" />
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          position="popper"
          sideOffset={6}
          className="pop z-50 max-h-[min(420px,var(--radix-select-content-available-height))] min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-xl border border-line bg-raised p-1 shadow-lg"
        >
          <Select.Viewport>
            {options.map((o) => (
              <Select.Item
                key={enc(o.value)}
                value={enc(o.value)}
                className="relative flex cursor-default items-center gap-3 rounded-lg py-1.5 pr-8 pl-2.5 text-[12.5px] text-ink outline-none select-none data-[highlighted]:bg-sunken"
              >
                <div className="flex flex-col">
                  <Select.ItemText>{o.label}</Select.ItemText>
                  {o.hint && <span className="text-[11px] text-muted">{o.hint}</span>}
                </div>
                <Select.ItemIndicator className="absolute right-2.5">
                  <Check className="size-3.5 text-accent" />
                </Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Viewport>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg bg-sunken p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-6 rounded-md px-2.5 text-[11.5px] font-medium transition-all",
            value === o.value ? "bg-raised text-ink shadow-sm" : "text-muted hover:text-ink-2",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted", className)}>
      {children}
    </div>
  );
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      {icon && <div className="mb-3 grid size-10 place-items-center rounded-xl bg-sunken text-muted">{icon}</div>}
      <div className="text-[13px] font-semibold text-ink">{title}</div>
      {children && <div className="mt-1.5 max-w-72 text-[12px] leading-relaxed text-muted">{children}</div>}
    </div>
  );
}

/** The app's mark: three bars, the last still growing. */
export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <rect x="8" y="17" width="4" height="8" rx="1.5" fill="var(--accent-ink)" />
      <rect x="14" y="11" width="4" height="14" rx="1.5" fill="var(--accent-ink)" />
      <rect x="20" y="7" width="4" height="18" rx="1.5" fill="var(--accent-ink)" opacity=".55" />
    </svg>
  );
}
