"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { AuthGate } from "@/components/auth/AuthGate";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  if (pathname === "/login") {
    return <main className="min-h-screen bg-gray-50 dark:bg-gray-950">{children}</main>;
  }
  return (
    <AuthGate>
      <div className="flex h-screen bg-gray-50 dark:bg-gray-950">
        <Sidebar />
        <div className="flex flex-col flex-1 overflow-hidden">
          <Header />
          <main className="flex-1 overflow-y-auto p-6">{children}</main>
        </div>
      </div>
    </AuthGate>
  );
}
