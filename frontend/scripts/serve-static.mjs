// A tiny static server for the `mock` E2E project: serves the static export with no backend
// (the tests route REST and WebSocket themselves). `node scripts/serve-static.mjs out 8766`
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const root = resolve(process.argv[2] ?? "out");
const port = Number(process.argv[3] ?? 8766);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
  ".json": "application/json",
  ".ico": "image/x-icon",
};

createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://localhost");
  let path = normalize(join(root, decodeURIComponent(url.pathname)));
  if (!path.startsWith(root)) {
    response.writeHead(403).end();
    return;
  }
  if (existsSync(path) && statSync(path).isDirectory()) path = join(path, "index.html");
  if (!existsSync(path)) {
    response.writeHead(404, { "Content-Type": "text/plain" }).end("not found");
    return;
  }
  response.writeHead(200, { "Content-Type": TYPES[extname(path)] ?? "application/octet-stream" });
  createReadStream(path).pipe(response);
}).listen(port, "127.0.0.1", () => console.log(`static ${root} on http://127.0.0.1:${port}`));
