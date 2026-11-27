import { lstat, readFile } from 'node:fs/promises'
import { writeSync } from 'node:fs'
import { chromium } from 'playwright-core'

const API='http://127.0.0.1:8918',DASH='http://127.0.0.1:4175',LANDING='http://127.0.0.1:8917'
const actors=['admin','buyer','owner','realtor'],contexts=[],closed=new Set(),journeys=[],geometries=[],presenceSeen=new WeakSet(),uiReads=new WeakMap(),contextLocales=new WeakMap()
let browser,facts,accounts,stage='fixture',watchdog,failed=false,lastObservation=null
const requestIds=new Set()
let assignmentRequests=0,controlTransitions=0,assignmentPageChecks=0,assignmentReplay=0,uncertainCommits=0,refusalChecks=0,knownReadRecoveries=0,accountNoEmail=0,valuationNotices=0,sellerNotices=0,keyboardChecks=0;
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
 writeSync(1,'ASSIGNMENT_BROWSER_FAILURE '+stage+' '+reason+'\n')
 if(lastObservation)writeSync(1,'ASSIGNMENT_BROWSER_OBSERVATION '+JSON.stringify(lastObservation)+'\n')
 if(frames.length)writeSync(1,'ASSIGNMENT_BROWSER_FRAMES '+JSON.stringify({frames})+'\n')
 process.exit(1)
}
process.on('uncaughtException',fail);process.on('unhandledRejection',fail)
const mutating=request=>!['GET','HEAD','OPTIONS'].includes(request.method())
const historyPath=id=>{check(uuid(id),'OWNED_ACCOUNT_ID_REQUIRED');return '/admin/users/'+id+'/communications'}
const expectedItems=(offset=0,limit=10)=>facts.signup_items.slice(offset,offset+limit)
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
  if(/\/assignment-history$|\/communications$/.test(path))check(/no-store/i.test(response.headers()['cache-control']??''),'PRIVATE_NO_STORE_REQUIRED')
  if(boundary)boundaries++
  return await response.json()
 }finally{await response.dispose()}
}
async function contextFor(locale){
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,timezoneId:'UTC',serviceWorkers:'block'})
 contexts.push(context);contextLocales.set(context,locale);uiReads.set(context,new Map());context.setDefaultTimeout(15000);context.setDefaultNavigationTimeout(20000)
 context.on('page',page=>{page.on('pageerror',()=>pageErrors++);page.on('request',request=>{const url=new URL(request.url());if(url.origin===API&&/^\/admin\/(?:users|leads)\/[a-f0-9-]+\/(?:communications|assignment-history)$/.test(url.pathname)){const seen=uiReads.get(context);seen.set(url.pathname,(seen.get(url.pathname)??0)+1)}})})
 context.on('request',request=>{const url=new URL(request.url());if(url.origin===API&&request.method()==='PUT'&&/\/(assign|assign-realtor)$/.test(url.pathname)){assignmentRequests++;const body=request.postDataJSON();check(uuid(body.request_id),'ACTUAL_REQUEST_UUID_REQUIRED');requestIds.add(body.request_id)}if([API,DASH,LANDING].includes(url.origin)&&mutating(request)&&!(url.origin===API&&(request.method()==='POST'&&['/auth/login','/__ci/control'].includes(url.pathname)||request.method()==='PUT'&&/^\/admin\/(?:users|leads)\/[a-f0-9-]{36}\/(?:assign|assign-realtor)$/.test(url.pathname))))unexpectedMutations++;if(/google-analytics|googletagmanager|facebook\.com\/tr|doubleclick/.test(url.href))trackingRequests++})
 await context.route('**/*',route=>{const request=route.request(),url=new URL(request.url());if(![API,DASH,LANDING].includes(url.origin)){blockedExternal++;if(mutating(request))externalWrites++;return route.abort('blockedbyclient')}return route.continue()})
 await context.addInitScript(({locale,owned})=>{if(!owned.includes(location.origin))return;localStorage.setItem('ildr_lang',locale);localStorage.setItem('ildr_measurement_consent_v1',JSON.stringify({choice:'denied',savedAt:Date.now()}));const before=navigator.onLine;if(!before)Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>true});Object.defineProperty(window,'__assignmentBrowser',{value:{before,modeled:!before}})}, {locale,owned:[DASH,LANDING]})
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
  const value=await page.evaluate(()=>({before:window.__assignmentBrowser.before,modeled:window.__assignmentBrowser.modeled,after:navigator.onLine,visible:document.visibilityState==='visible'}))
  check(Object.values(value).every(item=>typeof item==='boolean')&&value.modeled===!value.before&&value.after&&value.visible,'OWNED_LOOPBACK_PRESENCE_REQUIRED')
  console.log('ASSIGNMENT_BROWSER_PRESENCE '+JSON.stringify({locale:contextLocales.get(page.context()),page:presence===0?'main':'later',...value}));presenceSeen.add(page);presence++
 }
}
function controlCensus(boxes,width){
 try{
  if(!Array.isArray(boxes)||boxes.length>1000000||boxes.some((box,index)=>box.ordinal!==index||!Number.isInteger(box.tagCode)||box.tagCode<0||box.tagCode>3||![box.left,box.right,box.width].every(Number.isFinite)||box.width<0||box.left>box.right))return
  const outside=boxes.filter(box=>box.left < -1||box.right > width+1||box.width > width+1)
  if(boxes.length<=200&&outside.length===0)return
  const limit=value=>Math.max(-100000,Math.min(100000,value))
  const rows=outside.slice(0,8).map(box=>{
   if(!Number.isInteger(box.ordinal)||box.ordinal<0||box.ordinal>=boxes.length||!Number.isInteger(box.tagCode)||box.tagCode<0||box.tagCode>3||![box.left,box.right,box.width].every(Number.isFinite)||box.width<0)throw 0
   return {ordinal:box.ordinal,tagCode:box.tagCode,left:limit(Math.floor(box.left)),right:limit(Math.ceil(box.right)),width:Math.min(100000,Math.ceil(box.width))}
  })
  const value={positive_count:boxes.length,outside_count:outside.length,rows},raw=JSON.stringify(value)
  if(Buffer.byteLength(raw,'utf8')<=2048)writeSync(1,'ASSIGNMENT_BROWSER_CONTROL_CENSUS '+raw+'\n')
 }catch{}
}
async function geometry(page,scope,label){
 for(const width of [320,390]){
  advance(label+'_geometry_'+width);await page.setViewportSize({width,height:844});await foreground(page)
  const value=await page.evaluate(()=>({width:innerWidth,document:document.documentElement.scrollWidth}))
  check(value.width===width&&value.document<=width+1,'VIEWPORT_OVERFLOW')
  const boxes=await scope.locator('input,select,textarea,button').evaluateAll(nodes=>nodes.filter(node=>{const r=node.getBoundingClientRect();return r.width>0&&r.height>0}).map((node,ordinal)=>{const r=node.getBoundingClientRect();return {ordinal,tagCode:{INPUT:0,SELECT:1,TEXTAREA:2,BUTTON:3}[node.tagName],left:r.left,right:r.right,width:r.width}}))
  if(!(boxes.length<=200&&boxes.every(box=>box.left>=-1&&box.right<=width+1&&box.width<=width+1)))controlCensus(boxes,width)
  check(boxes.length<=200&&boxes.every(box=>box.left>=-1&&box.right<=width+1&&box.width<=width+1),'CONTROL_OVERFLOW')
  check(!geometries.some(row=>row.label===label&&row.width===width),'UNIQUE_GEOMETRY_REQUIRED')
  const record={label,width,document:value.document};geometries.push(record);console.log('ASSIGNMENT_BROWSER_GEOMETRY '+JSON.stringify(record))
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
 for(const forbidden of [...facts.signup_items.map(item=>item.id),...facts.private_markers])check(!text.includes(forbidden),'NO_PRIVATE_CONTENT_OR_MACHINE_REFERENCE_REQUIRED')
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
const assignmentPath=(scope,id)=>'/admin/'+(scope==='lead'?'leads':'users')+'/'+id+'/assignment-history'
const mutationPath=(scope,id)=>'/admin/'+(scope==='lead'?'leads':'users')+'/'+id+'/'+(scope==='lead'?'assign':'assign-realtor')
const assignment=(page,copy)=>page.getByRole('region',{name:t(copy,'assignment.title'),exact:true})
const assignmentHistory=(page,copy)=>page.getByRole('region',{name:t(copy,'assignment.history_title'),exact:true})
const phaseNames=['real_sessions_boundaries','lazy_keyboard_entry','loading_paging','history_hide_abort','target_close_abort',
 'uncertain_commit','later_assignment_old_replay','saved_read_recovery','agent_refusal','account_no_email','valuation_notice',
 'seller_promotion','signup_paging','signup_untracked','signup_error_recovery','auth_loss_clear']
function step(name){advance(facts.locale+'_'+name)}
function completed(name){check(!journeys.includes(name),'UNIQUE_JOURNEY_REQUIRED');journeys.push(name)}
async function control(context,action){
 const response=await context.request.post(API+'/__ci/control',{data:{action},headers:{Origin:DASH,'x-ci-control':facts.control_secret},timeout:15000,maxRedirects:0})
 try{lastObservation={kind:'control',status:response.status()};check(response.status()===200&&JSON.stringify(await response.json())==='{"status":"PASS"}','EXACT_OWNED_CONTROL_REQUIRED');controlTransitions++}finally{await response.dispose()}
}
async function taggedBoundaries(sessions,anonymous){
 for(const scope of ['lead','account']){
  const id=scope==='lead'?facts.leads.main.id:facts.ids.owner,path=assignmentPath(scope,id)
  const admin=await read(sessions.admin,path,200,scope+'_admin');check(admin.limit===10&&admin.offset===0,'UNCHANGED_DEFAULT10_REQUIRED')
  await read(anonymous,path,401,scope+'_anonymous')
  for(const actor of ['buyer','owner','realtor'])await read(sessions[actor],path,403,scope+'_'+actor)
  await read(sessions.admin,assignmentPath(scope,facts.unknown_id),404,scope+'_unknown')
  const malformed=await read(sessions.admin,assignmentPath(scope,facts.invalid_id),422,scope+'_invalid')
  check(!JSON.stringify(malformed).includes(facts.invalid_id),'INVALID_INPUT_NOT_ECHOED_REQUIRED')
  await read(sessions.admin,path+(scope==='lead'?'?limit=51':'?offset=10001'),422,scope+'_bounds')
 }
}
async function openLead(page,copy,key){
 await page.goto(DASH+'/leads?workspace=admin&openId='+facts.leads[key].id,{waitUntil:'domcontentloaded'});await foreground(page)
 const selected=await panel(page),region=assignment(page,copy);await region.waitFor({state:'visible'})
 check(await selected.getByText(facts.leads[key].name,{exact:true}).count()>=1,'ACTUAL_OWNED_LEAD_PANEL_REQUIRED')
 return region
}
async function usersPage(page,copy){
 await page.goto(DASH+'/users?workspace=admin',{waitUntil:'domcontentloaded'});await foreground(page)
 await page.getByPlaceholder(copy.admin.users_page.search_ph,{exact:true}).waitFor({state:'visible'})
}
async function choose(region,copy,agent){
 const select=region.locator('select');check(await select.count()===1&&await select.isEnabled(),'ACTUAL_ENABLED_AGENT_SELECT_REQUIRED')
 await select.selectOption(agent??'');check(await select.inputValue()===(agent??''),'ACTUAL_AGENT_SELECTION_REQUIRED')
}
async function sendSave(page,copy,region,scope,target,expected=204,{keyboard=false,confirm=false}={}){
 const path=mutationPath(scope,target)
 const response=page.waitForResponse(response=>new URL(response.url()).origin===API&&new URL(response.url()).pathname===path&&response.request().method()==='PUT'&&response.status()===expected)
 if(keyboard){await button(region,t(copy,'assignment.save')).focus();check(await button(region,t(copy,'assignment.save')).evaluate(node=>node===document.activeElement),'KEYBOARD_FOCUS_REQUIRED');await page.keyboard.press('Enter');keyboardChecks++}
 else await button(region,t(copy,'assignment.save')).click()
 if(confirm)await button(page,copy.admin.lead_panel.grant_access_confirm).click()
 const actual=await response;lastObservation={kind:'save',status:actual.status()};await actual.finished()
 return actual
}
async function currentName(region,copy,actor){
 await region.getByRole('status').filter({hasText:t(copy,'assignment.saved')}).waitFor({state:'visible'})
 check(await region.locator('select').inputValue()===facts.ids[actor]&&await region.getByText(facts.names[actor],{exact:true}).count()>=1,'AUTHORITATIVE_CURRENT_AGENT_REQUIRED')
}
async function openedAssignmentHistory(page,copy){
 const region=assignment(page,copy)
 await button(region,t(copy,'assignment.open_history')).click()
 const history=assignmentHistory(page,copy);await history.waitFor({state:'visible'});return history
}
async function assignmentRows(region,copy,items){
 const outer=region.locator('ol > li');await outer.first().waitFor({state:items.length?'visible':'hidden'})
 check(await outer.count()===items.length,'ASSIGNMENT_VISIBLE_PAGE_LENGTH_REQUIRED')
 for(let index=0;index<items.length;index++){
  const expected=items[index],row=outer.nth(index)
  await row.getByText(t(copy,'assignment.actions.'+expected.action),{exact:true}).waitFor({state:'visible'})
  const actualNotices=row.locator('ul > li');check(await actualNotices.count()===expected.notices.length,'EXACT_VISIBLE_NOTICE_COUNT_REQUIRED')
  const date=await row.evaluate((_node,{locale,value})=>new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)),{locale:facts.locale==='en'?'en-US':'es-DO',value:expected.created_at})
  check((await row.innerText()).includes(date),'ASSIGNMENT_ACTUAL_DATE_REQUIRED')
  for(let number=0;number<expected.notices.length;number++){
   const notice=expected.notices[number],display=actualNotices.nth(number)
   await display.getByText(t(copy,'assignment.kinds.'+notice.kind),{exact:true}).waitFor({state:'visible'})
   await display.getByText(t(copy,'assignment.statuses.'+notice.status),{exact:true}).waitFor({state:'visible'})
   check(await display.locator('dd').first().innerText()===String(notice.attempts),'ACTUAL_RECORDED_ATTEMPTS_REQUIRED')
   for(const [key,position] of [['created_at',1],['attempted_at',2],['accepted_at',3]]){
    if(key==='accepted_at'&&notice[key]===null)continue
    const expectedDate=notice[key]===null?t(copy,'not_recorded'):await display.evaluate((_node,{locale,value})=>new Intl.DateTimeFormat(locale,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)),{locale:facts.locale==='es'?'es-DO':'en-US',value:notice[key]})
    check(await display.locator('dd').nth(position).innerText()===expectedDate,'ACTUAL_NOTICE_DATES_REQUIRED')
   }
   if(notice.outcome_uncertain)await display.getByText(t(copy,'assignment.email_uncertain'),{exact:true}).waitFor({state:'visible'})
  }
  if(!expected.notices.length)await row.getByText(t(copy,'assignment.no_email'),{exact:true}).waitFor({state:'visible'})
 }
 await region.getByText(t(copy,'assignment.accepted_note'),{exact:true}).waitFor({state:'visible'})
 const text=await region.innerText()
 for(const forbidden of [...items.map(item=>item.id),...facts.private_markers])check(!text.includes(forbidden),'ASSIGNMENT_METADATA_ONLY_NO_IDENTIFIERS_REQUIRED')
 check(await region.locator('a,form,code').count()===0&&await button(region,t(copy,'retry')).count()===0,'ASSIGNMENT_HISTORY_NO_DELIVERY_ACTION_REQUIRED')
}
async function waitCanceled(page,pending,action){
 const canceled=page.waitForEvent('requestfailed',{predicate:request=>request===pending})
 await action();await canceled;check(!!pending.failure(),'CLIENT_UNMOUNT_ABORTS_OWNED_REQUEST_REQUIRED')
}
async function assignmentJourney(copy,sessions,page){
 const context=sessions.admin,main=facts.leads.main.id,path=assignmentPath('lead',main)
 step('lazy_keyboard_entry')
 let region=await openLead(page,copy,'main')
 check(await assignmentHistory(page,copy).count()===0&&!uiReads.get(context).get(path),'HISTORY_LAZY_NO_READ_BEFORE_OPEN_REQUIRED')
 await region.locator('input[type="search"]').fill(facts.names.realtor)
 await choose(region,copy,facts.ids.realtor)
 await geometry(page,region,facts.locale+'_lead_controls');completed('lazy_keyboard_entry')

 step('loading_paging')
 const gate=await hold(context,path)
 await button(region,t(copy,'assignment.open_history')).click();await gate.captured
 let history=assignmentHistory(page,copy);await history.getByText(t(copy,'assignment.loading'),{exact:true}).waitFor({state:'visible'})
 check(await history.getAttribute('aria-busy')==='true','ASSIGNMENT_ACTUAL_LOADING_REQUIRED')
 await geometry(page,history,facts.locale+'_assignment_loading')
 const loaded=page.waitForResponse(response=>new URL(response.url()).pathname===path&&response.status()===200)
 await gate.release();const load=await loaded;await load.finished();await gate.remove()
 const report=await load.json();check(report.total===11&&report.items.length===10&&JSON.stringify(report.items.map(row=>row.id))===JSON.stringify(facts.seed_assignment_items.slice(0,10).map(row=>row.id)),'SEED_ACTUAL_STORED_PAGING_REQUIRED')
 await assignmentRows(history,copy,facts.seed_assignment_items.slice(0,10))
 await geometry(page,history,facts.locale+'_assignment_history')
 const second=await responseFor(page,path,200,()=>button(history,t(copy,'next')).click(),{limit:10,offset:10})
 check((await second.json()).items.length===1,'ASSIGNMENT_REAL_SECOND_PAGE_REQUIRED');await assignmentRows(history,copy,facts.seed_assignment_items.slice(10));assignmentPageChecks++
 await responseFor(page,path,200,()=>button(history,t(copy,'previous')).click(),{limit:10,offset:0});await assignmentRows(history,copy,facts.seed_assignment_items.slice(0,10));assignmentPageChecks++;completed('loading_paging')

 step('history_hide_abort')
 const hidden=await hold(context,path);await button(history,t(copy,'refresh')).click();await hidden.captured
 await waitCanceled(page,hidden.request(),()=>button(region,t(copy,'assignment.hide_history')).click())
 check(await assignmentHistory(page,copy).count()===0,'HIDE_REMOVES_CACHED_HISTORY_REQUIRED');await hidden.release();await hidden.remove();staleCancellations++;completed('history_hide_abort')

 step('target_close_abort')
 history=await openedAssignmentHistory(page,copy);await assignmentRows(history,copy,facts.seed_assignment_items.slice(0,10))
 const stale=await hold(context,path);await button(history,t(copy,'refresh')).click();await stale.captured
 await waitCanceled(page,stale.request(),()=>closePanel(page))
 region=await openLead(page,copy,'empty');history=await openedAssignmentHistory(page,copy)
 await history.getByText(t(copy,'assignment.untracked'),{exact:true}).waitFor({state:'visible'})
 await stale.release();await stale.remove();check(await history.locator('ol > li').count()===0,'OLD_TARGET_RESPONSE_CANNOT_POPULATE_NEW_TARGET');staleCancellations++;completed('target_close_abort')

 step('uncertain_commit')
 region=await openLead(page,copy,'main');await choose(region,copy,facts.ids.realtor)
 let committedResolve,committedReject,abortComplete=false,firstBody
 const committed=new Promise((resolve,reject)=>{committedResolve=resolve;committedReject=reject})
 const mutation=mutationPath('lead',main)
 const lose=async route=>{
  if(route.request().method()!=='PUT'||new URL(route.request().url()).pathname!==mutation)return route.fallback()
  try{
   firstBody=route.request().postDataJSON();const response=await route.fetch({maxRetries:0,maxRedirects:0,timeout:20000})
   try{lastObservation={kind:'save',status:response.status()};check(response.status()===204&&(await response.body()).length===0,'REAL_COMMITTED204_BEFORE_ABORT_REQUIRED')}finally{await response.dispose()}
   await route.abort('connectionfailed');abortComplete=true;committedResolve()
  }catch(error){committedReject(error);throw error}
 }
 await context.route('**/*',lose)
 await button(region,t(copy,'assignment.save')).focus();await page.keyboard.press('Enter');keyboardChecks++
 await committed;await button(region,t(copy,'assignment.resolve')).waitFor({state:'visible'})
 check(abortComplete&&await button(region,t(copy,'assignment.resolve')).isEnabled()&&await region.locator('select').isDisabled()&&await button(region,t(copy,'assignment.save')).isDisabled(),'ACTUAL_UNCERTAIN_FREEZE_REQUIRED')
 await context.unroute('**/*',lose);uncertainCommits++
 await geometry(page,region,facts.locale+'_assignment_uncertain');completed('uncertain_commit')

 step('later_assignment_old_replay')
 const other=await context.newPage();let otherRegion=await openLead(other,copy,'main')
 await choose(otherRegion,copy,facts.ids.admin);await sendSave(other,copy,otherRegion,'lead',main);await currentName(otherRegion,copy,'admin');await other.close()
 await page.bringToFront()
 const replayResponse=page.waitForResponse(response=>new URL(response.url()).origin===API&&new URL(response.url()).pathname===mutation&&response.request().method()==='PUT'&&response.status()===204)
 await button(region,t(copy,'assignment.resolve')).click();const replay=await replayResponse;await replay.finished()
 check(JSON.stringify(replay.request().postDataJSON())===JSON.stringify(firstBody),'EXACT_UUID_BODY_REPLAY_REQUIRED');assignmentReplay++
 await currentName(region,copy,'admin');completed('later_assignment_old_replay')

 step('saved_read_recovery')
 await choose(region,copy,facts.ids.realtor)
 const readLoss=await hold(context,'/admin/leads','abort')
 await sendSave(page,copy,region,'lead',main);await readLoss.captured
 await region.getByRole('alert').filter({hasText:t(copy,'assignment.saved_read_error')}).waitFor({state:'visible'})
 check(await region.locator('select').isDisabled()&&await button(region,t(copy,'assignment.save')).isDisabled()&&await button(region,t(copy,'assignment.resolve')).count()===0,'KNOWN204_GET_ONLY_LOCK_REQUIRED')
 await readLoss.release();await readLoss.remove();const requestsBefore=assignmentRequests
 await geometry(page,region,facts.locale+'_assignment_current_error')
 await button(region,t(copy,'assignment.refresh_current')).click();await currentName(region,copy,'realtor')
 check(assignmentRequests===requestsBefore,'REFRESH_CURRENT_MUST_NOT_REPEAT_PUT');knownReadRecoveries++;completed('saved_read_recovery')

 step('agent_refusal')
 history=await openedAssignmentHistory(page,copy);await history.locator('ol > li').first().waitFor({state:'visible'})
 await control(context,'agent_unverified');await sendSave(page,copy,region,'lead',main,404)
 await region.getByRole('alert').filter({hasText:t(copy,'assignment.refused')}).waitFor({state:'visible'})
 check(await region.getByText(t(copy,'assignment.access_lost'),{exact:true}).count()===0&&await region.locator('select').isEnabled()
  &&await region.locator('select').inputValue()===facts.ids.realtor,'BAD_AGENT404_RETAINS_VALID_CURRENT_TARGET_REQUIRED')
 await history.locator('ol > li').first().waitFor({state:'visible'});await control(context,'agent_verified');refusalChecks++
 await geometry(page,region,facts.locale+'_assignment_refused');completed('agent_refusal')

 step('account_no_email')
 await usersPage(page,copy);await openUser(page,copy,'owner');region=assignment(page,copy)
 await choose(region,copy,facts.ids.realtor);await sendSave(page,copy,region,'account',facts.ids.owner);await currentName(region,copy,'realtor')
 await choose(region,copy,null);await sendSave(page,copy,region,'account',facts.ids.owner)
 await region.getByRole('status').filter({hasText:t(copy,'assignment.saved')}).waitFor({state:'visible'})
 check(await region.locator('select').inputValue()==='','ACTUAL_ACCOUNT_CLEAR_REQUIRED')
 history=await openedAssignmentHistory(page,copy);await history.locator('ol > li').first().waitFor({state:'visible'})
 check(await history.locator('ol > li').count()===2&&await history.getByText(t(copy,'assignment.no_email'),{exact:true}).count()===2,'TWO_ACCOUNT_ACTIONS_EXPLICIT_ZERO_EMAIL_REQUIRED');accountNoEmail+=2
 await geometry(page,history,facts.locale+'_account_no_email');completed('account_no_email')

 step('valuation_notice')
 region=await openLead(page,copy,'valuation');await choose(region,copy,facts.ids.realtor)
 await sendSave(page,copy,region,'lead',facts.leads.valuation.id);await currentName(region,copy,'realtor')
 history=await openedAssignmentHistory(page,copy);await history.locator('ul > li').first().waitFor({state:'visible'})
 check(await history.locator('ul > li').count()===1,'VALUATION_EXACT_ONE_NOTICE_REQUIRED');valuationNotices++
 const buyerBefore=await read(context,'/admin/users');check(buyerBefore.find(row=>row.id===facts.ids.buyer)?.role==='buyer','VALUATION_DOES_NOT_PROMOTE_BUYER_REQUIRED');completed('valuation_notice')

 step('seller_promotion')
 region=await openLead(page,copy,'seller');await choose(region,copy,facts.ids.realtor)
 await sendSave(page,copy,region,'lead',facts.leads.seller.id,204,{confirm:true});await currentName(region,copy,'realtor')
 history=await openedAssignmentHistory(page,copy);await history.locator('ul > li').first().waitFor({state:'visible'})
 check(await history.locator('ul > li').count()===3,'SELLER_EXACT_THREE_NOTICE_KINDS_REQUIRED')
 for(const kind of ['lead_assigned_agent','owner_upgrade_customer','owner_assigned_agent'])await history.getByText(t(copy,'assignment.kinds.'+kind),{exact:true}).waitFor({state:'visible'})
 const currentUsers=await read(context,'/admin/users'),promoted=currentUsers.find(row=>row.id===facts.ids.buyer)
 check(promoted?.role==='owner'&&promoted.assigned_realtor_id===facts.ids.realtor,'ACTUAL_OWNER_PROMOTION_MIRROR_REQUIRED');sellerNotices+=3
 await geometry(page,history,facts.locale+'_seller_three_notices');completed('seller_promotion')
}
async function signupJourney(copy,context,page){
 step('signup_paging');await usersPage(page,copy);await geometry(page,page.locator('main'),facts.locale+'_users')
 await openUser(page,copy,'buyer')
 const path=historyPath(facts.ids.buyer),gate=await hold(context,path),selected=await panel(page)
 await button(selected,t(copy,'account.open')).click();await gate.captured
 let region=await history(page,copy);await region.getByText(t(copy,'account.loading'),{exact:true}).waitFor({state:'visible'})
 await geometry(page,region,facts.locale+'_signup_loading')
 const ready=page.waitForResponse(response=>new URL(response.url()).pathname===path&&response.status()===200)
 await gate.release();const first=await ready;await first.finished();await gate.remove();metadata(await first.json(),0,10)
 await visibleRows(region,copy,expectedItems(),facts.locale);await geometry(page,region,facts.locale+'_signup_tracked')
 let response=await responseFor(page,path,200,()=>button(region,t(copy,'next')).click(),{limit:10,offset:10})
 metadata(await response.json(),10,10);await visibleRows(region,copy,expectedItems(10),facts.locale);uiPagingChecks++
 response=await responseFor(page,path,200,()=>button(region,t(copy,'previous')).click(),{limit:10,offset:0});metadata(await response.json(),0,10)
 await visibleRows(region,copy,expectedItems(),facts.locale);uiPagingChecks++;completed('signup_paging')
 step('signup_untracked');await closePanel(page);await openUser(page,copy,'owner');({region}=await openHistory(page,copy,'owner'))
 await region.getByText(t(copy,'account.untracked'),{exact:true}).waitFor({state:'visible'});check(await region.getByRole('listitem').count()===0,'UNCHANGED_UNTRACKED_SIGNUP_REQUIRED')
 await geometry(page,region,facts.locale+'_signup_untracked');untrackedAccounts++;completed('signup_untracked')
 step('signup_error_recovery');await closePanel(page);await openUser(page,copy,'buyer');({region}=await openHistory(page,copy,'buyer'));await visibleRows(region,copy,expectedItems(),facts.locale)
 const broken=await hold(context,path,'abort');await button(region,t(copy,'refresh')).click();await broken.captured
 await region.getByText(t(copy,'account.load_error'),{exact:true}).waitFor({state:'visible'});check(await region.getByRole('listitem').count()===0,'UNCHANGED_SIGNUP_ERROR_CLEARS_ROWS_REQUIRED')
 await geometry(page,region,facts.locale+'_signup_error');await broken.release();await broken.remove()
 await responseFor(page,path,200,()=>button(region,t(copy,'refresh')).click(),{limit:10,offset:0});await visibleRows(region,copy,expectedItems(),facts.locale);currentReadErrors++;recoveries++;completed('signup_error_recovery')
}
async function run(){
 advance('fixture')
 const filename=process.env.ILDR_BROWSER_FIXTURES,info=await lstat(filename)
 check(info.isFile()&&!info.isSymbolicLink()&&info.uid===process.getuid()&&(info.mode&0o077)===0&&info.size>0&&info.size<=131072,'OWNED_BOUNDED_FIXTURE_REQUIRED')
 const prepared=JSON.parse(await readFile(filename,'utf8'));check(prepared.scope==='assignment-history-only'&&prepared.accounts&&prepared.facts,'EXACT_FIXTURE_SCOPE_REQUIRED');({accounts,facts}=prepared)
 facts.ordered_items=facts.signup_items
 check(['en','es'].includes(facts.locale)&&Object.keys(accounts).sort().join(',')===actors.toSorted().join(',')&&actors.every(actor=>uuid(facts.ids[actor])&&facts.roles[actor]===actor),'FOUR_SYNTHETIC_ROLES_REQUIRED')
 check(facts.signup_items.length===11&&facts.seed_assignment_items.length===11&&Object.values(facts.leads).length===4&&Object.values(facts.leads).every(row=>uuid(row.id)),'SOURCE_BOUND_PRIVATE_SEED_FACTS_REQUIRED')
 const copy=JSON.parse(await readFile(new URL('./'+facts.locale+'.json',import.meta.url),'utf8')),sessions={}
 browser=await chromium.launch({headless:true})
 try{
  step('real_sessions_boundaries')
  for(const actor of actors)sessions[actor]=await contextFor(facts.locale)
  const anonymous=await contextFor(facts.locale),page=await sessions.admin.newPage()
  for(const actor of ['buyer','owner','realtor'])await apiLogin(sessions[actor],actor)
  await page.goto(LANDING+'/login/',{waitUntil:'domcontentloaded'});await foreground(page)
  await page.locator('input[type="email"]').fill(accounts.admin.email);await page.locator('input[type="password"]').fill(accounts.admin.password)
  const signed=page.waitForResponse(response=>new URL(response.url()).pathname==='/auth/login'&&response.request().method()==='POST'&&response.status()===200)
  await button(page,copy.auth.login.form.submit).click();await signed;await page.waitForURL(url=>url.origin===DASH)
  const me=await read(sessions.admin,'/auth/me');check(me.id===facts.ids.admin&&me.role==='admin','ACTUAL_ADMIN_UI_LOGIN_REQUIRED');actualLogins++
  await taggedBoundaries(sessions,anonymous);completed('real_sessions_boundaries')
  await assignmentJourney(copy,sessions,page)
  await signupJourney(copy,sessions.admin,page)
  step('auth_loss_clear')
  const region=await openLead(page,copy,'main'),history=await openedAssignmentHistory(page,copy)
  await history.locator('ol > li').first().waitFor({state:'visible'});await sessions.admin.clearCookies()
  await responseFor(page,assignmentPath('lead',facts.leads.main.id),401,()=>button(history,t(copy,'refresh')).click(),{limit:10,offset:0})
  await region.getByRole('alert').filter({hasText:t(copy,'assignment.access_lost')}).waitFor({state:'visible'})
  check(await assignmentHistory(page,copy).count()===0&&await region.locator('select,ol').count()===0,'ACTUAL_AUTH_LOSS_CLEARS_CURRENT_HISTORY_REQUIRED');authLossChecks++;completed('auth_loss_clear')
  advance('final')
  check(JSON.stringify(journeys)===JSON.stringify(phaseNames),'ALL_SIXTEEN_ORDERED_JOURNEYS_REQUIRED')
  check(geometries.length===26&&contexts.length===5&&actualLogins===4&&presence===2&&boundaries===16,'EXACT_SESSION_GEOMETRY_BOUNDARY_COUNTS_REQUIRED')
  check(assignmentRequests===9&&requestIds.size===8&&controlTransitions===2&&assignmentReplay===1&&uncertainCommits===1&&refusalChecks===1&&knownReadRecoveries===1&&accountNoEmail===2&&valuationNotices===1&&sellerNotices===3,'EXACT_ASSIGNMENT_ACTION_COUNTS_REQUIRED')
  check(assignmentPageChecks===2&&uiPagingChecks===2&&staleCancellations===2&&recoveries===1&&authLossChecks===1&&keyboardChecks===1,'EXACT_PAGING_RECOVERY_KEYBOARD_COUNTS_REQUIRED')
  check(pageErrors===0&&unexpectedMutations===0&&externalWrites===0&&trackingRequests===0&&blockedExternal<=999,'OWNED_BROWSER_ISOLATION_REQUIRED')
  for(const context of contexts){await context.close();closed.add(context)}check(browser.contexts().length===0&&closed.size===5,'ALL_LOCALE_CONTEXTS_CLOSED_REQUIRED')
  await browser.close();browser=null;clearTimeout(watchdog)
  console.log('ASSIGNMENT_BROWSER_RESULT '+JSON.stringify({status:'PASS',locale:facts.locale,journeys:16,geometry:26,contexts:5,presence:2,actual_logins:4,
   assignment_requests:9,successful_responses:8,unique_request_ids:8,persisted_assignment_intents:7,exact_replays:1,uncertain_commits:1,agent_refusals:1,known204_get_only_recoveries:1,
   account_no_email_events:2,valuation_notices:1,seller_notices:3,control_transitions:2,api_boundary_checks:16,assignment_ui_page_checks:2,signup_ui_page_checks:2,
   canceled_history_reads:2,signup_read_recoveries:1,auth_loss_checks:1,keyboard_saves:1,closed_contexts:5,page_errors:0,unexpected_mutations:0,external_writes:0,
   tracking_requests:0,blocked_external_requests:blockedExternal}))
 }catch(error){fail(error)}finally{clearTimeout(watchdog);if(browser){for(const context of contexts)if(!closed.has(context))await context.close().catch(()=>{});await browser.close()}}
}
run().catch(fail)
