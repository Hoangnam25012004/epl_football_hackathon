// Messages that arrive between two frames are applied together: at 1000x the store changes at
// most once per frame (docs/day4-design.md section 4.5). Latencies (receive → apply) are kept
// for the debug panel and the performance tests.
export type Schedule = (callback: () => void) => void;

const MAX_SAMPLES = 500;

export const animationFrame: Schedule = (callback) => {
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => callback());
  else setTimeout(callback, 16);
};

export class FrameBatcher<T> {
  private queue: { item: T; at: number }[] = [];
  private scheduled = false;
  readonly latencies: number[] = [];

  constructor(
    private readonly flush: (items: T[]) => void,
    private readonly schedule: Schedule = animationFrame,
    private readonly now: () => number = () => performance.now(),
  ) {}

  push(item: T): void {
    this.queue.push({ item, at: this.now() });
    if (!this.scheduled) {
      this.scheduled = true;
      this.schedule(() => this.flushNow());
    }
  }

  flushNow(): void {
    this.scheduled = false;
    if (this.queue.length === 0) return;
    const batch = this.queue;
    this.queue = [];
    this.flush(batch.map((entry) => entry.item));
    const done = this.now();
    for (const entry of batch) {
      this.latencies.push(done - entry.at);
      if (this.latencies.length > MAX_SAMPLES) this.latencies.shift();
    }
  }

  /** Drop what has not been applied yet (a new connection replaces the state). */
  clear(): void {
    this.queue = [];
  }
}
