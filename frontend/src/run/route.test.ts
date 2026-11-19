import { describe, expect, it } from "vitest";

import { parseRunHash, runHash } from "./route";

describe("the run address", () => {
  it("reads a run id from the hash", () => {
    expect(parseRunHash("#run=df60281df155")).toBe("df60281df155");
    expect(parseRunHash("#run=a_b-C9")).toBe("a_b-C9");
  });

  it("ignores any other hash, including odd or unsafe ones", () => {
    for (const hash of ["", "#", "#run=", "#other=abc", "#run=a b", "#run=<script>", "#run=a/b", "#run=" + "x".repeat(65)]) {
      expect(parseRunHash(hash)).toBeNull();
    }
  });

  it("round-trips", () => {
    expect(parseRunHash(runHash("abc123"))).toBe("abc123");
  });
});
