import { readFileSync, readdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import ts from "typescript";
import { expect, it } from "vitest";
import { messages } from "./messages";

it("ships a translation for every explicit source message", () => {
  const missing: string[] = [];
  const root = fileURLToPath(new URL("../", import.meta.url));
  const catalog = { ...messages };
  const edition = join(root, "enterprise", "localization");
  if (existsSync(edition)) {
    for (const file of readdirSync(edition).filter(file => file.endsWith(".json"))) {
      Object.assign(catalog, JSON.parse(readFileSync(join(edition, file), "utf8")));
    }
  }
  function walk(directory: string) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const file = join(directory, entry.name);
      if (entry.isDirectory()) { if (entry.name !== "localization") walk(file); continue; }
      if (!/\.tsx?$/.test(file) || /\.(test|d)\.tsx?$/.test(file)) continue;
      const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
      function visit(node: ts.Node) {
        if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "t") {
          const key = node.arguments[0];
          if (key && ts.isStringLiteralLike(key) && !Object.hasOwn(catalog, key.text)) missing.push(`${entry.name}: ${key.text}`);
        }
        ts.forEachChild(node, visit);
      }
      visit(source);
    }
  }
  walk(root);
  expect(missing).toEqual([]);
});
