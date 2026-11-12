/** Strongest first, fastest last; ranks are preferences, not benchmark claims. */
export function decisionRole(index: number, count: number): string {
  if (count <= 1) return "Balanced";
  const roles = count === 2 ? ["Deep reasoning", "Fast"]
    : count === 3 ? ["Deep reasoning", "Balanced", "Fast"]
      : count === 4 ? ["Deep reasoning", "Advanced", "Balanced", "Fast"]
        : ["Deep reasoning", "Advanced", "Balanced", "Efficient", "Fast"];
  return roles[Math.round(index * (roles.length - 1) / (count - 1))];
}

export function orderedDecisionModels<T extends { id: string; priority: number; status: string }>(rows: T[], order: string[] = []): T[] {
  const rank = new Map(order.map((id, i) => [id, i]));
  return rows.filter(row => row.status === "active").slice().sort((a, b) =>
    (rank.get(a.id) ?? order.length) - (rank.get(b.id) ?? order.length) || a.priority - b.priority || a.id.localeCompare(b.id));
}
