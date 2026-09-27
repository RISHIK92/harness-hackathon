"use client";

import { useEffect, useState } from "react";

/** Index that advances every `ms`. Shared by the watermark, the hero ticker
 *  and the login greeting — all of which are the same idea at different sizes. */
export function useCycle(length: number, ms: number) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % length), ms);
    return () => clearInterval(t);
  }, [length, ms]);
  return i;
}
