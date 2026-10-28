/** Whole-workspace privacy gate: shown once the session context is blocked. Stateless so it can be rendered directly. */
type SessionBoundaryGateProps={onReload:()=>void}

/** Blocked snapshots carry no owner; only the discriminant is inspected. */
export function isSessionBlocked(snapshot:unknown):boolean{
  if(!snapshot||typeof snapshot!=='object')return false
  const s=snapshot as {state?:unknown;status?:unknown;kind?:unknown}
  return s.state==='blocked'||s.status==='blocked'||s.kind==='blocked'
}

export default function SessionBoundaryGate({onReload}:SessionBoundaryGateProps){
  return <main className="deployment-gate session-boundary-gate" role="alert" aria-labelledby="session-boundary-title" aria-describedby="session-boundary-desc">
    <h1 id="session-boundary-title">账户状态需核对</h1>
    <div id="session-boundary-desc">
      <p>当前登录状态已变化，已收起刚才资料。</p>
      <p>刚才的操作可能已经处理。重载后请核对原操作记录和结果，再决定是否继续。</p>
    </div>
    <button type="button" autoFocus style={{minHeight:48,minWidth:48,fontSize:18}} onClick={()=>onReload()}>重新加载页面</button>
  </main>
}
