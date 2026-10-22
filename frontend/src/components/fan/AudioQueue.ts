// The voice of the fan player, for Day 5 (docs/day4-design.md section 7.5): the interface only.
// Day 5 adds `audio_url` to CommentaryView and plays the queue with Azure AI Speech audio.
import type { CommentaryView } from "@/lib/contracts";

export const MAX_WAITING = 3;

export type AudioQueue = {
  /** Queue an item the server marked `speak`; the most exciting wait least. */
  enqueue: (item: CommentaryView) => void;
  clear: () => void;
};

/** Which items to keep when the queue is too long: the most exciting, then the newest. */
export function keepForAudio(waiting: readonly CommentaryView[]): CommentaryView[] {
  return [...waiting]
    .filter((item) => item.speak)
    .sort((a, b) => (b.excitement ?? 0) - (a.excitement ?? 0) || b.clock_ms - a.clock_ms)
    .slice(0, MAX_WAITING)
    .sort((a, b) => a.clock_ms - b.clock_ms);
}

/** Day 4: nothing is played (no audio yet, nothing promised in the interface). */
export const silentQueue: AudioQueue = { enqueue: () => {}, clear: () => {} };
