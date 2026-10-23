"use client";

// Every page: Suspense (static export + useSearchParams), the interface language, toasts and the
// synthetic-data badge (principle U5). The badge is part of the shell, so no page can lose it.
import { Suspense, useEffect, type ReactNode } from "react";
import { I18nProvider, type LangDefault } from "@/lib/i18n";
import { SyntheticFallback } from "./SyntheticBadge";
import { ToastHost } from "./Toast";

export function PageShell({
  defaultLang,
  theme = "auto",
  transparent = false,
  children,
}: {
  defaultLang: LangDefault;
  theme?: "auto" | "studio";
  transparent?: boolean;
  children: ReactNode;
}) {
  return (
    <Suspense fallback={transparent ? null : <SyntheticFallback />}>
      <ThemeClass theme={theme} transparent={transparent} />
      <I18nProvider fallback={defaultLang}>
        <ToastHost>{children}</ToastHost>
      </I18nProvider>
    </Suspense>
  );
}

function ThemeClass({ theme, transparent }: { theme: "auto" | "studio"; transparent: boolean }) {
  useEffect(() => {
    const html = document.documentElement;
    if (transparent) html.classList.add("overlay-page");
    if (theme === "studio") document.body.classList.add("theme-studio");
    return () => {
      html.classList.remove("overlay-page");
      document.body.classList.remove("theme-studio");
    };
  }, [theme, transparent]);
  return null;
}
