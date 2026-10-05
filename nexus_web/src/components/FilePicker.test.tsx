import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import { setLocale } from "../localization";
import { FilePicker } from "./FilePicker";

afterEach(() => setLocale("en-US"));

describe("localized file selection", () => {
  it("supplies English UI even when the browser uses Chinese native controls", () => {
    setLocale("en-US");
    const html = renderToStaticMarkup(<FilePicker label="API workbook or CSV" accept=".xlsx,.csv" files={[]} onFilesChange={() => {}} />);
    expect(html).toContain("Choose file");
    expect(html).toContain("No file selected");
    expect(html).not.toMatch(/[\u3400-\u9fff]/);
    // Only the custom button is visible/focusable; the native chooser still
    // carries the file restrictions and accessible input label.
    expect(html).toMatch(/<input[^>]*type="file"[^>]*hidden=""[^>]*accept="\.xlsx,\.csv"/);
    expect(html).toMatch(/<button[^>]*type="button"[^>]*aria-describedby=/);
  });

  it("translates empty and multiple-selection feedback to the application locale", () => {
    setLocale("zh-CN");
    const render = (files: File[]) => renderToStaticMarkup(<FilePicker label="附件" multiple files={files} onFilesChange={() => {}} />);
    expect(render([])).toContain("未选择文件");
    const html = render([new File(["a"], "one.txt"), new File(["b"], "two.txt")]);
    expect(html).toContain("选择文件");
    expect(html).toContain("已选择 2 个文件");
    expect(html).not.toContain("files selected");
  });

  it("keeps filenames verbatim, escapes markup, and reflects parent resets", () => {
    const file = new File(["text"], "中文-<sample>.csv");
    const render = (files: File[]) => renderToStaticMarkup(<FilePicker label="Upload" files={files} onFilesChange={() => {}} />);
    const html = render([file]);
    expect(html).toContain("中文-&lt;sample&gt;.csv");
    expect(html).not.toContain("<sample>");
    expect(render([])).toContain("No file selected");
  });

  it("disables both selection paths while retaining selected-file feedback", () => {
    const html = renderToStaticMarkup(<FilePicker label="Python file" disabled files={[new File(["code"], "agent.py")]} onFilesChange={() => {}} />);
    expect(html).toMatch(/<input[^>]*disabled=""/);
    expect(html).toMatch(/<button[^>]*disabled=""/);
    expect(html).toContain("agent.py");
  });
});
