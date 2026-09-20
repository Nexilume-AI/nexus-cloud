import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import ts from "typescript";
import { expect, it } from "vitest";
import { messages } from "./messages";

const UI_ATTRS = new Set(["placeholder", "title", "aria-label", "alt", "aria-description", "aria-placeholder", "aria-valuetext"]);
const NON_UI_PROPS = /^(className|id|key|type|role|name|href|to|value|queryKey|url|path|method|format|variant|tone|size|icon|src|target|rel|data-|aria-(?!label|description|placeholder|valuetext))/;

function isClassList(value: string) {
  const tokens = value.trim().split(/\s+/);
  return tokens.length > 0 && tokens.every((token) => /^[a-z0-9!:\-\[\]\/().%_#*+~,>='"]+$/.test(token)) && tokens.some((token) => /[a-z]/.test(token));
}

/** Multi-word English copy a person would read, not identifiers or code. */
function looksLikeCopy(value: string) {
  const text = value.trim();
  if (text.length < 3 || !/[A-Za-z]/.test(text)) return false;
  if (/[<>{}$`\\]/.test(text)) return false;
  if (/^(import |from |assert |def |class |return |sys\.|subprocess|urllib|pathlib|scripts=|available=|probe=|opener=|request=|candidates=|python=)/.test(text)) return false;
  if (/^(https?|wss?):\/\//.test(text)) return false;
  if (/^[A-Za-z0-9_.\-/:@#;= ]+$/.test(text)) return false;
  if (isClassList(text)) return false;
  const words = text.split(/\s+/).filter((word) => /[A-Za-z]/.test(word));
  return words.filter((word) => /^[A-Za-z]/.test(word)).length >= 2;
}

it("keeps user-facing English behind the translation catalog", () => {
  const offenders: string[] = [];
  const root = fileURLToPath(new URL("../", import.meta.url));
  function walk(directory: string) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const file = join(directory, entry.name);
      if (entry.isDirectory()) { if (entry.name !== "localization") walk(file); continue; }
      if (!/\.tsx?$/.test(file) || /\.(test|d)\.tsx?$/.test(file)) continue;
      // The Computer bootstrap embeds a Python program and shell snippets; it is
      // transport code, not interface copy, and never renders in the workspace UI.
      if (entry.name === "computer-setup") continue;
      const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
      const parentOf = new Map<ts.Node, ts.Node>();
      (function link(node: ts.Node) { ts.forEachChild(node, (child) => { parentOf.set(child, node); link(child); }); })(source);
      const insideT = new Set<ts.Node>();
      (function mark(node: ts.Node) {
        if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && (node.expression.text === "t" || node.expression.text === "translate")) {
          const argument = node.arguments[0];
          if (argument) (function all(child: ts.Node) { insideT.add(child); ts.forEachChild(child, all); })(argument);
        }
        ts.forEachChild(node, mark);
      })(source);
      const inNonUi = (node: ts.Node) => {
        const directParent = parentOf.get(node);
        if (directParent && (ts.isPropertyAssignment(directParent) || ts.isPropertyDeclaration(directParent)) && directParent.name === node) return true;
        let parent = parentOf.get(node);
        while (parent) {
          if (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent)) return true;
          if (ts.isJsxAttribute(parent)) return !UI_ATTRS.has(parent.name.getText(source));
          if (ts.isPropertyAssignment(parent) || ts.isPropertyDeclaration(parent)) {
            if (NON_UI_PROPS.test(parent.name?.getText?.(source) ?? "")) return true;
          }
          if (ts.isCallExpression(parent)) {
            const callee = parent.expression.getText(source);
            if (/^(console|queryClient|import|require|JSON|Object|Array|String|Number|Math|Date|window|document|localStorage|fetch|api\.|client\.|cx|cn|clsx|twMerge|z\.|Intl|decodeURI|encodeURI)/.test(callee)) return true;
            if (/^(useState|useQuery|useMutation)$/.test(callee)) return true;
          }
          if (ts.isFunctionLike(parent)) break;
          parent = parentOf.get(parent);
        }
        return false;
      };
      // Error-code tables map machine codes to copy and are consumed via t(value).
      const inCodeTable = (node: ts.Node) => {
        let parent = parentOf.get(node);
        while (parent) {
          if (ts.isPropertyAssignment(parent) && parent.name === node) {
            const text = parent.name.getText(source);
            if (/^["']?code[:_ ]/.test(text) || /^(SOURCE_|ACCESS_|WORKER_|IMPORT_|FOLLOW_UP_|STEER_|TURN_|AGENT_|ROUTER_|TOOL_)/.test(text)) return true;
          }
          if (ts.isFunctionLike(parent)) break;
          parent = parentOf.get(parent);
        }
        return false;
      };
      // Thrown diagnostics describe programming invariants; anything a person
      // actually reads must be translated at the display site.
      const inThrownDiagnostic = (node: ts.Node) => {
        let parent = parentOf.get(node);
        for (let depth = 0; parent && depth < 3; depth += 1) {
          if (ts.isNewExpression(parent) || (ts.isCallExpression(parent) && parent.expression.getText(source) === "Error")) return true;
          parent = parentOf.get(parent);
        }
        return false;
      };
      // Command builders (shell/PowerShell snippets) are code, not interface copy.
      const inCommandTemplate = (node: ts.Node) => {
        let parent = parentOf.get(node);
        while (parent) {
          if (ts.isNoSubstitutionTemplateLiteral(parent) || ts.isTemplateExpression(parent)) {
            const text = parent.getText(source);
            if (text.includes("\n") || text.includes("$") || /\b(echo|Write-Host|mkdir|cp |cat >)/.test(text)) return true;
          }
          if (ts.isFunctionLike(parent)) break;
          parent = parentOf.get(parent);
        }
        return false;
      };
      (function visit(node: ts.Node) {
        if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
          const text = node.text;
          if (looksLikeCopy(text) && !Object.hasOwn(messages, text) && !insideT.has(node) && !inNonUi(node) && !inCommandTemplate(node) && !inThrownDiagnostic(node) && !inCodeTable(node)) {
            offenders.push(`${entry.name}: ${text}`);
          }
        }
        ts.forEachChild(node, visit);
      })(source);
    }
  }
  walk(root);
  expect(offenders).toEqual([]);
});
