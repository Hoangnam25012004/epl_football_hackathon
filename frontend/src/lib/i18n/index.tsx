"use client";

// Interface language (docs/day4-design.md section 4.8): `?ui=` > localStorage > the page default.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { en, type Key } from "./en";
import { vi } from "./vi";

export type UiLang = "en" | "vi";
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

export function initialUiLang(fallback: UiLang): UiLang {
  if (typeof window === "undefined") return fallback;
  const fromQuery = new URLSearchParams(window.location.search).get("ui");
  if (fromQuery === "en" || fromQuery === "vi") return fromQuery;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "vi") return stored;
  } catch {
    /* storage blocked */
  }
  return fallback;
}

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

export function I18nProvider({ fallback, children }: { fallback: UiLang; children: ReactNode }) {
  const [lang, setLangState] = useState<UiLang>(() => initialUiLang(fallback));
  const setLang = useCallback((next: UiLang) => {
    setLangState(next);
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
