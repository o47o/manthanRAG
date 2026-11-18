"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { AuthError, clearApiKey } from "@/lib/auth";

// A 401 (key revoked or wrong) anywhere sends the user back to the key screen.
function onError(err: unknown) {
  if (err instanceof AuthError && err.status === 401 && typeof window !== "undefined") {
    clearApiKey();
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError }),
        mutationCache: new MutationCache({ onError }),
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: (count, err) => !(err instanceof AuthError) && count < 1,
          },
        },
      })
  );

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
