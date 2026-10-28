/** Fence presentation updates only; never consume a caller's read failure. */
type RefreshEffects<S,T>={
  start:()=>void;progress:(value:S)=>void;success:(value:T)=>void;
  failure:(error:unknown)=>void;finish:()=>void
}
export class LatestRefresh{
  private epoch=0
  async run<S,T>(load:(publish:(value:S)=>void)=>Promise<T>,effects:RefreshEffects<S,T>):Promise<void>{
    const epoch=++this.epoch
    const current=()=>epoch===this.epoch
    effects.start()
    try{
      const value=await load(value=>{if(current())effects.progress(value)})
      if(current())effects.success(value)
    }catch(error){
      if(current())effects.failure(error)
      throw error
    }finally{if(current())effects.finish()}
  }
}
