// Day 6 studio help (docs/day6-design.md section 8.5, P1): every panel, the four verdicts with
// their colour, icon and words, and the shortcuts; "?" opens it unless the user is typing.
import { act, fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HelpButton } from "@/components/studio/HelpDrawer";
import { renderWith } from "./render";

describe("studio help", () => {
  it("opens from its button with the panels, the verdicts and the keys; Escape closes it", () => {
    renderWith(<HelpButton />);
    expect(screen.queryByTestId("help")).toBeNull();
    fireEvent.click(screen.getByTestId("help-open"));
    const help = screen.getByTestId("help");
    expect(screen.getByRole("dialog")).toHaveAccessibleName("How the Studio works");
    expect(help.querySelectorAll("dt")).toHaveLength(7);
    for (const words of ["True", "Partly true", "False", "Not enough data"]) {
      expect(within(help).getByText(words)).toBeInTheDocument();
    }
    expect(help).toHaveTextContent("has not been played");
    expect(help.querySelectorAll("kbd")).toHaveLength(4);
    act(() => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(screen.queryByTestId("help")).toBeNull();
  });

  it('"?" opens it, but not while typing a claim', () => {
    renderWith(
      <>
        <input aria-label="claim" />
        <HelpButton />
      </>,
    );
    fireEvent.keyDown(screen.getByLabelText("claim"), { key: "?" });
    expect(screen.queryByTestId("help")).toBeNull();
    fireEvent.keyDown(document.body, { key: "?" }); // nothing focused
    expect(screen.getByTestId("help")).toBeInTheDocument();
  });

  it("speaks Vietnamese too", () => {
    renderWith(<HelpButton />, "vi");
    fireEvent.click(screen.getByTestId("help-open"));
    expect(screen.getByRole("dialog")).toHaveAccessibleName("Cách dùng Studio");
    expect(screen.getByTestId("help")).toHaveTextContent("Chạy hoặc dừng trận");
  });
});
