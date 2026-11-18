import type { ButtonHTMLAttributes, ReactNode } from "react";

export type BadgeTone = "neutral" | "blue" | "green" | "orange" | "red" | "purple";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "border-slate-200 bg-white text-slate-600",
  blue: "border-blue-200 bg-blue-50 text-blue-700",
  green: "border-emerald-200 bg-emerald-50 text-emerald-700",
  orange: "border-amber-200 bg-amber-50 text-amber-700",
  red: "border-red-200 bg-red-50 text-red-700",
  purple: "border-violet-200 bg-violet-50 text-violet-700",
};

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-[3px] border px-1.5 py-0.5 text-[10px] font-medium leading-4 ${BADGE_TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function Status({ tone = "green", children }: { tone?: "green" | "red" | "amber" | "blue" | "gray"; children: ReactNode }) {
  const dot = {
    green: "bg-emerald-500",
    red: "bg-red-500",
    amber: "bg-amber-500",
    blue: "bg-blue-500",
    gray: "bg-slate-400",
  }[tone];
  const text = {
    green: "text-emerald-700",
    red: "text-red-700",
    amber: "text-amber-700",
    blue: "text-blue-700",
    gray: "text-slate-500",
  }[tone];

  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-[10px]">
      <span className={`h-1.5 w-1.5 shrink-0 ${dot}`} />
      <span className={text}>{children}</span>
    </span>
  );
}

const BUTTON_VARIANTS = {
  primary: "border border-blue-600 bg-blue-600 text-white hover:border-blue-700 hover:bg-blue-700 disabled:hover:bg-blue-600",
  secondary:
    "border border-slate-300 bg-white text-slate-700 hover:border-blue-300 hover:bg-blue-50/40 disabled:hover:bg-white",
  ghost: "border border-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-900",
  danger: "border border-red-600 bg-red-600 text-white hover:bg-red-700 hover:border-red-700",
};

const BUTTON_SIZES = {
  sm: "h-7 px-2.5 text-[11px]",
  md: "h-8 px-3 text-[12px]",
  lg: "h-9 px-4 text-[12px]",
};

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof BUTTON_VARIANTS;
  size?: keyof typeof BUTTON_SIZES;
};

export function Button({ variant = "primary", size = "md", className = "", ...props }: ButtonProps) {
  return (
    <button
      className={`inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-[4px] font-medium disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400 ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
      {...props}
    />
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  className = "",
  bodyClassName = "",
  flush = false,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  flush?: boolean;
}) {
  return (
    <section className={`cp-panel overflow-hidden ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-[#f8fafc] px-5 py-3">
          <div className="min-w-0">
            {title && <h2 className="text-[13px] font-semibold leading-5 text-slate-900">{title}</h2>}
            {description && <p className="mt-0.5 text-[11px] leading-4 text-slate-500">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? bodyClassName : `p-5 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  description,
  meta,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="text-[24px] font-semibold leading-8 tracking-[-0.02em] text-slate-950">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-[12px] leading-5 text-slate-500">{description}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[10px] text-slate-500">{meta}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2 pb-0.5">{actions}</div>}
    </header>
  );
}

export function Metric({
  label,
  value,
  tone = "default",
}: {
  label: ReactNode;
  value: ReactNode;
  tone?: "default" | "blue" | "green" | "amber" | "red";
}) {
  const toneClass = {
    default: "text-slate-900",
    blue: "text-blue-700",
    green: "text-emerald-700",
    amber: "text-amber-700",
    red: "text-red-700",
  }[tone];

  return (
    <span className="inline-flex items-baseline gap-2 whitespace-nowrap">
      <span className="text-[11px] text-slate-500">{label}</span>
      <span className={`cp-mono text-[14px] font-medium tabular-nums ${toneClass}`}>{value}</span>
    </span>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="py-1">
      <h3 className="text-[12px] font-medium text-slate-600">{title}</h3>
      {description && <p className="mt-1 max-w-lg text-[11px] leading-5 text-slate-400">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ error }: { error: { code?: string; message: string } | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-[4px] border border-red-200 border-l-2 border-l-red-500 bg-red-50 px-3 py-2 text-[11px] leading-5 text-red-800">
      <span className="cp-mono text-[10px] font-semibold text-red-600">{error.code ?? "error"}</span>
      <span className="ml-2 break-words">{error.message}</span>
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-500">
      <svg className="h-3 w-3 animate-spin text-blue-500" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-80" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
      </svg>
      {label}
    </span>
  );
}
