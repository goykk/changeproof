import type { Metadata } from "next";
import Link from "next/link";
import { NavTabs } from "@/components/NavTabs";
import "./globals.css";

export const metadata: Metadata = {
  title: "ChangeProof",
  description: "变更影响分析与回归测试平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-[#f4f5f7]">
        <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/95 backdrop-blur supports-[backdrop-filter]:bg-white/85">
          <div className="mx-auto flex h-11 max-w-[1120px] items-center gap-6 px-4 sm:px-6">
            <Link href="/" className="shrink-0 text-[15px] font-semibold tracking-[-0.02em] text-slate-950" aria-label="ChangeProof 首页">
              ChangeProof
            </Link>
            <NavTabs />
          </div>
        </header>
        <main className="mx-auto max-w-[1120px] px-4 py-6 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
