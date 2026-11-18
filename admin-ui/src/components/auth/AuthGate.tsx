"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getApiKey } from "@/lib/auth";

/** Sends the user to /login until an admin key is stored for this tab. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (pathname === "/login") {
      setReady(true);
      return;
    }
    if (!getApiKey()) {
      router.replace(`/login?next=${encodeURIComponent(pathname || "/")}`);
      return;
    }
    setReady(true);
  }, [pathname, router]);

  if (!ready) return null;
  return <>{children}</>;
}
