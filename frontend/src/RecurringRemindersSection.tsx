import {useEffect,useRef,useState} from 'react'
import {api} from './api'
import {RecurringRemindersController} from './recurring-reminders'
import type {State} from './recurring-reminders'
import {startReminderMonitor} from './reminder-monitor'
import RecurringRemindersPanel from './RecurringRemindersPanel'

const empty:State={candidates:null,reminders:null,loading:true,error:'',needsConsent:false}
type Props={enabled:boolean;busy:boolean;onConsentExpired:()=>void;onAuthorize:()=>void}

export default function RecurringRemindersSection({enabled,busy,onConsentExpired,onAuthorize}:Props){
  const [state,setState]=useState<State>(empty)
  const [resume,setResume]=useState(0)
  const controller=useRef<RecurringRemindersController|null>(null)
  const expired=useRef(onConsentExpired);expired.current=onConsentExpired
  useEffect(()=>{
    const next=new RecurringRemindersController(path=>api<unknown>(path),(path,body)=>api<unknown>(path,body),snapshot=>{
      setState(snapshot)
      if(snapshot.needsConsent&&enabled)expired.current()
    })
    controller.current=next
    const stop=startReminderMonitor(next,enabled,{every:(fn,ms)=>window.setInterval(fn,ms),cancel:id=>window.clearInterval(id as number)})
    const hide=()=>{stop();setState({...empty,loading:false})}
    const show=(event:PageTransitionEvent)=>{if(event.persisted)setResume(v=>v+1)}
    window.addEventListener('pagehide',hide);window.addEventListener('pageshow',show)
    return ()=>{stop();if(controller.current===next)controller.current=null;window.removeEventListener('pagehide',hide);window.removeEventListener('pageshow',show)}
  },[enabled,resume])
  return <RecurringRemindersPanel enabled={enabled} busy={busy} {...state} onRefresh={()=>{void controller.current?.load(enabled)}} onAck={(id,version)=>{void controller.current?.ack(id,version)}} onAuthorize={onAuthorize}/>
}
