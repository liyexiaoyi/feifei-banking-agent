export type GoalOperationBindable = { id: string; operation_ids?: readonly string[] | null }

const binds = (g: GoalOperationBindable | null | undefined, opId: string): boolean =>
  !!g && Array.isArray(g.operation_ids) && g.operation_ids.includes(opId)

// Pure: the current goal takes precedence, then the first saved goal that lists the operation.
export function goalOperationBinding<C extends GoalOperationBindable, S extends GoalOperationBindable>(
  current: C | null | undefined,
  saved: readonly S[] | null | undefined,
  opId: string,
): C | S | null {
  if (binds(current, opId)) return current as C
  for (const g of Array.isArray(saved) ? saved : []) if (binds(g, opId)) return g
  return null
}
