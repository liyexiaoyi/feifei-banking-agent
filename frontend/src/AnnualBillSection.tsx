import {useEffect,useRef,useState} from 'react'
import {api} from './api'
import {AnnualBillController} from './annual-bill'
import type {AnnualBillState} from './annual-bill'
import AnnualBillPanel from './AnnualBillPanel'

export default function AnnualBillSection({initialYear}:{initialYear:string}){
  const [year,setYear]=useState(initialYear)
  const [snapshot,setSnapshot]=useState<AnnualBillState>({year:initialYear,report:null,loading:true,error:null})
  const controller=useRef<AnnualBillController|null>(null)
  useEffect(()=>{
    const next=new AnnualBillController(path=>api<unknown>(path),setSnapshot)
    controller.current=next
    void next.load(year)
    return ()=>{next.dispose();if(controller.current===next)controller.current=null}
  },[year])
  return <AnnualBillPanel year={year} report={snapshot.report} loading={snapshot.loading} error={snapshot.error||''} onYearChange={setYear} onRefresh={()=>{void controller.current?.load(year)}}/>
}
