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
  "dominant_loser-42.casual-vi-beginner-har",
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

describe("fixtures follow the WebSocket contract", () => {
  const validators = new Map<string, ReturnType<typeof validatorFor>>();
  for (const name of NAMES) {
    it(name, () => {
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
        expect(ok, `${message.type} n=${message.n}: ${JSON.stringify(validate.errors?.[0])}`).toBe(
          true,
        );
      }
    });
  }
});
