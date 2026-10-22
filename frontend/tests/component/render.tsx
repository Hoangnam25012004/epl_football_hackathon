// Render a component with the providers every page has (interface language, toasts); a
// `rerender` keeps them.
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { ToastHost } from "@/components/common/Toast";
import { I18nProvider, type UiLang } from "@/lib/i18n";

export function renderWith(ui: ReactElement, lang: UiLang = "en"): RenderResult {
  try {
    window.localStorage.setItem("pundit.ui-lang", lang);
  } catch {
    /* jsdom without storage */
  }
  const Providers = ({ children }: { children: ReactNode }) => (
    <I18nProvider fallback={lang}>
      <ToastHost>{children}</ToastHost>
    </I18nProvider>
  );
  return render(ui, { wrapper: Providers });
}

/** A fetch that answers JSON per URL substring; unknown URLs get 404. */
export function fakeFetch(routes: Record<string, { status?: number; body: unknown }>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const impl = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const key = Object.keys(routes).find((k) => url.includes(k));
    const route = key ? routes[key] : { status: 404, body: { detail: "not found" } };
    return new Response(JSON.stringify(route?.body ?? null), { status: route?.status ?? 200 });
  };
  return { impl, calls };
}
