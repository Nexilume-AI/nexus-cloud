import { describe, expect, it } from "vitest";

import { formatMoney } from "./format";

describe("formatMoney", () => {
  it("formats ISO currencies with Intl currency formatting", () => {
    expect(formatMoney("12.5", "USD")).toBe("$12.50");
  });

  it("formats non-ISO accounting units without throwing", () => {
    expect(formatMoney("10", "token_credit")).toBe("10 token credit");
  });
});
