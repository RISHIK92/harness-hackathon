"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getToken } from "@/lib/api";
import EscalationInbox from "./EscalationInbox";

/** Client-side route guard.
 *
 * Deliberately NOT Next 16's proxy.ts (renamed from middleware): the token
 * lives in localStorage, which the server cannot read, so a proxy check
 * would have nothing to check. The real protection is server-side anyway —
 * every /api route requires the bearer token and workspace membership
 * (server/tests/test_tenancy.py). This only decides which screen to show.
 */
export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) router.replace("/login");
    else setReady(true);
  }, [router]);

  if (!ready) return <div className="min-h-screen bg-neutral-950" />;
  // The inbox rides along on every signed-in screen: when your agent needs
  // you mid-meeting, you are rarely on the page the call started from.
  return (
    <>
      {children}
      <EscalationInbox />
    </>
  );
}
