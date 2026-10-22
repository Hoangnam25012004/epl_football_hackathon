// Speak a claim (P2, docs/day5-design.md section 11): the microphone fills the claim box and
// never sends it; it is there only when the server has speech to text and the page can record.
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ClaimBox } from "@/components/studio/ClaimBox";
import { fakeFetch, renderWith } from "./render";

const recorder = vi.hoisted(() => ({
  starts: 0,
  stops: 0,
  cancels: 0,
  failWith: null as string | null,
  opening: null as Promise<void> | null, // set: start waits for it (the microphone warming up)
}));

vi.mock("@/lib/audio/recorder", () => {
  class RecorderError extends Error {
    constructor(readonly problem: string) {
      super(problem);
    }
  }
  class Recorder {
    async start() {
      recorder.starts += 1;
      if (recorder.opening) await recorder.opening;
      if (recorder.failWith) throw new RecorderError(recorder.failWith);
    }
    stop() {
      recorder.stops += 1;
      return { wav: new Uint8Array(44 + 32_000), durationMs: 1000 };
    }
    cancel() {
      recorder.cancels += 1;
    }
  }
  return { Recorder, RecorderError };
});

const HEALTH = { status: "ok", synthetic: true, speech: { provider: "mock", stt: "mock" } };

function mount(answer: { status?: number; body: unknown }, stt = "mock") {
  const fetch = fakeFetch({
    "/healthz": { body: { ...HEALTH, speech: { ...HEALTH.speech, stt } } },
    "/speech/recognize": answer,
    "/claims/verify": { body: {} },
  });
  vi.stubGlobal("fetch", fetch.impl);
  const view = renderWith(
    <ClaimBox matchId="dominant_loser-42" examples={[]} history={[]} onDetails={() => {}} />,
    "en",
  );
  return { ...fetch, unmount: view.unmount };
}

beforeEach(() => {
  Object.assign(recorder, { starts: 0, stops: 0, cancels: 0, failWith: null, opening: null });
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("AudioWorkletNode", class {});
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia: vi.fn() },
    configurable: true,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "mediaDevices");
});

const ok = { body: { text: "The home side dominated the second half", language: "en",
  status: "ok", confidence: 0.99, duration_ms: 1000, provider: "mock" } }; // prettier-ignore

describe("MicButton in the claim box", () => {
  it("records, stops, fills the box with the text and sends nothing", async () => {
    const fetch = mount(ok);
    const mic = await screen.findByTestId("mic-button");
    expect(mic).toHaveAttribute("aria-label", "Speak a claim");
    fireEvent.click(mic);
    await waitFor(() => expect(mic).toHaveAttribute("data-phase", "recording"));
    expect(mic).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("mic-status")).toHaveTextContent("Recording 0 s · Esc to cancel");
    await waitFor(() => expect(recorder.starts).toBe(1));
    fireEvent.click(mic);
    await waitFor(() =>
      expect(screen.getByTestId("claim-input")).toHaveValue(
        "The home side dominated the second half",
      ),
    );
    const posted = fetch.calls.find((call) => call.url.includes("/speech/recognize"));
    expect(posted?.url).toContain("/speech/recognize?lang=en");
    expect(posted?.init?.method).toBe("POST");
    expect(new Headers(posted?.init?.headers).get("Content-Type")).toBe("audio/wav");
    expect(posted?.init?.body).toBeInstanceOf(Blob);
    expect(fetch.calls.some((call) => call.url.includes("/claims/verify"))).toBe(false);
    expect(await screen.findByText("Check the text, then press Enter to verify it")).toBeVisible();
    expect(screen.getByTestId("claim-input")).toHaveFocus();
    expect(mic).toHaveAttribute("data-phase", "idle");
  });

  it("nothing understood, busy, unavailable: a toast, and the box keeps what was typed", async () => {
    for (const [answer, message] of [
      [{ body: { ...ok.body, text: "", status: "no_match" } }, "Nothing understood: try again"],
      [
        { status: 429, body: { detail: "stt_busy" } },
        "Speech recognition is busy: try again in a moment",
      ],
      [
        { status: 503, body: { detail: "stt_unavailable" } },
        "Speech recognition is not available right now",
      ],
    ] as const) {
      const { unmount } = mount(answer);
      fireEvent.change(screen.getByTestId("claim-input"), { target: { value: "typed" } });
      const mic = await screen.findByTestId("mic-button");
      fireEvent.click(mic);
      await waitFor(() => expect(recorder.starts).toBeGreaterThan(0));
      await waitFor(() => expect(mic).toHaveAttribute("data-phase", "recording"));
      fireEvent.click(mic);
      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(screen.getByTestId("claim-input")).toHaveValue("typed");
      unmount();
      recorder.starts = 0;
    }
  });

  it("says 'opening' until the microphone delivers sound; Esc or leaving then closes it", async () => {
    let open = () => {};
    recorder.opening = new Promise<void>((resolve) => (open = resolve));
    const first = mount(ok);
    const mic = await screen.findByTestId("mic-button");
    fireEvent.click(mic);
    await waitFor(() => expect(mic).toHaveAttribute("data-phase", "starting"));
    expect(mic).toBeDisabled(); // not "recording" yet: words said now would be lost
    expect(screen.getByTestId("mic-status")).toHaveTextContent("Opening the microphone…");
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(mic).toHaveAttribute("data-phase", "idle");
    await act(async () => open());
    expect(recorder.cancels).toBe(1); // opened after Esc: closed at once
    expect(mic).toHaveAttribute("data-phase", "idle");

    recorder.opening = new Promise<void>((resolve) => (open = resolve));
    fireEvent.click(mic);
    await waitFor(() => expect(mic).toHaveAttribute("data-phase", "starting"));
    first.unmount();
    await act(async () => open());
    expect(recorder.cancels).toBe(2); // the page left while it opened: closed too
  });

  it("Esc cancels: no request", async () => {
    const fetch = mount(ok);
    const mic = await screen.findByTestId("mic-button");
    fireEvent.click(mic);
    await waitFor(() => expect(recorder.starts).toBe(1));
    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(mic).toHaveAttribute("data-phase", "idle");
    expect(recorder.cancels).toBe(1);
    expect(fetch.calls.some((call) => call.url.includes("/speech/recognize"))).toBe(false);
  });

  it("the microphone refused: says how to allow it; no microphone: the button goes", async () => {
    recorder.failWith = "denied";
    mount(ok);
    fireEvent.click(await screen.findByTestId("mic-button"));
    expect(
      await screen.findByText("Allow the microphone in the browser to speak a claim"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("mic-button")).toHaveAttribute("data-phase", "idle");
    recorder.failWith = "no_device";
    fireEvent.click(screen.getByTestId("mic-button"));
    expect(await screen.findByText("No microphone found")).toBeInTheDocument();
    expect(screen.queryByTestId("mic-button")).toBeNull();
  });

  it("not shown without speech to text on the server, or without a way to record", async () => {
    const first = mount(ok, "off");
    const fetch = first;
    await waitFor(() =>
      expect(fetch.calls.some((call) => call.url.includes("/healthz"))).toBe(true),
    );
    await act(async () => {});
    expect(screen.queryByTestId("mic-button")).toBeNull();
    first.unmount();
    Reflect.deleteProperty(navigator, "mediaDevices");
    mount(ok);
    await act(async () => {});
    expect(screen.queryByTestId("mic-button")).toBeNull();
  });
});
