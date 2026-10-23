import type { Metadata } from "next";

// The tab's title (docs/day6-design.md section 8.5): "Analyst vs Casual · Pundit Copilot"
export const metadata: Metadata = { title: "Analyst vs Casual" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
