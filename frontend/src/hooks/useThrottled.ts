"use client";

import { useEffect, useRef, useState } from "react";

/** `value`, updated at most once every `ms` (and always with the latest value at the end).
 * A debounce would never fire while snapshots keep arriving at a high replay speed. */
export function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const last = useRef(0);
  useEffect(() => {
    const wait = Math.max(0, last.current + ms - Date.now());
    const timer = setTimeout(() => {
      last.current = Date.now();
      setShown(value);
    }, wait);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return shown;
}
