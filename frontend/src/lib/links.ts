// Day 6 (docs/day6-design.md section 8.5): the project's links on the landing page, set when the
// UI is built (NEXT_PUBLIC_REPO_URL, NEXT_PUBLIC_VIDEO_URL; the video comes on Day 7). Only an
// https address becomes a link.
function https(value: string | undefined): string {
  return value && /^https:\/\/[^\s"'<>]+$/.test(value) ? value : "";
}

export const REPO_URL = https(process.env.NEXT_PUBLIC_REPO_URL);
export const VIDEO_URL = https(process.env.NEXT_PUBLIC_VIDEO_URL);
