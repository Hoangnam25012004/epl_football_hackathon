import type { Metadata } from "next";

// The tab's title (docs/day6-design.md section 8.5): "Overlay · Pundit Copilot"
export const metadata: Metadata = { title: "Overlay" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
