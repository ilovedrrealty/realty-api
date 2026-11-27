// A synthetic browser lifecycle observation, not application/privacy acceptance.
import http from 'node:http'
import { performance } from 'node:perf_hooks'
import { chromium } from 'playwright-core'

const PAGE='http://127.0.0.1:4175',API='http://127.0.0.1:8918'
const cases=['abort','reload','close'],servers=[]
let browser,stage='setup',external=0,contextsClosed=0,caseCount=0,totalFetches=0
const check=value=>{if(!value)throw new Error('probe_invariant')}
function emit(kind,value){const line='HELD_ROUTE_PROBE_'+kind+' '+JSON.stringify(value);check(Buffer.byteLength(line)<=1024);console.log(line)}
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done});return {promise,resolve}}
async function observe(terminal,ms){const start=performance.now();let timer;try{await Promise.race([terminal,new Promise(resolve=>{timer=setTimeout(resolve,ms)})])}finally{clearTimeout(timer)}return Math.ceil(performance.now()-start)}
async function listen(port,handler){const server=http.createServer(handler);servers.push(server);await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve)});return server}
function html(generation,retired){return `<!doctype html><meta charset="utf-8"><link rel="icon" href="data:,"><body data-generation="${generation}" data-retired="${retired}"><button id="abort">Abort</button><output id="value"></output><script>
window.probeAbortObserved=false;
${retired?'':`const controller=new AbortController(),xhr=new XMLHttpRequest();
xhr.onabort=()=>{window.probeAbortObserved=true};
controller.signal.addEventListener('abort',()=>xhr.abort(),{once:true});
document.querySelector('#abort').onclick=()=>controller.abort();
xhr.onload=()=>{document.querySelector('#value').textContent=JSON.parse(xhr.responseText).value};
xhr.open('GET','${API}/data');xhr.withCredentials=true;xhr.send();`}
</script>`}

async function runCase(name,state){
 stage=name
 const context=await browser.newContext({serviceWorkers:'block'}),page=await context.newPage()
 context.setDefaultTimeout(15000);context.setDefaultNavigationTimeout(15000)
 const captured=deferred(),release=deferred(),handled=deferred(),terminal=deferred()
 let original,actual,contextFailed=0,pageFailed=0,finished=0,responseStatus=null,used=false
 let outcome='not_attempted_failed',disposed=false,handlerDone=false,handlerError=false,livePage=page
 const startFetches=state.fetches
 context.on('requestfailed',request=>{if(request===original){contextFailed++;terminal.resolve()}})
 page.on('requestfailed',request=>{if(request===original)pageFailed++})
 context.on('requestfinished',request=>{if(request===original){finished++;terminal.resolve()}})
 context.on('response',response=>{if(response.request()===original)responseStatus=response.status()})
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url())
  if(url.origin===API&&url.pathname==='/data'&&request.method()==='GET'&&!used){
   used=true;original=request
   try{
    actual=await route.fetch({maxRetries:0,maxRedirects:0,timeout:15000})
    check(actual.status()===200);captured.resolve();await release.promise
    if(original.failure())outcome='not_attempted_failed'
    else{try{await route.fulfill({response:actual});outcome='returned'}catch(error){outcome=error?.constructor?.name==='TargetClosedError'?'target_closed':'other_error'}}
   }catch{handlerError=true;captured.resolve()}
   finally{if(actual){try{await actual.dispose();disposed=true}catch{handlerError=true}}handlerDone=true;handled.resolve()}
   return
  }
  if(url.origin===PAGE&&url.pathname==='/'&&request.method()==='GET')return route.continue()
  external++;await route.abort('blockedbyclient')
 })
 try{
  state.retired=false
  await page.goto(PAGE+'/',{waitUntil:'domcontentloaded'});await captured.promise;check(!handlerError&&actual?.status()===200)
  check(original.frame().page()===page)
  const generation=await page.locator('body').getAttribute('data-generation')
  if(name!=='abort')state.retired=true
  let actionSettled=false,actionFailed=false
  const action=(name==='abort'?page.getByRole('button',{name:'Abort',exact:true}).click():
   name==='reload'?page.reload({waitUntil:'domcontentloaded'}):page.close())
   .then(()=>{actionSettled=true},()=>{actionSettled=true;actionFailed=true})
  // Do not assume even the lifecycle action settles while interception is held.
  const beforeMs=await observe(terminal.promise,1000)
  const before={failed:contextFailed,page_failed:pageFailed,failure:!!original.failure(),finished,action_settled:actionSettled}
  release.resolve();await handled.promise
  await action;check(!actionFailed)
  if(name==='close'){livePage=await context.newPage();await livePage.goto(PAGE+'/',{waitUntil:'domcontentloaded'})}
  const nativeAbort=name==='abort'?await page.evaluate(()=>window.probeAbortObserved):null
  if(name==='abort')check(nativeAbort===true)
  const retired=await livePage.locator('body').getAttribute('data-generation')!==generation
  check(retired===(name!=='abort'))
  const afterMs=await observe(terminal.promise,2000)
  // A protocol round trip on the live document precedes the final observation.
  const stale=await livePage.locator('#value').evaluate(node=>node.textContent==='OWNED_STALE_VALUE')
  check(stale===false&&state.fetches-startFetches===1&&handlerDone&&disposed&&!handlerError)
  for(const value of [contextFailed,pageFailed,finished])check(Number.isInteger(value)&&value>=0&&value<=1)
  check(responseStatus===null||responseStatus===200)
  emit('CASE',{case:name,real_status:200,server_fetches:state.fetches-startFetches,original_page:true,
   before_failed:before.failed,before_page_failed:before.page_failed,before_failure:before.failure,before_finished:before.finished,
   after_failed:contextFailed,after_page_failed:pageFailed,after_failure:!!original.failure(),after_finished:finished,
   after_response_status:responseStatus,fulfill:outcome,handler_done:handlerDone,response_disposed:disposed,
   native_abort:nativeAbort,action_settled_before_release:before.action_settled,old_page_closed:page.isClosed(),document_retired:retired,new_page:name==='close',
   stale_value_present:stale,before_observation_ms:beforeMs,after_observation_ms:afterMs})
  caseCount++
 }finally{
  release.resolve()
  await context.close();contextsClosed++
 }
}

let watchdog
try{
 watchdog=setTimeout(()=>{emit('FAILURE',{stage,reason:'deadline'});process.exit(1)},60000)
 const state={generation:0,retired:false,fetches:0}
 await listen(4175,(request,response)=>{
  if(request.method!=='GET'||request.url!=='/'){external++;response.writeHead(404);response.end();return}
  response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'})
  response.end(html(++state.generation,state.retired))
 })
 await listen(8918,(request,response)=>{
  if(request.method!=='GET'||request.url!=='/data'){external++;response.writeHead(404);response.end();return}
  state.fetches++;totalFetches++
  response.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store','Access-Control-Allow-Origin':PAGE,'Access-Control-Allow-Credentials':'true'})
  response.end('{"value":"OWNED_STALE_VALUE"}')
 })
 browser=await chromium.launch({headless:true})
 for(const name of cases)await runCase(name,state)
 check(caseCount===3&&contextsClosed===3&&totalFetches===3&&external===0)
 stage='cleanup'
 await browser.close();check(!browser.isConnected())
 for(const server of servers){server.closeAllConnections();await new Promise((resolve,reject)=>server.close(error=>error?reject(error):resolve()))}
 emit('RESULT',{status:'OBSERVED',cases:3,contexts_closed:3,server_fetches:3,external_requests:0,browser_closed:true,servers_closed:2,product_acceptance:false})
}catch{
 emit('FAILURE',{stage,reason:'invariant_or_lifecycle'});process.exitCode=1
}finally{
 clearTimeout(watchdog)
 if(browser?.isConnected())await browser.close().catch(()=>{})
 for(const server of servers){server.closeAllConnections();server.close()}
}
