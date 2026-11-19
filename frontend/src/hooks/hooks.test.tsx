import { render, renderHook } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it } from "vitest";

import { APP_NAME, useDocumentTitle } from "./useDocumentTitle";
import { useFocusOnChange } from "./useFocusOnChange";

describe("useDocumentTitle", () => {
  it("names the app on its own and puts the screen first otherwise", () => {
    const { rerender } = renderHook(({ title }) => useDocumentTitle(title), {
      initialProps: { title: APP_NAME as string | null },
    });
    expect(document.title).toBe("WorkflowWeaver");
    rerender({ title: "Past workflows" });
    expect(document.title).toBe("Past workflows · WorkflowWeaver");
  });

  it("leaves the title alone for null (a screen that sets its own)", () => {
    document.title = "Set elsewhere";
    renderHook(() => useDocumentTitle(null));
    expect(document.title).toBe("Set elsewhere");
  });
});

function Screen({ screenKey }: { screenKey: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusOnChange(screenKey, ref);
  return (
    <div ref={ref}>
      <h1 tabIndex={-1}>Heading for {screenKey}</h1>
      <input aria-label="typing here" />
    </div>
  );
}

describe("useFocusOnChange", () => {
  it("does not move focus on first load", () => {
    render(<Screen screenKey="start" />);
    expect(document.body).toHaveFocus();
  });

  it("moves focus to the heading when the screen changes", () => {
    const { rerender, getByRole } = render(<Screen screenKey="start" />);
    rerender(<Screen screenKey="history" />);
    expect(getByRole("heading")).toHaveFocus();
  });

  it("does not steal focus when the same screen merely re-renders", () => {
    const { rerender, getByLabelText } = render(<Screen screenKey="start" />);
    getByLabelText("typing here").focus();
    rerender(<Screen screenKey="start" />);
    expect(getByLabelText("typing here")).toHaveFocus();
  });
});
