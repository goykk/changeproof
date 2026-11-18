"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  { href: "/", label: "变更分析", match: (pathname: string) => pathname === "/" || pathname.startsWith("/changesets") },
  { href: "/eval", label: "策略评测", match: (pathname: string) => pathname.startsWith("/eval") },
];

export function NavTabs() {
  const pathname = usePathname() ?? "/";

  return (
    <nav className="flex h-11 items-stretch gap-5 text-[12px]">
      {ITEMS.map((item) => {
        const active = item.match(pathname);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`relative flex items-center whitespace-nowrap border-b-2 px-0.5 font-medium ${
              active
                ? "border-blue-600 text-slate-950"
                : "border-transparent text-slate-500 hover:border-blue-200 hover:text-slate-900"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
