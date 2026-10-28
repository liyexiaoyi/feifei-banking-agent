import {useEffect,useRef,useState} from 'react'
import {api} from './api'
import {HandoffProgressController} from './handoff-progress-controller'
import type {HandoffProgressState} from './handoff-progress-controller'
import type {LocalHandoff} from './task-plan'
import HandoffProgressPanel from './HandoffProgressPanel'

export default function HandoffProgressCard({summary,blocked}:{summary:LocalHandoff;blocked:boolean}){
 const [state,setState]=useState<HandoffProgressState>({view:null,loading:true,busy:false,error:'',notice:''})
 const active=useRef<HandoffProgressController|null>(null)
 useEffect(()=>{
  const controller=new HandoffProgressController({plan_id:summary.plan_id,handoff_id:summary.id},api,setState)
  active.current=controller;void controller.refresh()
  return ()=>{controller.dispose();if(active.current===controller)active.current=null}
 },[summary.id,summary.plan_id])
 return <><HandoffProgressPanel view={state.view} loading={state.loading} busy={state.busy||blocked} error={state.error} onRefresh={()=>{if(!blocked)void active.current?.refresh()}} onUpdate={status=>{if(!blocked)void active.current?.update(status)}}/>{state.notice&&<p className="handoff-progress-notice" role="status">{state.notice}</p>}</>
}
