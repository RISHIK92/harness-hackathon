"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useDocProgress, useSignedIn } from "./scroll";

const LINKS = [
  { href: "#turn", label: "A turn" },
  { href: "#languages", label: "Languages" },
  { href: "#hiring", label: "Instead of hiring" },
  { href: "#rules", label: "Rules" },
];

/**
 * Fixed page chrome: the hairline scroll-progress bar and the nav, which
 * stays invisible over the hero and settles into a floating bar once the
 * focus sequence is done.
 */
export default function Chrome() {
  const p = useDocProgress();
  const signedIn = useSignedIn();
  const [past, setPast] = useState(false);

  useEffect(() => {
    const onScroll = () => setPast(window.scrollY > window.innerHeight * 1.5);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <>
      <div className="fixed inset-x-0 top-0 z-50 h-px" style={{ background: "transparent" }}>
        <div
          className="h-px origin-left"
          style={{
            background: "var(--l-rust)",
            transform: `scaleX(${p})`,
            transition: "transform .12s linear",
          }}
        />
      </div>

      <header
        className="fixed inset-x-0 top-0 z-40 px-6 md:px-10"
        style={{
          transform: past ? "translateY(0)" : "translateY(-12px)",
          opacity: past ? 1 : 0,
          pointerEvents: past ? "auto" : "none",
          transition: "opacity .6s cubic-bezier(.16,1,.3,1), transform .6s cubic-bezier(.16,1,.3,1)",
        }}
      >
        <div
          className="mx-auto mt-4 flex max-w-6xl items-center justify-between rounded-full px-5 py-2.5 backdrop-blur-xl"
          style={{
            background: "rgba(255,253,248,.78)",
            border: "1px solid var(--l-rule)",
            boxShadow: "0 10px 40px -24px rgba(28,25,23,.5)",
          }}
        >
          <Link
            href="/"
            className="text-[22px] leading-none italic"
            style={{ fontFamily: "var(--font-display)", color: "var(--l-ink)" }}
          >
            photon
          </Link>
          <nav className="hidden items-center gap-7 md:flex">
            {LINKS.map((l) => (
              <a
                key={l.href}
                href={l.href}
                className="text-[12px] tracking-[0.14em] uppercase transition-colors"
                style={{ color: "var(--l-muted)" }}
                onMouseEnter={(e) => (e.currentTarget.style.color = "var(--l-ink)")}
                onMouseLeave={(e) => (e.currentTarget.style.color = "var(--l-muted)")}
              >
                {l.label}
              </a>
            ))}
          </nav>
          <Link
            href={signedIn ? "/home" : "/login"}
            className="rounded-full px-4 py-1.5 text-[12px] tracking-[0.14em] uppercase transition-transform hover:-translate-y-px"
            style={{ background: "var(--l-ink)", color: "var(--l-paper)" }}
          >
            {signedIn ? "Dashboard" : "Sign in"}
          </Link>
        </div>
      </header>
    </>
  );
}
