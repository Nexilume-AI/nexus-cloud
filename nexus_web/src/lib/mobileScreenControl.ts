export type ScreenPoint = { x: number; y: number };

/** Map CSS coordinates to object-contain content, never to its letterbox. */
export function containedScreenPoint(
  rect: { left: number; top: number; width: number; height: number },
  imageWidth: number, imageHeight: number, clientX: number, clientY: number
): ScreenPoint | null {
  if (![rect.left, rect.top, rect.width, rect.height, imageWidth, imageHeight, clientX, clientY].every(Number.isFinite)
      || rect.width <= 0 || rect.height <= 0 || imageWidth <= 0 || imageHeight <= 0) return null;
  const scale = Math.min(rect.width / imageWidth, rect.height / imageHeight);
  const width = imageWidth * scale;
  const height = imageHeight * scale;
  const x = clientX - rect.left - (rect.width - width) / 2;
  const y = clientY - rect.top - (rect.height - height) / 2;
  if (x < 0 || y < 0 || x >= width || y >= height) return null;
  return { x: x / width, y: y / height };
}

export function screenGesture(start: ScreenPoint, end: ScreenPoint, distance: number, duration: number) {
  if (distance >= 8) return { action: "swipe" as const, arguments: {
    start_x: start.x, start_y: start.y, end_x: end.x, end_y: end.y,
    duration_ms: Math.max(50, Math.min(5000, Math.round(duration)))
  } };
  if (duration >= 500) return { action: "long_press" as const, arguments: { x: start.x, y: start.y, duration_ms: Math.min(5000, Math.round(duration)) } };
  return { action: "tap_coordinates" as const, arguments: { x: start.x, y: start.y } };
}
