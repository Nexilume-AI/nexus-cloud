import { Card } from "../components/Card";

export function PlaceholderPage({ title, phase }: { title: string; phase: string }) {
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-ink">{title}</h1>
        <p className="mt-1 text-sm text-muted">Planned for {phase}; the navigation contract is already in place.</p>
      </div>
      <Card title="Implementation queue">
        <p className="text-sm leading-6 text-muted">
          This section is intentionally staged behind the core gateway loop. The first production slice focuses on
          provider connection, deployment publication, API key policy, and live gateway testing.
        </p>
      </Card>
    </div>
  );
}
