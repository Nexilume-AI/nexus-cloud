import { describe, expect, it } from "vitest";
import { containedScreenPoint, screenGesture } from "./mobileScreenControl";

describe("mobile screen coordinate mapping", () => {
  const rect = { left: 10, top: 20, width: 400, height: 400 };
  it("maps the actual portrait image instead of horizontal black bars", () => {
    expect(containedScreenPoint(rect, 100, 200, 160, 120)).toEqual({ x: 0.25, y: 0.25 });
    expect(containedScreenPoint(rect, 100, 200, 30, 120)).toBeNull();
    expect(containedScreenPoint(rect, 100, 200, 330, 120)).toBeNull();
  });
  it("maps landscape and rejects vertical black bars", () => {
    expect(containedScreenPoint(rect, 200, 100, 210, 220)).toEqual({ x: 0.5, y: 0.5 });
    expect(containedScreenPoint(rect, 200, 100, 210, 40)).toBeNull();
  });
  it("rejects unloaded dimensions, nonfinite values and out-of-frame pointers", () => {
    expect(containedScreenPoint(rect, 0, 100, 20, 20)).toBeNull();
    expect(containedScreenPoint(rect, 100, 100, NaN, 20)).toBeNull();
    expect(containedScreenPoint(rect, 100, 100, 410, 20)).toBeNull();
  });
  it("distinguishes taps, long presses and bounded swipes", () => {
    const start = { x: 0.2, y: 0.3 }, end = { x: 0.7, y: 0.8 };
    expect(screenGesture(start, start, 0, 100).action).toBe("tap_coordinates");
    expect(screenGesture(start, start, 0, 700).action).toBe("long_press");
    expect(screenGesture(start, end, 40, 9000)).toMatchObject({ action: "swipe", arguments: { start_x: 0.2, end_x: 0.7, duration_ms: 5000 } });
  });
});
