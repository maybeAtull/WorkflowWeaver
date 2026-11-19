import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ErrorBoundary from "./ErrorBoundary";

function Boom(): never {
  throw new Error("secret internal detail");
}

beforeEach(() => {
  // React logs a caught error to the console; keep the test output clean (and check we log it).
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe("ErrorBoundary", () => {
  it("shows its children when nothing goes wrong", () => {
    render(
      <ErrorBoundary>
        <p>All fine</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("All fine")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("replaces a crashed page with a calm message and never shows the technical detail", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong");
    expect(alert).toHaveTextContent("keeps running");
    expect(document.body).not.toHaveTextContent("secret internal detail");
    expect(console.error).toHaveBeenCalled(); // the detail goes to the console for whoever debugs it
  });

  it("offers a way to reload", () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload the page" }));
    expect(reload).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });
});
