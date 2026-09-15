import { afterEach, expect, it } from "vitest";
import { setLocale } from "../localization";
import { providerImportStatusLabel } from "./providerImportStatus";

afterEach(() => setLocale("zh-CN"));

it("localizes import states at display time and preserves unknown wire states", () => {
  const states = ["preview", "processing", "partial", "complete", "expired", "discarded"];
  setLocale("en-US");
  const english = states.map(providerImportStatusLabel);
  setLocale("zh-CN");
  for (const [index, status] of states.entries()) {
    expect(providerImportStatusLabel(status)).toMatch(/[\u4e00-\u9fff]/);
    expect(providerImportStatusLabel(status)).not.toBe(english[index]);
  }
  expect(providerImportStatusLabel("future_state" )).toBe("future_state");
  setLocale("en-US");
  expect(states.map(providerImportStatusLabel)).toEqual(english);
});
