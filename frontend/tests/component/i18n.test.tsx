// Day 6: the interface language of a page prerendered at build time (the landing page). It
// hydrates in the build's language with no mismatch, then shows the viewer's (stored, or the
// browser's): rendering the viewer's at once gave React error 418 for a viewer who chose VI.
import { act } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { browserLang, I18nProvider, type LangDefault, useT } from "@/lib/i18n";

function Title() {
  const { t } = useT();
  return <h1>{t("landing.title")}</h1>;
}

/** Prerender as the build does (no storage, no browser), then hydrate in a browser where the
 * viewer stored `stored` and prefers `languages`. */
async function hydrate(
  fallback: LangDefault,
  { stored, languages }: { stored?: string; languages?: string[] } = {},
): Promise<{ text: string; errors: unknown[] }> {
  const tree = (
    <I18nProvider fallback={fallback}>
      <Title />
    </I18nProvider>
  );
  window.localStorage.clear();
  const container = document.createElement("div");
  container.innerHTML = renderToString(tree); // what the build wrote
  document.body.append(container);
  if (stored) window.localStorage.setItem("pundit.ui-lang", stored);
  if (languages) vi.spyOn(window.navigator, "languages", "get").mockReturnValue(languages);
  const errors: unknown[] = [];
  await act(async () => {
    hydrateRoot(container, tree, { onRecoverableError: (error) => errors.push(error) });
  });
  const text = container.textContent ?? "";
  container.remove();
  return { text, errors };
}

describe("interface language of a prerendered page", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("the build is written in the page default", () => {
    window.localStorage.setItem("pundit.ui-lang", "vi");
    const html = renderToString(
      <I18nProvider fallback="en">
        <Title />
      </I18nProvider>,
    );
    expect(html).toContain("Choose a match");
  });

  it("hydrates without a mismatch, then shows the stored language", async () => {
    const { text, errors } = await hydrate("en", { stored: "vi" });
    expect(errors).toEqual([]);
    expect(text).toBe("Chọn trận đấu");
  });

  it("a browser in Vietnamese gets the landing page in Vietnamese", async () => {
    const { text, errors } = await hydrate("browser", { languages: ["vi-VN", "en-US"] });
    expect(errors).toEqual([]);
    expect(text).toBe("Chọn trận đấu");
  });

  it("a stored choice beats the browser", async () => {
    const { text } = await hydrate("browser", { stored: "en", languages: ["vi-VN"] });
    expect(text).toBe("Choose a match");
  });

  it("the browser's first language that is Vietnamese or English", () => {
    expect(browserLang(["vi-VN", "en"])).toBe("vi");
    expect(browserLang(["en-GB", "vi"])).toBe("en");
    expect(browserLang(["fr-FR", "vi"])).toBe("vi");
    expect(browserLang(["fr-FR"])).toBe("en");
    expect(browserLang([])).toBe("en");
  });
});
