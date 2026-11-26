import { lstat, readFile } from 'node:fs/promises'
import { writeSync } from 'node:fs'
import { chromium } from 'playwright-core'

const API='http://127.0.0.1:8918',DASH='http://127.0.0.1:4175',LANDING='http://127.0.0.1:8917'
const actors=['admin','buyer','owner','realtor'],contexts=[],closed=new Set(),journeys=[],geometries=[],presenceSeen=new WeakSet(),uiReads=new WeakMap(),contextLocales=new WeakMap()
let browser,facts,accounts,stage='fixture',watchdog,failed=false,lastObservation=null
let actualLogins=0,presence=0,boundaries=0,apiPagingReads=0,uiPagingChecks=0,staleCancellations=0,currentReadErrors=0,recoveries=0,authLossChecks=0,untrackedAccounts=0
let pageErrors=0,unexpectedMutations=0,externalWrites=0,trackingRequests=0,blockedExternal=0
class Failure extends Error {}
const check=(value,reason)=>{if(!value)throw new Failure(reason)}
const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value)
const button=(scope,name)=>scope.getByRole('button',{name,exact:true})
const t=(copy,key,values={})=>{const value=key.split('.').reduce((part,item)=>part?.[item],copy.communications);check(typeof value==='string','LOCALIZED_COPY_REQUIRED');return value.replace(/\{\{(\w+)\}\}/g,(_,name)=>String(values[name]??''))}
function advance(value){check(/^[A-Za-z][A-Za-z0-9_]{0,95}$/.test(value),'FINITE_PHASE_REQUIRED');stage=value;clearTimeout(watchdog);watchdog=setTimeout(()=>fail(new Failure('STAGE_DEADLINE')),60000)}
function fail(error){
 if(failed)return;failed=true
 const reason=error instanceof Failure&&/^[A-Z][A-Z0-9_]{0,95}$/.test(error.message)?error.message:error?.name==='TimeoutError'?'TIMEOUT':'PROCESS_ERROR',frames=[]
 try{for(const line of String(error?.stack??'').split('\n')){const match=line.match(/(?:file:\/\/)?\/browser-run\/run-browser\.mjs:([0-9]{1,5}):([0-9]{1,5})\)?$/);if(match&&!frames.some(row=>row.line===+match[1]&&row.column===+match[2]))frames.push({line:+match[1],column:+match[2]});if(frames.length===8)break}}catch{}
 writeSync(1,'SIGNUP_HISTORY_BROWSER_FAILURE '+stage+' '+reason+'\n')
 if(lastObservation)writeSync(1,'SIGNUP_HISTORY_BROWSER_OBSERVATION '+JSON.stringify(lastObservation)+'\n')
 if(frames.length)writeSync(1,'SIGNUP_HISTORY_BROWSER_FRAMES '+JSON.stringify({frames})+'\n')
 process.exit(1)
}
process.on('uncaughtException',fail);process.on('unhandledRejection',fail)
const mutating=request=>!['GET','HEAD','OPTIONS'].includes(request.method())
const historyPath=id=>{check(uuid(id),'OWNED_ACCOUNT_ID_REQUIRED');return '/admin/users/'+id+'/communications'}
const expectedItems=(offset=0,limit=10)=>facts.ordered_items.slice(offset,offset+limit)
function metadata(data,offset,limit){
 check(data&&Object.keys(data).sort().join(',')==='items,limit,offset,total,tracked'&&data.total===11&&data.tracked===true&&data.offset===offset&&data.limit===limit&&Array.isArray(data.items),'EXACT_SCOPED_REPORT_REQUIRED')
 const want=expectedItems(offset,limit);check(data.items.length===want.length,'EXACT_PAGE_LENGTH_REQUIRED')
 const keys=['id','event_kind','channel','provider','step','status','attempts','max_attempts','created_at','first_attempt_at','last_attempt_at','next_attempt_at','accepted_at','error_code','outcome_uncertain'].sort().join(',')
 for(let index=0;index<want.length;index++){
  const row=data.items[index],expected=want[index]
  check(row&&Object.keys(row).sort().join(',')===keys&&row.event_kind==='signup'&&row.channel==='email'&&row.provider==='resend','METADATA_WHITELIST_REQUIRED')
  check(Object.keys(expected).every(key=>key.endsWith('_at')&&row[key]!==null&&expected[key]!==null
    ?Number.isFinite(Date.parse(row[key]))&&Date.parse(row[key])===Date.parse(expected[key])
    :JSON.stringify(row[key])===JSON.stringify(expected[key])),'ACTUAL_STORED_PAGE_ORDER_REQUIRED')
 }
 return data
}
async function read(context,path,status=200,boundary=null){
 check(!closed.has(context)&&path.startsWith('/')&&!path.startsWith('//'),'FIXED_OWNED_READ_REQUIRED')
 const response=await context.request.get(API+path,{headers:boundary?{'x-ci-boundary':boundary}:{},timeout:15000,maxRedirects:0})
 try{
  lastObservation={kind:'read',status:response.status()}
  check(response.status()===status,'ACTUAL_BOUNDARY_STATUS_REQUIRED')
  if(path.startsWith('/admin/users/'))check(/no-store/i.test(response.headers()['cache-control']??''),'PRIVATE_NO_STORE_REQUIRED')
  if(boundary)boundaries++
  return await response.json()
 }finally{await response.dispose()}
}
async function contextFor(locale){
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,timezoneId:'UTC',serviceWorkers:'block'})
 contexts.push(context);contextLocales.set(context,locale);uiReads.set(context,new Map());context.setDefaultTimeout(15000);context.setDefaultNavigationTimeout(20000)
 context.on('page',page=>{page.on('pageerror',()=>pageErrors++);page.on('request',request=>{const url=new URL(request.url());if(url.origin===API&&/^\/admin\/users\/[a-f0-9-]+\/communications$/.test(url.pathname)){const seen=uiReads.get(context);seen.set(url.pathname,(seen.get(url.pathname)??0)+1)}})})
 context.on('request',request=>{const url=new URL(request.url());if([API,DASH,LANDING].includes(url.origin)&&mutating(request)&&!(url.origin===API&&request.method()==='POST'&&url.pathname==='/auth/login'))unexpectedMutations++;if(/google-analytics|googletagmanager|facebook\.com\/tr|doubleclick/.test(url.href))trackingRequests++})
 await context.route('**/*',route=>{const request=route.request(),url=new URL(request.url());if(![API,DASH,LANDING].includes(url.origin)){blockedExternal++;if(mutating(request))externalWrites++;return route.abort('blockedbyclient')}return route.continue()})
 await context.addInitScript(({locale,owned})=>{if(!owned.includes(location.origin))return;localStorage.setItem('ildr_lang',locale);localStorage.setItem('ildr_measurement_consent_v1',JSON.stringify({choice:'denied',savedAt:Date.now()}));const before=navigator.onLine;if(!before)Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>true});Object.defineProperty(window,'__signupHistoryBrowser',{value:{before,modeled:!before}})}, {locale,owned:[DASH,LANDING]})
 return context
}
async function apiLogin(context,actor){
 const response=await context.request.post(API+'/auth/login',{data:accounts[actor],headers:{Origin:DASH},timeout:15000,maxRedirects:0})
 try{lastObservation={kind:'login',status:response.status()};check(response.status()===200&&(await context.cookies(API)).some(cookie=>cookie.name==='ildr_token'&&cookie.httpOnly),'REAL_COOKIE_LOGIN_REQUIRED')}finally{await response.dispose()}
 const me=await read(context,'/auth/me');check(me.id===facts.ids[actor]&&me.role===facts.roles[actor],'ACTUAL_ACTOR_ROLE_REQUIRED');actualLogins++
}
async function foreground(page){
 await page.bringToFront();await page.waitForFunction(()=>document.visibilityState==='visible'&&navigator.onLine)
 if(!presenceSeen.has(page)){
  const value=await page.evaluate(()=>({before:window.__signupHistoryBrowser.before,modeled:window.__signupHistoryBrowser.modeled,after:navigator.onLine,visible:document.visibilityState==='visible'}))
  check(Object.values(value).every(item=>typeof item==='boolean')&&value.modeled===!value.before&&value.after&&value.visible,'OWNED_LOOPBACK_PRESENCE_REQUIRED')
  console.log('SIGNUP_HISTORY_BROWSER_PRESENCE '+JSON.stringify({locale:contextLocales.get(page.context()),...value}));presenceSeen.add(page);presence++
 }
}
async function geometry(page,scope,label){
 for(const width of [320,390]){
  advance(label+'_geometry_'+width);await page.setViewportSize({width,height:844});await foreground(page)
  const value=await page.evaluate(()=>({width:innerWidth,document:document.documentElement.scrollWidth}))
  check(value.width===width&&value.document<=width+1,'VIEWPORT_OVERFLOW')
  const boxes=await scope.locator('input,select,textarea,button').evaluateAll(nodes=>nodes.filter(node=>{const r=node.getBoundingClientRect();return r.width>0&&r.height>0}).map(node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width}}))
  check(boxes.length<=200&&boxes.every(box=>box.left>=-1&&box.right<=width+1&&box.width<=width+1),'CONTROL_OVERFLOW')
  check(!geometries.some(row=>row.label===label&&row.width===width),'UNIQUE_GEOMETRY_REQUIRED')
  const record={label,width,document:value.document};geometries.push(record);console.log('SIGNUP_HISTORY_BROWSER_GEOMETRY '+JSON.stringify(record))
 }
}
async function responseFor(page,path,status,action,query={}){
 const waiting=page.waitForResponse(response=>{const url=new URL(response.url());return url.origin===API&&url.pathname===path&&response.request().method()==='GET'&&response.status()===status&&Object.entries(query).every(([key,value])=>url.searchParams.get(key)===String(value))})
 await action();const response=await waiting;await response.finished();lastObservation={kind:'read',status:response.status()};check(/no-store/i.test(response.headers()['cache-control']??''),'BROWSER_PRIVATE_NO_STORE_REQUIRED');return response
}
async function panel(page){
 const selected=page.locator('div.fixed.inset-y-0.right-0')
 await selected.waitFor({state:'visible'});check(await selected.count()===1,'UNIQUE_USER_PANEL_REQUIRED');return selected
}
async function closePanel(page){const selected=await panel(page),close=selected.locator('button.absolute.top-4.right-4');check(await close.count()===1,'SOURCE_CLOSE_CONTROL_REQUIRED');await close.click();await selected.waitFor({state:'hidden'})}
async function openUser(page,copy,actor){
 await page.getByPlaceholder(copy.admin.users_page.search_ph,{exact:true}).fill(facts.names[actor])
 const name=page.locator('div.sm\\:hidden').getByText(facts.names[actor],{exact:true});await name.waitFor({state:'visible'});check(await name.count()===1,'UNIQUE_OWNED_USER_CARD_REQUIRED');await name.click()
 const selected=await panel(page);await button(selected,t(copy,'account.open')).waitFor({state:'visible'});return selected
}
async function history(page,copy){const region=page.getByRole('region',{name:t(copy,'account.title'),exact:true});await region.waitFor({state:'visible'});check(await region.count()===1,'UNIQUE_ACCOUNT_HISTORY_REQUIRED');return region}
async function visibleRows(region,copy,items,locale){
 const rows=region.getByRole('listitem');await rows.first().waitFor({state:items.length?'visible':'hidden'})
 check(await rows.count()===items.length,'VISIBLE_PAGE_LENGTH_REQUIRED')
 for(let i=0;i<items.length;i++){
  const item=items[i],row=rows.nth(i),label=t(copy,/^email_\d+$/.test(item.step)?'steps.owner_email':'account.steps.'+item.step,{number:item.step.slice(6)})
  await row.getByRole('heading',{level:4,name:label,exact:true}).waitFor({state:'visible'})
  check(await row.getByText(t(copy,'statuses.'+item.status),{exact:true}).count()===1,'VISIBLE_STATUS_ISOLATION_REQUIRED')
  const date=await row.evaluate((_element,{locale,value})=>new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)),{locale:locale==='es'?'es-DO':'en-US',value:item.created_at})
  check((await row.innerText()).includes(date),'VISIBLE_DATE_ISOLATION_REQUIRED')
 }
 const text=await region.innerText()
 for(const forbidden of [...facts.ordered_items.map(item=>item.id),...facts.private_markers])check(!text.includes(forbidden),'NO_PRIVATE_CONTENT_OR_MACHINE_REFERENCE_REQUIRED')
 check(await region.locator('a,form').count()===0&&await button(region,t(copy,'retry')).count()===0,'READONLY_NO_SEND_RETRY_REQUIRED')
}
async function openHistory(page,copy,actor){
 const path=historyPath(facts.ids[actor]),selected=await panel(page)
 const response=await responseFor(page,path,200,()=>button(selected,t(copy,'account.open')).click(),{limit:10,offset:0})
 return {region:await history(page,copy),data:await response.json()}
}
async function hold(context,path,mode='hold'){
 let resolveCaptured,release,resolveDone,request,settled=false
 const captured=new Promise(resolve=>{resolveCaptured=resolve}),gate=new Promise(resolve=>{release=resolve}),done=new Promise(resolve=>{resolveDone=resolve})
 const handler=async route=>{
  const req=route.request(),url=new URL(req.url())
  if(settled||url.origin!==API||url.pathname!==path||req.method()!=='GET')return route.fallback()
  settled=true;request=req;resolveCaptured()
  try{if(mode==='abort'){await route.abort('connectionfailed');return}await gate;await route.continue()}
  catch(error){if(!request.failure())throw error}
  finally{resolveDone()}
 }
 await context.route('**/*',handler)
 return {captured,request:()=>request,release:async()=>{release();await done},remove:async()=>{await context.unroute('**/*',handler)}}
}
async function taggedBoundaries(sessions,anonymous,locale){
 const path=historyPath(facts.ids.buyer)
 metadata(await read(sessions.admin,path,200,locale+'_admin_tracked'),0,25)
 const empty=await read(sessions.admin,historyPath(facts.ids.owner),200,locale+'_admin_untracked')
 check(empty.total===0&&empty.tracked===false&&empty.items.length===0,'ACTUAL_UNTRACKED_ACCOUNT_REQUIRED')
 await read(sessions.admin,historyPath(facts.unknown_user_id),404,locale+'_admin_unknown')
 const malformed=await read(sessions.admin,'/admin/users/'+facts.invalid_user_id+'/communications',422,locale+'_admin_invalid')
 check(!JSON.stringify(malformed).includes(facts.invalid_user_id),'INVALID_INPUT_NOT_ECHOED_REQUIRED')
 await read(anonymous,path,401,locale+'_anonymous')
 for(const actor of ['buyer','owner','realtor'])await read(sessions[actor],path,403,locale+'_'+actor)
 const observed=[]
 for(const offset of [0,2,4,6,8,10]){const data=metadata(await read(sessions.admin,path+'?limit=2&offset='+offset),offset,2);observed.push(...data.items.map(item=>item.id));apiPagingReads++}
 check(JSON.stringify(observed)===JSON.stringify(facts.ordered_items.map(item=>item.id)),'REAL_API_PAGING_ORDER_REQUIRED')
}
async function localeJourney(locale){
 const copy=JSON.parse(await readFile(new URL('./'+locale+'.json',import.meta.url),'utf8')),sessions={}
 advance(locale+'_admin_user_entry')
 for(const actor of actors)sessions[actor]=await contextFor(locale)
 const anonymous=await contextFor(locale),page=await sessions.admin.newPage()
 for(const actor of ['buyer','owner','realtor'])await apiLogin(sessions[actor],actor)
 await page.goto(LANDING+'/login/',{waitUntil:'domcontentloaded'});await foreground(page)
 await page.locator('input[type="email"]').fill(accounts.admin.email);await page.locator('input[type="password"]').fill(accounts.admin.password)
 const signed=page.waitForResponse(response=>new URL(response.url()).origin===API&&new URL(response.url()).pathname==='/auth/login'&&response.request().method()==='POST'&&response.status()===200)
 await button(page,copy.auth.login.form.submit).click();await signed
 await page.waitForURL(url=>url.origin===DASH);const me=await read(sessions.admin,'/auth/me');check(me.id===facts.ids.admin&&me.role==='admin','ACTUAL_ADMIN_UI_LOGIN_REQUIRED');actualLogins++
 await page.goto(DASH+'/users?workspace=admin',{waitUntil:'domcontentloaded'});await foreground(page)
 await page.getByPlaceholder(copy.admin.users_page.search_ph,{exact:true}).waitFor({state:'visible'})
 await geometry(page,page.locator('main'),locale+'_users');journeys.push(locale+'_admin_user_entry')

 advance(locale+'_tracked_history_loading')
 let selected=await openUser(page,copy,'buyer'),path=historyPath(facts.ids.buyer)
 check(!uiReads.get(sessions.admin).get(path),'NO_HISTORY_READ_BEFORE_OPEN_REQUIRED')
 const firstHold=await hold(sessions.admin,path)
 await button(selected,t(copy,'account.open')).click();await firstHold.captured
 let region=await history(page,copy);await region.getByText(t(copy,'account.loading'),{exact:true}).waitFor({state:'visible'})
 check(await region.getAttribute('aria-busy')==='true','ACTUAL_LOADING_STATE_REQUIRED')
 await geometry(page,region,locale+'_history_loading')
 const initialResponse=page.waitForResponse(response=>new URL(response.url()).pathname===path&&response.request().method()==='GET'&&response.status()===200)
 await firstHold.release();const initial=await initialResponse;await initial.finished();metadata(await initial.json(),0,10);await firstHold.remove()
 await visibleRows(region,copy,expectedItems(),locale);journeys.push(locale+'_tracked_history_loading')

 advance(locale+'_metadata_and_paging_privacy')
 check(await region.getByText(t(copy,'account.accepted_note'),{exact:true}).count()===1,'ACCEPTANCE_NOT_INBOX_DELIVERY_REQUIRED')
 await geometry(page,region,locale+'_tracked_history')
 let response=await responseFor(page,path,200,()=>button(region,t(copy,'next')).click(),{limit:10,offset:10})
 metadata(await response.json(),10,10);await visibleRows(region,copy,expectedItems(10),locale)
 check(await button(region,t(copy,'next')).isDisabled()&&await button(region,t(copy,'previous')).isEnabled(),'REAL_SECOND_PAGE_REQUIRED');uiPagingChecks++
 response=await responseFor(page,path,200,()=>button(region,t(copy,'previous')).click(),{limit:10,offset:0})
 metadata(await response.json(),0,10);await visibleRows(region,copy,expectedItems(),locale);uiPagingChecks++
 journeys.push(locale+'_metadata_and_paging_privacy')

 advance(locale+'_untracked_and_stale_account_switch')
 const stale=await hold(sessions.admin,path)
 await button(region,t(copy,'refresh')).click();await stale.captured
 const pending=stale.request(),canceled=page.waitForEvent('requestfailed',{predicate:request=>request===pending})
 await closePanel(page);await canceled;check(!!pending.failure(),'ACCOUNT_CHANGE_ABORTS_OLD_READ_REQUIRED')
 selected=await openUser(page,copy,'owner');const empty=await openHistory(page,copy,'owner');region=empty.region
 check(empty.data.total===0&&empty.data.tracked===false&&empty.data.items.length===0,'REAL_EMPTY_ACCOUNT_REQUIRED')
 await region.getByText(t(copy,'account.untracked'),{exact:true}).waitFor({state:'visible'})
 untrackedAccounts++
 await stale.release();await stale.remove();check(await region.getByRole('listitem').count()===0,'OLD_ACCOUNT_CANNOT_REPLACE_CURRENT_HISTORY');staleCancellations++
 await geometry(page,region,locale+'_untracked_history');journeys.push(locale+'_untracked_and_stale_account_switch')

 advance(locale+'_read_error_retry')
 await closePanel(page);await openUser(page,copy,'buyer');({region}=await openHistory(page,copy,'buyer'));await visibleRows(region,copy,expectedItems(),locale)
 const broken=await hold(sessions.admin,path,'abort')
 await button(region,t(copy,'refresh')).click();await broken.captured
 await region.getByText(t(copy,'account.load_error'),{exact:true}).waitFor({state:'visible'})
 check(await region.getByRole('listitem').count()===0&&await region.getByText(t(copy,'account.untracked'),{exact:true}).count()===0,'READ_ERROR_IS_NOT_EMPTY_HISTORY_REQUIRED')
 await broken.release();await broken.remove();currentReadErrors++
 await geometry(page,region,locale+'_read_error')
 response=await responseFor(page,path,200,()=>button(region,t(copy,'refresh')).click(),{limit:10,offset:0})
 metadata(await response.json(),0,10);await visibleRows(region,copy,expectedItems(),locale);recoveries++
 journeys.push(locale+'_read_error_retry')

 advance(locale+'_auth_loss_and_role_boundaries')
 await taggedBoundaries(sessions,anonymous,locale)
 await sessions.admin.clearCookies()
 response=await responseFor(page,path,401,()=>button(region,t(copy,'refresh')).click(),{limit:10,offset:0})
 await region.getByText(t(copy,'account.access_lost'),{exact:true}).waitFor({state:'visible'})
 check(await region.getByRole('listitem').count()===0&&await region.getByText(t(copy,'account.untracked'),{exact:true}).count()===0,'REAL_AUTH_LOSS_CLEARS_HISTORY_REQUIRED')
 authLossChecks++;journeys.push(locale+'_auth_loss_and_role_boundaries')
 const completed=[...Object.values(sessions),anonymous]
 check(completed.length===5&&browser.contexts().length===5&&completed.reduce((count,context)=>count+context.pages().length,0)===1,'EXACT_COMPLETED_LOCALE_SESSIONS_REQUIRED')
 for(const context of completed){await context.close();closed.add(context)}
 check(browser.contexts().length===0,'COMPLETED_LOCALE_SESSIONS_CLOSED_REQUIRED')
}
async function run(){
 advance('fixture')
 const filename=process.env.ILDR_BROWSER_FIXTURES,info=await lstat(filename)
 check(info.isFile()&&!info.isSymbolicLink()&&info.uid===process.getuid()&&info.size>0&&info.size<=131072,'OWNED_BOUNDED_FIXTURE_REQUIRED')
 const prepared=JSON.parse(await readFile(filename,'utf8'));check(prepared.scope==='signup-email-history-only'&&prepared.accounts&&prepared.facts,'EXACT_FIXTURE_SCOPE_REQUIRED');({accounts,facts}=prepared)
 check(Object.keys(accounts).sort().join(',')===actors.toSorted().join(',')&&actors.every(actor=>uuid(facts.ids[actor])&&typeof facts.names[actor]==='string'&&facts.roles[actor]===actor),'FOUR_SYNTHETIC_ROLES_REQUIRED')
 check(uuid(facts.unknown_user_id)&&!Object.values(facts.ids).includes(facts.unknown_user_id)&&facts.invalid_user_id==='not-a-uuid'&&facts.ordered_items.length===11&&new Set(facts.ordered_items.map(item=>item.id)).size===11&&facts.ordered_items.every(item=>uuid(item.id))&&Array.isArray(facts.private_markers),'VALID_PRIVATE_STORED_FACTS_REQUIRED')
 browser=await chromium.launch({headless:true})
 try{
  await localeJourney('en');await localeJourney('es');advance('final')
  const names=['admin_user_entry','tracked_history_loading','metadata_and_paging_privacy','untracked_and_stale_account_switch','read_error_retry','auth_loss_and_role_boundaries']
  check(JSON.stringify(journeys)===JSON.stringify(['en','es'].flatMap(locale=>names.map(name=>locale+'_'+name))),'ALL_TWELVE_JOURNEYS_REQUIRED')
  check(geometries.length===20&&contexts.length===10&&closed.size===10&&presence===2&&actualLogins===8&&boundaries===16&&apiPagingReads===12&&uiPagingChecks===4,'EXACT_READ_SESSION_AND_PAGING_COUNTS_REQUIRED')
  check(staleCancellations===2&&currentReadErrors===2&&recoveries===2&&authLossChecks===2&&untrackedAccounts===2,'EXACT_RECOVERY_AND_DENIAL_COUNTS_REQUIRED')
  check(pageErrors===0&&unexpectedMutations===0&&externalWrites===0&&trackingRequests===0&&blockedExternal<=999,'READONLY_BROWSER_ISOLATION_REQUIRED')
  await browser.close();browser=null;clearTimeout(watchdog)
  console.log('SIGNUP_HISTORY_BROWSER_RESULT '+JSON.stringify({status:'PASS',locales:['en','es'],journeys:12,geometry:20,contexts:10,presence:2,actual_logins:8,api_boundary_checks:16,api_page_checks:apiPagingReads,ui_page_checks:uiPagingChecks,untracked_accounts:untrackedAccounts,stale_read_checks:staleCancellations,transport_error_recovery_checks:recoveries,auth_loss_checks:authLossChecks,default_api_limit:25,ui_page_size:10,ui_mutation_requests:unexpectedMutations,page_errors:pageErrors,unexpected_mutations:unexpectedMutations,external_writes:externalWrites,tracking_requests:trackingRequests,blocked_external_requests:blockedExternal}))
 }catch(error){fail(error)}finally{clearTimeout(watchdog);if(browser){for(const context of contexts)if(!closed.has(context))await context.close().catch(()=>{});await browser.close()}}
}
run().catch(fail)
