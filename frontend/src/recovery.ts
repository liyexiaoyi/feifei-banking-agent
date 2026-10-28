/** Additive contract v1; fault health is separate from fraud safety_hold. */
export type ToolGroup='planner'|'bill'|'transfer'|'subscriptions'|'cards'|'wealth'|'codegen'
export type ToolHealth={
  group:ToolGroup
  state:'READY'|'PAUSED'|'PROBING'|'CHECK_REQUIRED'
  consecutive_failures:number
  retry_after_seconds:number
  probe_available:boolean
  needs_handoff:boolean
  version:string
}
