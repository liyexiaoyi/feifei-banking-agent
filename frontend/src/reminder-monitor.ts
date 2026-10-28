type Target={state:{loading:boolean;needsConsent:boolean};load:(enabled:boolean)=>Promise<void>;dispose:()=>void}
type Clock={every:(callback:()=>void,ms:number)=>unknown;cancel:(handle:unknown)=>void}

export function startReminderMonitor(target:Target,enabled:boolean,clock:Clock):()=>void{
  let stopped=false,handle:unknown
  const stop=()=>{
    if(stopped)return
    stopped=true
    if(handle!==undefined)clock.cancel(handle)
    target.dispose()
  }
  const check=async()=>{
    if(stopped||target.state.loading)return
    if(target.state.needsConsent){stop();return}
    try{await target.load(enabled)}finally{if(target.state.needsConsent)stop()}
  }
  void check().catch(()=>{})
  if(enabled)handle=clock.every(()=>{void check().catch(()=>{})},60000)
  return stop
}
