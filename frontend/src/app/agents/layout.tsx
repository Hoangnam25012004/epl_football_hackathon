import type { Metadata } from "next";

// The tab's title (docs/day7-design.md section 2.5): "Agent Ops · Pundit Copilot"
export const metadata: Metadata = { title: "Agent Ops" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
