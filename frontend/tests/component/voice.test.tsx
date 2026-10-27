// Day 5 components (docs/day5-design.md sections 10.5-10.6): the voice control in every state,
// its keys, the recap read aloud, the card being read.
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CommentaryCard } from "@/components/fan/CommentaryCard";
import { CommentaryFeed, RecapPanel } from "@/components/fan/FanFeed";
import { RecapAudio, formatDuration } from "@/components/fan/RecapAudio";
import { VoiceControl, isShortcut, voiceLabelKey } from "@/components/fan/VoiceControl";
import type { AudioController, AudioSnapshot } from "@/lib/audio/controller";
import type { CommentaryView, RecapView, SpeechCue } from "@/lib/contracts";
import { applyBatch, initialState } from "@/store/match-store";
import { fixture, ofType } from "../fixtures";
import { fakeFetch, renderWith } from "./render";

const casual = fixture("dominant_loser-42.casual-vi-beginner-har");
const views = ofType(casual, "commentary").map((m) => m.payload) as CommentaryView[];
const recaps = applyBatch(initialState, casual).recaps;

const idle: AudioSnapshot = {
  on: false,
  needsGesture: false,
  speakingId: null,
  source: null,
  waiting: 0,
  browserAvailable: false,
  browserOnly: false,
  counts: { played: 0, dropped: 0, preempted: 0, failed: 0, fallbacks: 0, skipped: 0 },
};

function cue(withAudio: boolean, note: SpeechCue["note"] = null): SpeechCue {
  return {
    lang: "vi-VN",
    voice: "vi-VN-HoaiMyNeural",
    gender: "female",
    preset: "recap",
    segments:
      note === "guard"
        ? []
        : [{ text: "Hết hiệp một.", preset: "recap", rate: 0.95, pitch: 1, volume: 1 }],
    audio: withAudio
      ? {
          clip_id: "b".repeat(32),
          url: `/audio/${"b".repeat(32)}.wav`,
          media_type: "audio/wav",
          duration_ms: 84_600,
          exact: false,
        }
      : null,
    note: withAudio ? null : (note ?? "circuit_open"),
  };
}

function controls(overrides: Partial<Parameters<typeof VoiceControl>[0]> = {}) {
  const props = {
    on: false,
    resume: false,
    gender: "female" as const,
    language: "vi" as const,
    provider: "mock" as const,
    audio: idle,
    onToggle: vi.fn(),
    onGender: vi.fn(),
    onSkip: vi.fn(),
    ...overrides,
  };
  return { props, view: renderWith(<VoiceControl {...props} />, "en") };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("VoiceControl", () => {
  it("off: one button that turns the voice on", () => {
    const { props } = controls();
    const toggle = screen.getByTestId("voice-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveTextContent("Turn the voice on");
    expect(screen.queryByTestId("voice-status")).toBeNull();
    fireEvent.click(toggle);
    expect(props.onToggle).toHaveBeenCalledTimes(1);
  });

  it("cannot be turned on for English commentary or without a voice on the server", () => {
    controls({ language: "en" });
    expect(screen.getByTestId("voice-toggle")).toBeDisabled();
    expect(screen.getByTestId("voice-toggle")).toHaveAttribute(
      "title",
      "The voice reads Vietnamese commentary",
    );
    controls({ provider: "off" });
    expect(screen.getAllByTestId("voice-toggle")[1]).toBeDisabled();
  });

  it("on: voices, where the voice comes from, skip, the AI note", () => {
    const { props } = controls({ on: true, audio: { ...idle, on: true } });
    expect(screen.getByTestId("voice-toggle")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("voice-female")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("voice-male")).toHaveAttribute("aria-checked", "false");
    fireEvent.click(screen.getByTestId("voice-male"));
    expect(props.onGender).toHaveBeenCalledWith("male");
    expect(screen.getByTestId("voice-status")).toHaveTextContent("Test voice");
    expect(screen.getByTestId("voice-skip")).toBeDisabled(); // nothing is being read
    expect(screen.getByText("AI-generated voice, reading synthetic data.")).toBeInTheDocument();
  });

  it("hides female / male when the browser reads with its only voice", () => {
    const voices = [{ name: "Linh", lang: "vi-VN" }]; // an iPhone
    vi.stubGlobal("speechSynthesis", {
      getVoices: () => voices,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });
    controls({ on: true, provider: "browser", audio: { ...idle, on: true, source: "browser" } });
    expect(screen.queryByTestId("voice-female")).toBeNull();
    expect(screen.getByTestId("voice-status")).toHaveTextContent("Browser voice");
  });

  it("keeps female / male when the browser has both (Edge)", () => {
    const voices = [
      // Edge 156 spells them with accents and spaces
      { name: "Microsoft Hoài My Online (Natural) - Vietnamese (Vietnam)", lang: "vi-VN" },
      { name: "Microsoft Nam Minh Online (Natural) - Vietnamese (Vietnam)", lang: "vi-VN" },
    ];
    vi.stubGlobal("speechSynthesis", {
      getVoices: () => voices,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });
    controls({ on: true, provider: "browser", audio: { ...idle, on: true, source: "browser" } });
    expect(screen.getByTestId("voice-male")).toBeInTheDocument();
  });

  it("skip is enabled while a line is read", () => {
    const { props } = controls({ on: true, audio: { ...idle, on: true, speakingId: "c1" } });
    fireEvent.click(screen.getByTestId("voice-skip"));
    expect(props.onSkip).toHaveBeenCalledTimes(1);
  });

  it("asks for a tap when the browser refused to play, or to resume a voice left on", () => {
    const blocked = controls({ on: false, audio: { ...idle, needsGesture: true } });
    expect(screen.getByTestId("voice-tap")).toHaveTextContent("Tap to turn the sound on");
    fireEvent.click(screen.getByTestId("voice-tap"));
    expect(blocked.props.onToggle).toHaveBeenCalledTimes(1);
    blocked.view.unmount();
    controls({ resume: true });
    expect(screen.getByTestId("voice-tap")).toHaveTextContent("Tap to turn the voice back on");
  });

  it("labels the source of the voice", () => {
    const on = { ...idle, on: true };
    expect(voiceLabelKey("azure", on)).toBe("fan.voiceServerAzure");
    expect(voiceLabelKey("mock", on)).toBe("fan.voiceServerMock");
    expect(voiceLabelKey("azure", { ...on, source: "browser" })).toBe("fan.voiceBrowser");
    expect(voiceLabelKey("azure", { ...on, browserOnly: true })).toBe("fan.voiceBrowser");
    expect(voiceLabelKey("browser", { ...on, browserAvailable: true })).toBe("fan.voiceBrowser");
    expect(voiceLabelKey("browser", on)).toBe("fan.voiceNoBrowser");
    expect(voiceLabelKey("off", on)).toBe("fan.voiceUnavailable");
    expect(voiceLabelKey("azure", { ...on, needsGesture: true })).toBe("fan.voiceTap");
  });

  it("keys: V turns the voice on or off, N skips; never while typing or with a modifier", () => {
    const { props } = controls({ on: true, audio: { ...idle, on: true, speakingId: "c1" } });
    fireEvent.keyDown(window, { key: "v" });
    expect(props.onToggle).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "N" });
    expect(props.onSkip).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "v", ctrlKey: true });
    const input = document.createElement("input");
    document.body.append(input);
    fireEvent.keyDown(input, { key: "v" });
    input.remove();
    expect(props.onToggle).toHaveBeenCalledTimes(1);
    expect(isShortcut(new KeyboardEvent("keydown", { key: "v", repeat: true }))).toBe(false);
  });

  it("keys do nothing when the voice cannot be used", () => {
    const { props } = controls({ language: "en" });
    fireEvent.keyDown(window, { key: "v" });
    fireEvent.keyDown(window, { key: "n" });
    expect(props.onToggle).not.toHaveBeenCalled();
    expect(props.onSkip).not.toHaveBeenCalled();
  });
});

describe("the card being read", () => {
  it("is marked and labelled; the feed stops announcing while the app speaks", () => {
    const item = views[3];
    if (!item) throw new Error("fixture too short");
    renderWith(<CommentaryCard item={item} scenario={null} ownTeam={null} speaking />, "vi");
    const card = screen.getByTestId("commentary-card");
    expect(card).toHaveAttribute("data-speaking", "true");
    expect(card).toHaveTextContent("Đang đọc");

    const { unmount } = renderWith(
      <CommentaryFeed
        items={views.slice(0, 5)}
        scenario={null}
        ownTeam={null}
        waiting={false}
        speakingId={views[2]?.id ?? null}
        voiceOn
      />,
      "vi",
    );
    const feed = screen.getByTestId("commentary-feed");
    expect(feed).toHaveAttribute("aria-live", "off");
    expect(feed.querySelectorAll("[data-speaking=true]")).toHaveLength(1);
    unmount();
    renderWith(
      <CommentaryFeed items={views.slice(0, 5)} scenario={null} ownTeam={null} waiting={false} />,
      "vi",
    );
    expect(screen.getByTestId("commentary-feed")).toHaveAttribute("aria-live", "polite");
  });
});

describe("RecapAudio", () => {
  const recap = recaps.H1 as RecapView;
  let controller: { dispatch: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    controller = { dispatch: vi.fn() };
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  });

  function mount(speech: SpeechCue | null, status = 200, lang: "vi" | "en" = "vi") {
    const fetch = fakeFetch({ "/recap?": { status, body: { ...recap, speech } } });
    vi.stubGlobal("fetch", fetch.impl);
    const view = renderWith(
      <RecapAudio
        matchId="dominant_loser-42"
        recap={recap}
        language={lang}
        mode="casual"
        gender="male"
        controller={controller as unknown as AudioController}
      />,
      "vi",
    );
    return { fetch, view };
  }

  it("is not offered for English", () => {
    mount(cue(true), 200, "en");
    expect(screen.queryByTestId("recap-audio")).toBeNull();
  });

  it("asks the server on the click, then plays it with controls and 'listen again'", async () => {
    const { fetch } = mount(cue(true));
    expect(screen.getByTestId("recap-audio")).toHaveAttribute("data-phase", "idle");
    fireEvent.click(screen.getByTestId("recap-listen"));
    const player = await screen.findByTestId("recap-player");
    expect(fetch.calls[0]?.url).toContain("/matches/dominant_loser-42/recap?scope=H1&lang=vi");
    expect(fetch.calls[0]?.url).toContain("&mode=casual&voice=male");
    expect(player.getAttribute("src")).toContain(`/audio/${"b".repeat(32)}.wav`);
    expect(player).toHaveAttribute("data-duration-ms", "84600");
    expect(screen.getByText("≈ 1:25")).toBeInTheDocument();

    fireEvent.play(player);
    expect(controller.dispatch).toHaveBeenLastCalledWith({ type: "recapStart" });
    fireEvent.pause(player);
    expect(controller.dispatch).toHaveBeenLastCalledWith({ type: "recapEnd" });
    (player as HTMLAudioElement).currentTime = 30;
    fireEvent.click(screen.getByTestId("recap-replay"));
    expect((player as HTMLAudioElement).currentTime).toBe(0);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
    fireEvent.ended(player);
    expect(controller.dispatch).toHaveBeenLastCalledWith({ type: "recapEnd" });
  });

  it("without a clip of the server: the browser's voice reads it, if it has one", async () => {
    const spoken: { text: string; onend: (() => void) | null }[] = [];
    vi.stubGlobal("speechSynthesis", {
      getVoices: () => [{ name: "Microsoft NamMinh Online (Natural)", lang: "vi-VN" }],
      speak: (utterance: { text: string; onend: (() => void) | null }) => spoken.push(utterance),
      cancel: () => {},
      addEventListener: () => {},
    });
    vi.stubGlobal(
      "SpeechSynthesisUtterance",
      class {
        lang = "";
        voice = null;
        rate = 1;
        pitch = 1;
        volume = 1;
        onend: (() => void) | null = null;
        onerror = null;
        constructor(public text: string) {}
      },
    );
    mount(cue(false));
    fireEvent.click(screen.getByTestId("recap-listen"));
    fireEvent.click(await screen.findByTestId("recap-browser"));
    expect(controller.dispatch).toHaveBeenLastCalledWith({ type: "recapStart" });
    expect(spoken.map((u) => u.text)).toEqual(["Hết hiệp một."]);
    act(() => spoken[0]?.onend?.());
    expect(controller.dispatch).toHaveBeenLastCalledWith({ type: "recapEnd" });
  });

  it("without a clip and without a browser voice: says so", async () => {
    mount(cue(false));
    fireEvent.click(screen.getByTestId("recap-listen"));
    expect(
      await screen.findByText("Trình duyệt này không có giọng tiếng Việt"),
    ).toBeInTheDocument();
  });

  it("a refused script, a failed request or a broken clip: the audio is not available", async () => {
    for (const [speech, status] of [
      [cue(false, "guard"), 200],
      [cue(true), 503],
      [null, 200],
    ] as const) {
      const { view } = mount(speech, status);
      fireEvent.click(screen.getByTestId("recap-listen"));
      expect(await screen.findByTestId("recap-audio-error")).toBeInTheDocument();
      view.unmount();
    }
    mount(cue(true));
    fireEvent.click(screen.getByTestId("recap-listen"));
    fireEvent.error(await screen.findByTestId("recap-player"));
    expect(await screen.findByTestId("recap-audio-error")).toBeInTheDocument();
  });

  it("a newer revision is offered, never cut in", async () => {
    const { view } = mount(cue(true));
    fireEvent.click(screen.getByTestId("recap-listen"));
    await screen.findByTestId("recap-player");
    view.rerender(
      <RecapAudio
        matchId="dominant_loser-42"
        recap={{ ...recap, revision: recap.revision + 1 }}
        language="vi"
        mode="casual"
        gender="male"
        controller={controller as unknown as AudioController}
      />,
    );
    expect(screen.getByTestId("recap-player")).toBeInTheDocument();
    const newer = { ...recap, revision: recap.revision + 1, speech: cue(true) };
    const refetch = fakeFetch({ "/recap?": { body: newer } });
    vi.stubGlobal("fetch", refetch.impl);
    fireEvent.click(screen.getByTestId("recap-update"));
    await waitFor(() => expect(screen.queryByTestId("recap-update")).toBeNull());
    expect(refetch.calls).toHaveLength(1);
    expect(screen.getByTestId("recap-player")).toBeInTheDocument();
  });

  it("leaving it ends the recap for the live queue", async () => {
    const { view } = mount(cue(true));
    fireEvent.click(screen.getByTestId("recap-listen"));
    await screen.findByTestId("recap-player");
    view.unmount();
    expect(controller.dispatch).toHaveBeenLastCalledWith({ type: "recapEnd" });
  });

  it("another voice in the panel is another recording (fresh state)", async () => {
    vi.stubGlobal(
      "fetch",
      fakeFetch({ "/recap?": { body: { ...recap, speech: cue(true) } } }).impl,
    );
    const audio = {
      matchId: "dominant_loser-42",
      language: "vi" as const,
      mode: "casual" as const,
      gender: "female" as const,
      controller: controller as unknown as AudioController,
    };
    const view = renderWith(<RecapPanel recaps={{ H1: recap }} audio={audio} />, "vi");
    fireEvent.click(screen.getByTestId("recap-listen"));
    await screen.findByTestId("recap-player");
    view.rerender(<RecapPanel recaps={{ H1: recap }} audio={{ ...audio, gender: "male" }} />);
    expect(screen.getByTestId("recap-listen")).toBeInTheDocument();
  });

  it("formats durations", () => {
    expect(formatDuration(84_600)).toBe("1:25");
    expect(formatDuration(59_400)).toBe("0:59");
    expect(formatDuration(-5)).toBe("0:00");
  });
});
