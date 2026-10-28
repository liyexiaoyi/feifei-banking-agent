import test from 'node:test'
import assert from 'node:assert/strict'
import {startReminderMonitor} from './reminder-monitor.ts'

test('monitor checks once then every 60 seconds, skips in-flight and stops on disposal',async()=>{
 let callback!:()=>void,delay=0,calls=0,cleared=0,disposed=0,release!:()=>void
 const target={state:{loading:false,needsConsent:false},load:async()=>{calls++;target.state.loading=true;await new Promise<void>(r=>release=r);target.state.loading=false},dispose:()=>disposed++}
 const stop=startReminderMonitor(target,true,{every:(fn,ms)=>{callback=fn;delay=ms;return 1},cancel:()=>cleared++})
 assert.equal(calls,1);assert.equal(delay,60000);callback();callback();assert.equal(calls,1)
 release();await Promise.resolve();await Promise.resolve();callback();assert.equal(calls,2)
 stop();callback();assert.equal(calls,2);assert.equal(cleared,1);assert.equal(disposed,1);stop();assert.equal(disposed,1);release()
})

test('no consent schedules no repeating check, and expired consent stops the timer',async()=>{
 let scheduled=0,cleared=0,callback!:()=>void
 const clock={every:(fn:()=>void)=>{scheduled++;callback=fn;return 1},cancel:()=>cleared++}
 const no={state:{loading:false,needsConsent:false},load:async(enabled:boolean)=>{assert.equal(enabled,false)},dispose:()=>{}}
 startReminderMonitor(no,false,clock);assert.equal(scheduled,0)
 const expired={state:{loading:false,needsConsent:false},load:async()=>{expired.state.needsConsent=true},dispose:()=>{}}
 startReminderMonitor(expired,true,clock);await Promise.resolve();await Promise.resolve();assert.equal(scheduled,1);assert.equal(cleared,1)
 callback();assert.equal(cleared,1)
})
