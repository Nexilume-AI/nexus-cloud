import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";

const root = resolve("dist/community");
const types = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json" };
createServer(async (request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  const file = resolve(root, "." + pathname.replace(/^\/static\/web/, ""));
  if (file !== root && !file.startsWith(root + sep)) { response.writeHead(403).end(); return; }
  try {
    const target = await stat(file).then(info => info.isFile() ? file : resolve(root, "index.html")).catch(() => resolve(root, "index.html"));
    response.writeHead(200, { "Content-Type": types[extname(target)] || "application/octet-stream" });
    response.end(await readFile(target));
  } catch { response.writeHead(500).end(); }
}).listen(4178, "127.0.0.1", () => console.log("Community test preview: http://127.0.0.1:4178"));
