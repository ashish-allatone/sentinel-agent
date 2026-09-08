/**
 * useCopyFlash — click-to-copy with a short acknowledgement on the row clicked.
 *
 * The SOC2 report shows the same long values (file paths, mount points, command
 * lines) in tables, breakdown lists and event feeds, and every one of them is
 * worth copying. This keeps that behaviour in one place: copy the value, then
 * mark the row so the caller can flash on it.
 *
 * The result carries whether the copy actually worked. A copy can fail — no
 * clipboard API outside a secure context, a browser that refuses the legacy
 * path — and a click that silently does nothing is indistinguishable from a
 * broken feature, so callers show the failure rather than swallowing it.
 */
import { useEffect, useRef, useState } from "react";

import { copyText } from "../utils/clipboard";

/**
 * @param {number} [resetMs] how long the acknowledgement stays up
 * @returns {[{id: string, ok: boolean}, (id: string, value: string) => Promise<void>]}
 */
export default function useCopyFlash(resetMs = 1600) {
  const [flash, setFlash] = useState({ id: "", ok: false });
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async (id, value) => {
    const ok = await copyText(value);
    setFlash({ id, ok });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setFlash({ id: "", ok: false }), resetMs);
  };

  return [flash, copy];
}
