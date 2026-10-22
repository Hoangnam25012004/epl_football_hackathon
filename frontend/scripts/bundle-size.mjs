// JavaScript a page loads first, gzipped (docs/day4-design.md section 12 F7): every <script src>
// of the exported HTML except the `noModule` polyfills modern browsers never fetch.
// `node scripts/bundle-size.mjs [out]`; exits 1 over a budget. React DOM + the Next.js runtime
// alone weigh about 130 KB, so the overlay budget is 160 KB (the design's 120 KB is below that
// floor; see the as-built section).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const root = process.argv[2] ?? "out";
const KB = 1024;
const BUDGETS = {
  overlay: 160 * KB,
  studio: 300 * KB,
  fan: 300 * KB,
  compare: 300 * KB,
  "": 300 * KB, // landing
};

let failed = false;
for (const [page, budget] of Object.entries(BUDGETS)) {
  const html = readFileSync(join(root, page, "index.html"), "utf8");
  const scripts = [
    ...new Set(
      [...html.matchAll(/<script src="([^"]+\.js)"([^>]*)>/g)]
        .filter((m) => !/noModule/i.test(m[2]))
        .map((m) => m[1]),
    ),
  ];
  const size = scripts.reduce(
    (sum, src) => sum + gzipSync(readFileSync(join(root, src.replace(/^\//, "")))).length,
    0,
  );
  const ok = size <= budget;
  failed ||= !ok;
  console.log(
    `${(page || "landing").padEnd(8)} ${(size / KB).toFixed(1).padStart(6)} KB gzip ` +
      `(${scripts.length} scripts, budget ${budget / KB} KB) ${ok ? "ok" : "OVER BUDGET"}`,
  );
}
process.exit(failed ? 1 : 0);
