export function formatTerminalStatusLine(message: string, cursorColumn: number): string {
  const prefix = cursorColumn > 0 ? "\r\n" : "";
  return `${prefix}[${message}]\r\n`;
}

/**
 * Normalizes a streamed terminal byte sequence without treating WebSocket frame
 * boundaries as line boundaries. A trailing carriage return is retained until
 * the next frame so a split CRLF pair is emitted exactly once.
 */
export class TerminalOutputNormalizer {
  private pendingCarriageReturn = false;

  push(value: string): string {
    const input = String(value || "");
    let output = "";
    let index = 0;

    if (this.pendingCarriageReturn) {
      if (input.startsWith("\n")) {
        output += "\r\n";
        index = 1;
      } else {
        output += "\r";
      }
      this.pendingCarriageReturn = false;
    }

    while (index < input.length) {
      const character = input[index];
      if (character === "\r") {
        if (index + 1 >= input.length) {
          this.pendingCarriageReturn = true;
          break;
        }
        if (input[index + 1] === "\n") {
          output += "\r\n";
          index += 2;
          continue;
        }
        output += "\r";
        index += 1;
        continue;
      }
      if (character === "\n") {
        output += "\r\n";
        index += 1;
        continue;
      }
      output += character;
      index += 1;
    }
    return output;
  }

  flush(): string {
    if (!this.pendingCarriageReturn) return "";
    this.pendingCarriageReturn = false;
    return "\r";
  }
}
