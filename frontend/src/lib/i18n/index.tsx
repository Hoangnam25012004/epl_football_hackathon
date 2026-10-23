"use client";

// Interface language (docs/day4-design.md section 4.8): `?ui=` > localStorage > the page default
// (Day 6: or the browser's language, for the landing page).
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { en, type Key } from "./en";
import { vi } from "./vi";

export type UiLang = "en" | "vi";
/** A page's default language: a fixed one, or the browser's (Vietnamese or English). */
export type LangDefault = UiLang | "browser";
export type Vars = Record<string, string | number>;

const DICTS: Record<UiLang, Record<Key, string>> = { en, vi };
const STORAGE_KEY = "pundit.ui-lang";

export function translate(lang: UiLang, key: Key, vars?: Vars): string {
  const template = DICTS[lang][key] ?? en[key] ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  );
}

export function hasKey(key: string): key is Key {
  return key in en;
}

/** "goal" → "Goal" / "Bàn thắng"; a kind without a word ("stat_tick") is shown readably. */
export function kindLabel(lang: UiLang, kind: string): string {
  for (const prefix of ["moment", "cue", "event"] as const) {
    const key = `${prefix}.${kind}`;
    if (hasKey(key)) return translate(lang, key);
  }
  return kind.replace(/_/g, " ");
}

/** The first of the browser's languages that is Vietnamese or English; English otherwise. */
export function browserLang(languages: readonly string[]): UiLang {
  for (const language of languages) {
    const code = language.toLowerCase();
    if (code.startsWith("vi")) return "vi";
    if (code.startsWith("en")) return "en";
  }
  return "en";
}

/** What a page prerendered at build time was written in: the server knows no browser. */
export function serverUiLang(fallback: LangDefault): UiLang {
  return fallback === "browser" ? "en" : fallback;
}

export function initialUiLang(fallback: LangDefault): UiLang {
  if (typeof window === "undefined") return serverUiLang(fallback);
  const fromQuery = new URLSearchParams(window.location.search).get("ui");
  if (fromQuery === "en" || fromQuery === "vi") return fromQuery;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "vi") return stored;
  } catch {
    /* storage blocked */
  }
  if (fallback !== "browser") return fallback;
  return browserLang(
    window.navigator.languages?.length
      ? window.navigator.languages
      : [window.navigator.language ?? ""],
  );
}

const noSubscription = () => () => {};

type I18n = {
  lang: UiLang;
  setLang: (lang: UiLang) => void;
  t: (key: Key, vars?: Vars) => string;
};

const I18nContext = createContext<I18n>({
  lang: "en",
  setLang: () => {},
  t: (key, vars) => translate("en", key, vars),
});

export function I18nProvider({
  fallback,
  children,
}: {
  fallback: LangDefault;
  children: ReactNode;
}) {
  // A page prerendered at build time (the landing page) hydrates in its default language, then
  // switches to the stored or browser one: rendering that one at once would not match the HTML
  // (React error 418). A page rendered on the client starts in the right language.
  const initial = useSyncExternalStore(
    noSubscription,
    () => initialUiLang(fallback),
    () => serverUiLang(fallback),
  );
  const [chosen, setChosen] = useState<UiLang | null>(null);
  const lang = chosen ?? initial;
  const setLang = useCallback((next: UiLang) => {
    setChosen(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage blocked: the choice lasts for this page */
    }
  }, []);
  const value = useMemo<I18n>(
    () => ({ lang, setLang, t: (key, vars) => translate(lang, key, vars) }),
    [lang, setLang],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useT(): I18n {
  return useContext(I18nContext);
}

export type { Key };
