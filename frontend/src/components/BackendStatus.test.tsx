import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import BackendStatus from "./BackendStatus";

afterEach(() => vi.unstubAllGlobals());

describe("BackendStatus", () => {
  it("says the backend is connected, in words that stay available to screen readers on a phone", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"status":"ok"}', { status: 200 })));
    render(<BackendStatus />);
    const status = await screen.findByText("Backend connected");
    expect(status).toHaveAttribute("role", "status");
    // Visually reduced to a dot on small screens, but never removed from the page.
    expect(status).toHaveClass("sr-only", "sm:not-sr-only");
  });

  it("says so when the backend cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<BackendStatus />);
    expect(await screen.findByText("Backend unreachable")).toBeInTheDocument();
  });
});
