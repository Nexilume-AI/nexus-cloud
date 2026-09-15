import { t, useLocale } from "../localization";
import { useState } from "react";
import { ArrowRight, Bot, Code2, Download, FileCheck2 } from "lucide-react";
import { Link } from "react-router-dom";
import { NexilumeTabs } from "./NexilumeControls";
import { useApplicationDistribution } from "../app/distribution";

const starter = `from nexus_agent.fastmcp import NexusMCPServer

server = NexusMCPServer("My Agent")

@server.tool(chat=True)
def hello(message: str) -> str:
    return "Hello " + message`;

/** An explanatory workflow, never a fabricated live Run or success claim. */
export function OverviewLaunchpad() {
  useLocale();
  const guide = useApplicationDistribution().agentDiscovery?.guide;
  const [mode, setMode] = useState<"use" | "build">("use");
  return (
    <section className="overview-launchpad" aria-label={t("How Nexus works")}>
      <div className="overview-launchpad__heading">
        <span className="overview-agent-mark" aria-hidden="true">
          <Bot size={25} />
        </span>
        <div>
          <span className="tech-label">{t("ONE AGENT. YOUR NEXT STEP.")}</span>
          <h2>{t("Choose how to start")}</h2>
        </div>
      </div>
      <NexilumeTabs
        label={t("Getting started")}
        idBase="overview-start"
        variant="compact"
        value={mode}
        onChange={setMode}
        options={[
          { value: "use", label: t("Use an Agent") },
          { value: "build", label: t("Bring your code") },
        ]}
      />
      <div
        role="tabpanel"
        id="overview-start-panel-use"
        aria-labelledby="overview-start-tab-use"
        hidden={mode !== "use"}
        tabIndex={0}
      >
        <ol className="overview-path">
          <li>
            <span aria-hidden="true">01</span>
            <div>
              <h3>{guide?.title ?? t("Choose one of your Agents")}</h3>
              <p>
                {guide?.description ?? t("Inspect tools and runtime availability in your personal Agent list.")}
              </p>
            </div>
          </li>
          <li>
            <span aria-hidden="true">02</span>
            <div>
              <h3>{t("Call it on your terms")}</h3>
              <p>
                {guide?.accessDescription ?? t("Open Private Display or connect your MCP client using scoped credentials.")}
              </p>
            </div>
          </li>
          <li>
            <span aria-hidden="true">03</span>
            <div>
              <h3>{t("Follow the work")}</h3>
              <p>{t("View your run and any traces or files the Agent reports.")}</p>
            </div>
          </li>
        </ol>
        <div className="overview-launchpad__footer">
          <FileCheck2 size={16} />
          <span>{t("Workflow guide · no live task is running")}</span>
        </div>
      </div>
      <div
        role="tabpanel"
        id="overview-start-panel-build"
        aria-labelledby="overview-start-tab-build"
        hidden={mode !== "build"}
        tabIndex={0}
      >
        <p className="overview-code-label">
          <Code2 size={16} />
          agent.py <span>{t("Minimal working example")}</span>
        </p>
        <pre className="overview-code">
          <code>{starter}</code>
        </pre>
        <p className="overview-build-note">{t("Python 3.12 · single file · optional requirements.txt. Build and verify first; deploy when ready.")}</p>
        <div className="overview-launchpad__footer">
          <a
            href={`${import.meta.env.BASE_URL}examples/nexus_python_agent.py`}
            download
          >
            <Download size={16} />{t("Download starter")}</a>
          <Link to="/agents?create=1">{t("Start setup")}{" "}<ArrowRight size={15} />
          </Link>
        </div>
      </div>
    </section>
  );
}
