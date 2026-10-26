// Every recorded message validates against the exported JSON Schema (docs/day4-design.md 11.2):
// a fixture that drifted from the backend contract fails here.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { fixture, type FixtureName } from "../fixtures";

const schemaDir = join(__dirname, "..", "..", "..", "packages", "schemas");
const payloads = JSON.parse(readFileSync(join(schemaDir, "ws_payloads.schema.json"), "utf8"));

function validatorFor(type: string) {
  const ajv = new Ajv2020({ strict: false, allErrors: false });
  addFormats(ajv);
  return ajv.compile({ $defs: payloads.$defs, ...payloads.properties[type] });
}

const NAMES: FixtureName[] = [
  "dominant_loser-42.analyst-en",
  "dominant_loser-42.analyst-vi",
  "dominant_loser-42.casual-vi-beginner-har",
  "dominant_loser-42.casual-vi-beginner-har-female",
  "dominant_loser-42.late-hello.analyst-en",
  "dominant_loser-42.llm-long.analyst-en",
  "dominant_loser-42.llm-long.casual-vi-beginner-har",
];

describe("recorded REST answers of the mock E2E follow their schema", () => {
  const cases = [
    ["scenarios.json", "scenario_info.schema.json"],
    ["players.dominant_loser-42.json", "player_brief.schema.json"],
  ] as const;
  for (const [file, schemaFile] of cases) {
    it(file, () => {
      const ajv = new Ajv2020({ strict: false });
      addFormats(ajv);
      const schema = JSON.parse(readFileSync(join(schemaDir, schemaFile), "utf8"));
      const validate = ajv.compile(schema);
      const body = JSON.parse(
        readFileSync(join(__dirname, "..", "fixtures", "rest", file), "utf8"),
      );
      expect(Array.isArray(body) && body.length > 0).toBe(true);
      for (const item of body)
        expect(validate(item), JSON.stringify(validate.errors?.[0])).toBe(true);
    });
  }
});

describe("Day 6: the recorded panels of the screenshot set follow their schema", () => {
  const cases = [
    ["timeline.dominant_loser-42.json", "timeline.schema.json"],
    ["stats.dominant_loser-42.FULL.json", "stats_panel.schema.json"],
    ["stats.dominant_loser-42.H1.json", "stats_panel.schema.json"],
    ["stats.dominant_loser-42.H2.json", "stats_panel.schema.json"],
  ] as const;
  for (const [file, schemaFile] of cases) {
    it(file, () => {
      const ajv = new Ajv2020({ strict: false });
      addFormats(ajv);
      const schema = JSON.parse(readFileSync(join(schemaDir, schemaFile), "utf8"));
      const validate = ajv.compile(schema);
      const body = JSON.parse(
        readFileSync(join(__dirname, "..", "fixtures", "rest", file), "utf8"),
      );
      expect(validate(body), JSON.stringify(validate.errors?.[0])).toBe(true);
    });
  }
});

describe("fixtures follow the WebSocket contract", () => {
  const validators = new Map<string, ReturnType<typeof validatorFor>>();
  for (const name of NAMES) {
    it(
      name,
      () => {
        const messages = fixture(name);
        expect(messages.length).toBeGreaterThan(0);
        for (const message of messages) {
          expect(message.v).toBe(1);
          let validate = validators.get(message.type);
          if (!validate) {
            validate = validatorFor(message.type);
            validators.set(message.type, validate);
          }
          const ok = validate(message.payload);
          expect(
            ok,
            `${message.type} n=${message.n}: ${JSON.stringify(validate.errors?.[0])}`,
          ).toBe(true);
        }
        // the first one also compiles the validators of every message type it meets
      },
      20_000,
    );
  }
});

describe("Day 5: the voice fixture", () => {
  it("speaks the lines marked `speak`, only those, with a clip of the server", () => {
    const messages = fixture("dominant_loser-42.casual-vi-beginner-har-female");
    const commentary = messages.flatMap((m) => (m.type === "commentary" ? [m.payload] : []));
    const spoken = commentary.filter((view) => view.speech);
    expect(spoken.length).toBeGreaterThan(10);
    for (const view of commentary) expect(Boolean(view.speech), view.id).toBe(Boolean(view.speak));
    for (const view of spoken) {
      expect(view.speech?.audio?.url).toMatch(/^\/audio\/[0-9a-f]{32}\.wav$/);
      expect(view.speech?.voice).toBe("vi-VN-HoaiMyNeural");
      // no digit is ever read as a digit (docs/day5-design.md section 5)
      for (const segment of view.speech?.segments ?? []) expect(segment.text).not.toMatch(/\d/);
    }
    const hello = messages[0];
    expect(hello?.type).toBe("hello");
    if (hello?.type === "hello") {
      const shown = hello.payload.commentary ?? [];
      expect(shown.every((view) => !view.speech)).toBe(true); // hello is silent
    }
    const recaps = messages.flatMap((m) => (m.type === "recap" ? [m.payload] : []));
    expect(new Set(recaps.map((r) => r.scope))).toEqual(new Set(["H1", "FULL"]));
    for (const recap of recaps) {
      expect(recap.speech?.audio?.duration_ms).toBeGreaterThan(30_000);
      expect(recap.speech?.audio?.duration_ms).toBeLessThanOrEqual(120_000);
    }
  });

  it("profiles without a voice never receive speech", () => {
    for (const name of [
      "dominant_loser-42.analyst-en",
      "dominant_loser-42.casual-vi-beginner-har",
    ] as const) {
      for (const message of fixture(name)) {
        if (message.type === "commentary" || message.type === "recap") {
          expect(message.payload.speech ?? null).toBeNull();
        }
      }
    }
  });
});
