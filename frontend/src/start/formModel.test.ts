import { describe, expect, it } from "vitest";

import type { TemplateInfo } from "../api/types";
import {
  FREE_TEXT_LIMIT,
  buildFreeTextBody,
  buildTemplateBody,
  counterState,
  simulateValue,
  validateFreeText,
  validateValues,
} from "./formModel";

const TEMPLATE: TemplateInfo = {
  id: "incident_response",
  name: "Incident response",
  description: "d",
  example: {},
  fields: [
    { key: "title", label: "Incident title", help: "", kind: "text", required: true, max_length: 10, options: [] },
    { key: "severity", label: "Severity", help: "", kind: "choice", required: true, max_length: 20, options: ["Low", "High"] },
    { key: "notes", label: "Notes", help: "", kind: "longtext", required: false, max_length: 100, options: [] },
  ],
};

describe("validateValues", () => {
  it("accepts a complete form and an empty optional field", () => {
    expect(validateValues(TEMPLATE, { title: "Slow", severity: "High" })).toEqual({});
  });

  it("asks for each missing required field in the backend's words", () => {
    expect(validateValues(TEMPLATE, { title: "  ", severity: "" })).toEqual({
      title: "Please fill in “Incident title”.",
      severity: "Please fill in “Severity”.",
    });
  });

  it("reports a value that is too long, with the numbers", () => {
    const problems = validateValues(TEMPLATE, { title: "x".repeat(11), severity: "Low" });
    expect(problems.title).toBe("“Incident title” is too long: 11 characters, the limit is 10.");
  });

  it("checks the length after trimming, like the backend", () => {
    expect(validateValues(TEMPLATE, { title: "  1234567890  ", severity: "Low" })).toEqual({});
  });

  it("rejects a choice that is not one of the options", () => {
    expect(validateValues(TEMPLATE, { title: "t", severity: "Apocalyptic" }).severity).toBe(
      "“Severity” must be one of: Low, High.",
    );
  });
});

describe("validateFreeText", () => {
  it("asks for a description when blank", () => {
    expect(validateFreeText("  \n ")).toBe("Describe the workflow you want to run.");
  });

  it("accepts text up to the limit and refuses beyond it", () => {
    expect(validateFreeText("x".repeat(FREE_TEXT_LIMIT))).toBeNull();
    expect(validateFreeText("x".repeat(FREE_TEXT_LIMIT + 1))).toBe(
      "Your description is too long: 6,001 characters, the limit is 6,000.",
    );
  });
});

describe("counterState", () => {
  it("stays hidden until the field is 80% full", () => {
    expect(counterState(79, 100).visible).toBe(false);
    expect(counterState(80, 100).visible).toBe(true);
  });

  it("flags going over and formats the numbers", () => {
    expect(counterState(4001, 4000)).toEqual({ visible: true, over: true, text: "4,001 / 4,000" });
    expect(counterState(4000, 4000).over).toBe(false);
  });
});

describe("simulateValue", () => {
  it("is off for a normal run and uses the backend's STEP:MODE format otherwise", () => {
    expect(simulateValue("off", 2)).toBeUndefined();
    expect(simulateValue("retry", 2)).toBe("s2:transient");
    expect(simulateValue("rollback", 3)).toBe("s3:fatal");
  });
});

describe("request bodies", () => {
  it("sends a template with every field, trimmed", () => {
    expect(buildTemplateBody(TEMPLATE, { title: " Slow ", severity: "High" }, undefined)).toEqual({
      template_id: "incident_response",
      values: { title: "Slow", severity: "High", notes: "" },
    });
  });

  it("adds the demo option only when one is set", () => {
    const withDemo = buildTemplateBody(TEMPLATE, { title: "t", severity: "Low" }, "s2:fatal");
    expect(withDemo).toMatchObject({ simulate: "s2:fatal" });
    expect("simulate" in buildFreeTextBody("hi", undefined)).toBe(false);
  });

  it("sends free text on its own, trimmed, never together with a template", () => {
    const body = buildFreeTextBody("  do it  ", "s1:transient");
    expect(body).toEqual({ request: "do it", simulate: "s1:transient" });
    expect("template_id" in body).toBe(false);
  });
});
