// The `mock` E2E project has no backend: REST is answered from recorded responses
// (tests/fixtures/rest: GET /scenarios and GET /matches/dominant_loser-42/players) and the
// WebSocket from recorded streams (docs/day4-design.md section 11.1). Every path the pages call
// has an answer, so any console error is a bug.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, type Page, type WebSocketRoute } from "@playwright/test";
import type { AnyWsMessage } from "@/lib/ws-types";
import { fixture } from "../fixtures";

export const MATCH = "dominant_loser-42";
export const analyst = fixture("dominant_loser-42.analyst-en");
export const casual = fixture("dominant_loser-42.casual-vi-beginner-har");
export const lateHello = fixture("dominant_loser-42.late-hello.analyst-en");
type Hello = Extract<AnyWsMessage, { type: "hello" }>;
export const helloOf = (stream: readonly AnyWsMessage[]) => stream[0] as Hello;

// fixture B has no hello (its messages start at n = 1): the kick-off hello of fixture A goes first
export const longAnalyst = [helloOf(analyst), ...fixture("dominant_loser-42.llm-long.analyst-en")];
export const longCasual = [
  helloOf(casual),
  ...fixture("dominant_loser-42.llm-long.casual-vi-beginner-har"),
];

const hello = helloOf(analyst);
const info = hello.payload.match;
const REST = join(__dirname, "..", "fixtures", "rest");
const read = (name: string): unknown => JSON.parse(readFileSync(join(REST, name), "utf8"));

/** Answers every REST call of the pages; returns the calls made. */
export async function mockRest(
  page: Page,
  { onCreate }: { onCreate?: () => void } = {},
): Promise<string[]> {
  const calls: string[] = [];
  const home = info.home.id;
  const away = info.away.id;
  await page.route(
    (url) => /^\/(healthz|scenarios|matches|claims)(\/|$)/.test(url.pathname),
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      calls.push(`${request.method()} ${path}${url.search}`);
      const reply = (body: unknown, status = 200) => route.fulfill({ status, json: body });
      if (path === "/healthz") return reply({ status: "ok", synthetic: true });
      if (path === "/scenarios") return reply(read("scenarios.json"));
      if (path === "/matches") {
        if (request.method() !== "POST") return reply([info]);
        onCreate?.();
        return reply({ ...info, status: "created" });
      }
      if (!path.startsWith(`/matches/${MATCH}`)) return reply({ detail: "not found" }, 404);
      switch (path.slice(`/matches/${MATCH}`.length)) {
        case "":
          return reply({ info, snapshot: hello.payload.snapshot });
        case "/players":
          return reply(read("players.dominant_loser-42.json"));
        case "/timeline":
          return reply({
            as_of_seq: 0,
            home,
            away,
            half_time_minute: null,
            markers: [],
            points: [],
            window_minutes: 5,
          });
        case "/stats":
          return reply({
            as_of_seq: 0,
            period: url.searchParams.get("period") ?? "FULL",
            home,
            away,
            rows: [],
          });
        case "/events":
          return reply({ as_of_seq: 0, events: [], missing: [] });
        case "/lower-thirds":
        case "/commentary":
          return reply([]);
        case "/on-air":
          return reply(hello.payload.on_air);
        default:
          return reply(info); // start / pause / resume / stop / speed
      }
    },
  );
  return calls;
}

export type Connection = { ws: WebSocketRoute; query: URLSearchParams; index: number };

/** Routes the match socket; `serve` decides what each new connection gets. */
export async function mockSocket(page: Page, serve: (connection: Connection) => void) {
  let index = 0;
  await page.routeWebSocket(/\/ws\/match\//, (ws) => {
    serve({ ws, query: new URL(ws.url()).searchParams, index: index++ });
  });
}

export function play(ws: WebSocketRoute, messages: readonly AnyWsMessage[]): void {
  for (const message of messages) ws.send(JSON.stringify(message));
}

/** Console errors and uncaught page errors of a page. */
export function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(String(error)));
  return errors;
}

/** The page never scrolls sideways (long texts wrap or truncate inside their boxes). */
export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}
