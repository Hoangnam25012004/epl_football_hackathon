// The landing page's status line in plain words (docs/day7-design.md section 2.9): which model
// answers and where it runs, what writes the studio lines, how claims are answered, which voice
// reads. Never "mock": the development stand-in is called what it is.
import type { Health } from "@/lib/contracts";
import type { Line } from "@/lib/claim-source";

export function serverStatus(health: Health): Line[] {
  const lines: Line[] = [];
  const { llm_provider: provider, model } = health;
  if (provider === "mock") lines.push({ key: "landing.modelMock", tone: "accent" });
  else if (provider === "ollama")
    lines.push({ key: "landing.modelCpu", vars: { model }, tone: "accent" });
  else lines.push({ key: "landing.modelHosted", vars: { model, provider }, tone: "accent" });
  if (health.llm_live_scope === "claims") lines.push({ key: "landing.studioTemplates" });
  if (health.claim_progressive) lines.push({ key: "landing.progressive" });
  const voice = health.speech?.provider;
  if (voice === "browser") lines.push({ key: "landing.voiceBrowser" });
  else if (voice === "azure") lines.push({ key: "landing.voiceAzure" });
  else if (voice === "piper") lines.push({ key: "landing.voicePiper" });
  else if (voice === "mock") lines.push({ key: "landing.voiceMock" });
  return lines;
}
