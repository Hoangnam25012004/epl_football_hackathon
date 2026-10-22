"use client";

// A 1920x1080 stage scaled to its container; the lower-third sits bottom-left inside the 5 % safe
// area (docs/day4-design.md section 6.1). Exits play their animation before the item goes away.
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { LowerThirdView } from "@/lib/contracts";
import { LowerThird, type Phase } from "./LowerThird";

export const STAGE_WIDTH = 1920;
export const STAGE_HEIGHT = 1080;
const EXIT_MS = 300;

/** The item to draw and its phase: a removed or replaced item first plays its exit, then the
 * new one enters. */
export function useAnimatedItem(current: LowerThirdView | null): {
  item: LowerThirdView | null;
  phase: Phase;
} {
  const [previous, setPrevious] = useState<LowerThirdView | null>(current);
  const [leaving, setLeaving] = useState<LowerThirdView | null>(null);
  if ((current?.id ?? null) !== (previous?.id ?? null)) {
    // adjusting state when a prop changes (react.dev): the old item starts its exit
    setPrevious(current);
    if (previous) setLeaving(previous);
  }
  useEffect(() => {
    if (!leaving) return;
    const timer = setTimeout(() => setLeaving(null), EXIT_MS);
    return () => clearTimeout(timer);
  }, [leaving]);
  if (leaving) return { item: leaving, phase: "exit" };
  return { item: current, phase: current ? "enter" : "static" };
}

export function Stage({
  current,
  debug = false,
  children,
}: {
  current: LowerThirdView | null;
  debug?: boolean;
  children?: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0);
  const { item, phase } = useAnimatedItem(current);

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const measure = () =>
      setScale(Math.min(element.clientWidth / STAGE_WIDTH, element.clientHeight / STAGE_HEIGHT));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={box} className="relative h-full w-full overflow-hidden" data-testid="stage">
      <div
        className="absolute left-0 top-0 origin-top-left"
        style={{
          width: STAGE_WIDTH,
          height: STAGE_HEIGHT,
          transform: `scale(${scale})`,
          // checkerboard: shows that everything outside the lower-third is transparent
          background: debug
            ? "repeating-conic-gradient(#8885 0% 25%, transparent 0% 50%) 50% / 40px 40px"
            : undefined,
        }}
      >
        {debug ? (
          <div className="pointer-events-none absolute inset-[5%] border-2 border-dashed border-fuchsia-500/70" />
        ) : null}
        <div className="absolute bottom-[54px] left-[96px]">
          {item ? <LowerThird key={item.id} item={item} phase={phase} /> : null}
        </div>
        {children}
      </div>
    </div>
  );
}
