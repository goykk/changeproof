"use client";

import type { ReactNode } from "react";

export type TabItem = {
  key: string;
  label: string;
  badge?: ReactNode;
  tone?: "blue" | "green" | "amber" | "red";
};

const BADGE_TONES = {
  blue: "bg-blue-50 text-blue-700",
  green: "bg-emerald-50 text-emerald-700",
  amber: "bg-amber-50 text-amber-700",
  red: "bg-red-50 text-red-700",
};

export function Tabs({
  items,
  active,
  onChange,
  className = "",
}: {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}) {
  return (
    <div className={`overflow-x-auto overflow-y-hidden border-b border-slate-200 bg-[#fbfcfe] px-4 ${className}`}>
      <div role="tablist" className="flex min-w-max gap-1">
        {items.map((item) => {
          const selected = item.key === active;
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => onChange(item.key)}
              className={`relative -mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-[11px] ${
                selected
                  ? "border-blue-600 bg-white font-semibold text-slate-950"
                  : "border-transparent text-slate-500 hover:text-slate-900"
              }`}
            >
              {item.label}
              {item.badge !== undefined && (
                <span
                  className={`cp-mono rounded-[3px] px-1 py-0.5 text-[9px] tabular-nums ${
                    selected
                      ? item.tone
                        ? BADGE_TONES[item.tone]
                        : "bg-slate-100 text-slate-500"
                      : "bg-slate-100 text-slate-400"
                  }`}
                >
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
