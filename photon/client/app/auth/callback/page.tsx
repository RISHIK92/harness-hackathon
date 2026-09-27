"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { setToken } from "@/lib/api";

// GitHub OAuth callback lands here with the token in the URL fragment
// (#token=...), not a query string — the fragment never leaves the
// browser, so it's never logged server-side or forwarded in a Referer
// header. See server/app/routers/auth.py::github_callback.
export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const hash = window.location.hash;
    const match = /token=([^&]+)/.exec(hash);
    if (!match) {
      setError("Missing token in GitHub sign-in redirect.");
      return;
    }
    setToken(decodeURIComponent(match[1]));
    // Drop the token from the URL/history before navigating away.
    window.history.replaceState(null, "", window.location.pathname);
    router.replace("/home");
  }, [router]);

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 flex items-center justify-center px-6">
      <div className="text-sm text-neutral-400">
        {error ? (
          <>
            <p className="text-red-400">{error}</p>
            <a href="/login" className="text-indigo-400 hover:text-indigo-300">
              Back to sign in
            </a>
          </>
        ) : (
          "Signing you in…"
        )}
      </div>
    </div>
  );
}
