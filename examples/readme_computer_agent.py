"""A hosted Agent works on the caller's Computer, not its own filesystem.

Upload this file with Build > Agents > Upload Python. The verified declaration
requests only Workspace file access. The caller must pair a Computer, approve
these scopes and Attach it before running. Use the companion expenses.csv in an
isolated Workspace. This deterministic example uses no model or API key.
"""
import csv
from decimal import Decimal
import hashlib
import io
from pathlib import Path
import tempfile

from nexus_agent import McpToolDescriptor, NexusAgent, NexusRunContext

agent = NexusAgent(
    agent_id="expense-report-assistant",
    computer_requirement="required",
    workspace_capabilities=("files.list", "files.read", "files.write"),
)


@agent.capability(
    "expenses.report",
    tool=McpToolDescriptor(
        name="expenses_report",
        title="Prepare my expense report",
        description="Read expenses.csv on your Attached Computer and ask before writing a report.",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
        chat=True,
        task=True,
        interactive=True,
    ),
)
def prepare_report(payload: dict, ctx: NexusRunContext) -> dict:
    """Create a deterministic expense summary inside the authorized Workspace."""
    ctx.plan.set([
        {"id": "read", "title": "Read expenses.csv on your Computer", "status": "running"},
        {"id": "confirm", "title": "Ask before writing", "status": "pending"},
        {"id": "write", "title": "Save and share the report", "status": "pending"},
    ])
    source = ctx.workspace.read_text("expenses.csv")
    rows = list(csv.DictReader(io.StringIO(source)))
    if not rows or len(rows) > 100 or any(set(row) != {"item", "category", "amount"} for row in rows):
        raise ValueError("Use the sample expenses.csv: item, category and amount; 1 to 100 rows.")
    totals = {}
    for row in rows:
        amount = Decimal(row["amount"])
        if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
            raise ValueError("Expense amounts must be finite, nonnegative USD values.")
        category = row["category"]
        totals[category] = totals.get(category, Decimal(0)) + amount
    total = sum(totals.values(), Decimal(0))
    ctx.plan.update("read", status="completed")
    ctx.plan.update("confirm", status="running")
    ctx.chat.say(
        f"I read **expenses.csv** from your Attached Computer: **{len(rows)} expenses, "
        f"${total:.2f} total**.\n\nThe Agent runs in Docker; the source file stays in "
        "your Workspace. Its contents are read through your authorized Run context."
    )
    answer = ctx.chat.ask(
        "Save expense-report.md in this Computer's Workspace?",
        key="write-expense-report",
        kind="confirm",
        choices=[
            {"value": "confirm", "label": "Save my report"},
            {"value": "cancel", "label": "Do not write"},
        ],
        timeout=600,
    )
    if answer.value != "confirm":
        ctx.chat.say("No report was written. Your source file is unchanged.")
        return {"status": "cancelled", "written": False}
    ctx.plan.update("confirm", status="completed")
    ctx.plan.update("write", status="running")
    report = (
        "# My expense report\n\n"
        "Source: `expenses.csv` on the caller's Attached Computer.\n\n"
        "| Category | Amount (USD) |\n| --- | ---: |\n"
        + "".join(f"| {category} | ${amount:.2f} |\n" for category, amount in sorted(totals.items()))
        + f"\n**Total: ${total:.2f} across {len(rows)} expenses.**\n\n"
        "The caller approved writing this report. The original CSV was not changed.\n\n"
        "> Deterministic SDK demo with synthetic expenses, not financial advice.\n"
    )
    ctx.workspace.write_text("expense-report.md", report)
    # Upload a matching private Run artifact for preview/download. This does
    # not mount the Computer into Docker or grant access to any other folder.
    with tempfile.TemporaryDirectory(prefix="nexus-report-") as directory:
        output = Path(directory) / "expense-report.md"
        output.write_text(report, encoding="utf-8")
        reference = ctx.output.upload_file(output, content_type="text/markdown", timeout=120)
    ctx.output.ready({"file_id": reference["file_id"]})
    ctx.plan.update("write", status="completed")
    ctx.chat.say(
        "**Your report is ready.**\n\n"
        "Saved `expense-report.md` beside your CSV on the Attached Computer. "
        "A private copy is also available in **Files**. No email was sent and nothing was published."
    )
    return {
        "status": "completed", "expenses": len(rows), "total_usd": f"{total:.2f}",
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
        "file": "expense-report.md",
    }
