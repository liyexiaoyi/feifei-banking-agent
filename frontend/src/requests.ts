type Entry={key:string;inFlight?:Promise<unknown>}

/** Retain the intent key until both the mutation and state synchronization succeed. */
export class RequestCoordinator{
  private entries=new Map<string,Entry>()

  run<T>(intent:string,send:(key:string)=>Promise<T>,synchronize:()=>Promise<void>,durableKey?:string):Promise<T>{
    let entry=this.entries.get(intent)
    if(!entry){entry={key:durableKey||crypto.randomUUID()};this.entries.set(intent,entry)}
    if(entry.inFlight)return entry.inFlight as Promise<T>
    const current=entry
    const work=(async()=>{
      try{
        const result=await send(current.key)
        await synchronize()
        this.entries.delete(intent)
        return result
      }finally{current.inFlight=undefined}
    })()
    current.inFlight=work
    return work
  }
}
