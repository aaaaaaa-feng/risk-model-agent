import type { BinSpec } from "../types";

export function editableBinSpec(spec: BinSpec | undefined) {
  if (!spec) return { kind: "numeric", edges: [] };
  return spec.kind === "numeric"
    ? { kind: "numeric", edges: spec.edges || [] }
    : { kind: "categorical", groups: spec.groups || [], rare_values: spec.rare_values || [] };
}
