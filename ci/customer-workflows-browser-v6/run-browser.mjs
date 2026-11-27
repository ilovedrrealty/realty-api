import { lstat, readFile } from 'node:fs/promises'
import { writeSync } from 'node:fs'
import { chromium } from 'playwright-core'

const API='http://127.0.0.1:8918',DASH='http://127.0.0.1:4175',LANDING='http://127.0.0.1:8917'
const actors=['admin','buyer','owner','realtor'],contexts=[],closed=new Set(),journeys=[],geometries=[],presenceSeen=new WeakSet(),uiReads=new WeakMap(),contextLocales=new WeakMap()
let browser,facts,accounts,stage='fixture',watchdog,failed=false,lastObservation=null,publicCheckpoint=null
const requestIds=new Set()
// New workflows are measured separately: the accepted sixteen-journey ledger,
// its twenty-six geometries and all assignment counters remain binding below.
const workflowJourneys=[],workflowGeometries=[],workflowRoles=new WeakMap()
let inWorkflow=false,workflowPresence=0,workflowControls=0,profileUIRequests=0,profileLegacyRequests=0
let workflowReads=0,workflowBoundaries=0,profileSuccesses=0,profileConflicts=0,profileDenials=0,directoryParity=0,directoryNavigation=0,adminDraftFilterChecks=0,manualTargetResets=0,workflowKeyboardActions=0
let workflowDenials=0,retiredReads=0,manualRequests=0,manualCopies=0,manualCopyRefusals=0,manualDraftChecks=0,manualResets=0,manualPublicChecks=0
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
 writeSync(1,'CUSTOMER_WORKFLOWS_BROWSER_FAILURE '+stage+' '+reason+'\n')
  if(/^(en|es)_workflow_manual_public_link$/.test(stage)&&publicCheckpoint){try{const record=JSON.stringify(publicCheckpoint);if(Buffer.byteLength(record)<=1024)writeSync(1,'CUSTOMER_WORKFLOWS_BROWSER_PUBLIC_CHECKPOINT '+record+'\n')}catch{}}
 if(lastObservation)writeSync(1,'CUSTOMER_WORKFLOWS_BROWSER_OBSERVATION '+JSON.stringify(lastObservation)+'\n')
 if(frames.length)writeSync(1,'CUSTOMER_WORKFLOWS_BROWSER_FRAMES '+JSON.stringify({frames})+'\n')
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
 await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:DASH})
 contexts.push(context);contextLocales.set(context,locale);uiReads.set(context,new Map());context.setDefaultTimeout(15000);context.setDefaultNavigationTimeout(20000)
 context.on('page',page=>{page.on('pageerror',()=>pageErrors++);page.on('request',request=>{const url=new URL(request.url());if(url.origin===API&&/^\/admin\/(?:users|leads)\/[a-f0-9-]+\/(?:communications|assignment-history)$/.test(url.pathname)){const seen=uiReads.get(context);seen.set(url.pathname,(seen.get(url.pathname)??0)+1)}})})
 context.on('request',request=>{const url=new URL(request.url());if(url.origin===API&&request.method()==='PUT'&&/\/(assign|assign-realtor)$/.test(url.pathname)){assignmentRequests++;const body=request.postDataJSON();check(uuid(body.request_id),'ACTUAL_REQUEST_UUID_REQUIRED');requestIds.add(body.request_id)}
  const profileWrite=url.origin===API&&request.method()==='PUT'&&(url.pathname==='/professional-profile'||url.pathname==='/admin/professionals/'+facts.workflows.profiles.managed.id)
  if(profileWrite){check(inWorkflow,'PROFILE_WRITES_ONLY_IN_WORKFLOW_REQUIRED');if(!request.headers()['x-ci-workflow-legacy'])profileUIRequests++}
  if([API,DASH,LANDING].includes(url.origin)&&mutating(request)&&!(url.origin===API&&(request.method()==='POST'&&['/auth/login','/__ci/control','/__ci/workflow-control'].includes(url.pathname)||request.method()==='PUT'&&/^\/admin\/(?:users|leads)\/[a-f0-9-]{36}\/(?:assign|assign-realtor)$/.test(url.pathname)||profileWrite)))unexpectedMutations++
  if(/google-analytics|googletagmanager|facebook\.com\/tr|doubleclick/.test(url.href))trackingRequests++})
 await context.route('**/*',route=>{const request=route.request(),url=new URL(request.url());if(![API,DASH,LANDING].includes(url.origin)){blockedExternal++;if(mutating(request))externalWrites++;return route.abort('blockedbyclient')}return route.continue()})
 await context.addInitScript(({locale,owned})=>{if(!owned.includes(location.origin))return;localStorage.setItem('ildr_lang',locale);localStorage.setItem('ildr_measurement_consent_v1',JSON.stringify({choice:'denied',savedAt:Date.now()}));const before=navigator.onLine;if(!before)Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>true});Object.defineProperty(window,'__assignmentBrowser',{value:{before,modeled:!before}})
  // Handoffs are captured without another app. Clipboard success uses the owned
  // browser's native clipboard; only one explicitly controlled failure is fake.
  const capture={opens:[],copies:[],rejectClipboard:true};Object.defineProperty(window,'__customerManual',{value:capture})
  window.open=(url,target,features)=>{capture.opens.push({url:String(url),target,features});return null}
  const clipboard=navigator.clipboard,write=clipboard?.writeText.bind(clipboard),read=clipboard?.readText.bind(clipboard)
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{capture.copies.push(String(value));if(capture.rejectClipboard)throw new DOMException('Owned clipboard refusal','NotAllowedError');if(!write)throw new DOMException('Owned clipboard unavailable','NotAllowedError');await write(value)},readText:async()=>{if(!read)throw new DOMException('Owned clipboard unavailable','NotAllowedError');return read()}}})
 }, {locale,owned:[DASH,LANDING]})
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
  if(inWorkflow){check(['owner_directory','owner_editor','realtor_manual'].includes(workflowRoles.get(page)),'FINITE_WORKFLOW_PRESENCE_REQUIRED');console.log('CUSTOMER_WORKFLOWS_EXTRA_PRESENCE '+JSON.stringify({locale:contextLocales.get(page.context()),page:workflowRoles.get(page),...value}));workflowPresence++}
  else{console.log('CUSTOMER_WORKFLOWS_BROWSER_PRESENCE '+JSON.stringify({locale:contextLocales.get(page.context()),page:presence===0?'main':'later',...value}));presence++}
  presenceSeen.add(page)
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
  if(Buffer.byteLength(raw,'utf8')<=2048)writeSync(1,'CUSTOMER_WORKFLOWS_BROWSER_CONTROL_CENSUS '+raw+'\n')
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
  const records=inWorkflow?workflowGeometries:geometries
  check(!records.some(row=>row.label===label&&row.width===width),'UNIQUE_GEOMETRY_REQUIRED')
  const record={label,width,document:value.document};records.push(record);console.log((inWorkflow?'CUSTOMER_WORKFLOWS_EXTRA_GEOMETRY ':'CUSTOMER_WORKFLOWS_BROWSER_GEOMETRY ')+JSON.stringify(record))
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
// Actual controls remain the source of every request. No JSON response is
// fabricated: the only held responses below come from the owned API itself.
const facetFields=['provider_kind_ids','property_class_ids','work_scope_ids']
const facetDimensions=[['provider_kind_ids','provider_kind','provider_kinds'],['property_class_ids','property_class','property_classes'],['work_scope_ids','work_scope','work_scopes']]
const workflowNames=['directory_filters','directory_navigation','own_editor','own_access_loss','managed_editor','admin_access_loss','manual_drafts','manual_handoffs','manual_invalid','manual_current_reset','manual_public_link','manual_cancel','manual_assignment_loss','manual_auth_loss','manual_pipeline']
const p=(copy,key,values={})=>localized(copy.professionals,key,values)
const m=(copy,key)=>localized(copy.admin.manual_follow_up,key)
function localized(copy,key,values={}){const lookup=name=>name.split('.').reduce((part,item)=>part?.[item],copy);const value=(Number.isFinite(values.count)?lookup(key+'_'+new Intl.PluralRules(facts.locale).select(values.count)):undefined)??lookup(key);check(typeof value==='string','WORKFLOW_LOCALIZED_COPY_REQUIRED');return value.replace(/\{\{(\w+)\}\}/g,(_,name)=>String(values[name]??''))}
function workflowStep(name){check(workflowNames.includes(name),'FINITE_WORKFLOW_JOURNEY_REQUIRED');advance(facts.locale+'_workflow_'+name)}
function workflowDone(name){check(!workflowJourneys.includes(name),'UNIQUE_WORKFLOW_JOURNEY_REQUIRED');workflowJourneys.push(name)}
async function workflowControl(context,action){
 const response=await context.request.post(API+'/__ci/workflow-control',{data:{action},headers:{Origin:DASH,'x-ci-control':facts.control_secret},timeout:15000,maxRedirects:0})
 try{check(response.status()===200&&JSON.stringify(await response.json())==='{"status":"PASS"}','EXACT_WORKFLOW_CONTROL_REQUIRED');workflowControls++}finally{await response.dispose()}
}
async function labelledControl(scope,tag,label){
 check(['input','select','textarea'].includes(tag),'FINITE_LABELLED_CONTROL_REQUIRED')
 const controls=scope.locator(tag),matches=await controls.evaluateAll((nodes,wanted)=>nodes.map((node,index)=>({index,labels:[...(node.labels??[])].map(label=>{const clone=label.cloneNode(true);for(const child of clone.querySelectorAll('input,select,textarea'))child.remove();return clone.textContent.replace(/\s+/g,' ').trim()})})).filter(row=>row.labels.includes(wanted)).map(row=>row.index),label.replace(/\s+/g,' ').trim())
 check(matches.length===1,'UNIQUE_ASSOCIATED_LABEL_REQUIRED');return controls.nth(matches[0])
}
async function noPrivate(scope){const text=await scope.innerText();for(const marker of [...facts.private_markers,...Object.values(facts.ids),...Object.values(facts.leads).map(row=>row.id),...Object.values(facts.workflows.manual).map(row=>row.id),...Object.values(facts.workflows.profiles).map(row=>row.draft.business_name)])check(!text.includes(marker),'WORKFLOW_PRIVATE_PROJECTION_REQUIRED')}
async function ownedDialog(page){const scope=page.getByRole('dialog');await scope.waitFor({state:'visible'});check(await scope.count()===1,'UNIQUE_PROFESSIONAL_DIALOG_REQUIRED');return scope}
async function workflowRead(context,path){const result=await read(context,path);workflowReads++;return result}
async function actualGET(page,path,status,action,observe=null,complete=null){
 const waiting=page.waitForResponse(response=>{const url=new URL(response.url()),matched=url.origin===API&&url.pathname===path&&response.request().method()==='GET'&&response.status()===status;if(matched)observe?.('response_received',response.status());return matched})
 observe?.('action_wait');await action();observe?.('response_wait');const result=await waiting
 if(complete){observe?.('body_wait',result.status());await complete(result);observe?.('body_received')}
 else{observe?.('finished_wait',result.status());await result.finished();observe?.('finished_resolved')}
 workflowReads++;return result
}
function publicProgress(primitive,status=null){
 if(!publicCheckpoint)return
 if(primitive!=='response_received')publicCheckpoint.primitive=primitive
 if(status!==null)publicCheckpoint.observed_status=status
 if(primitive==='finished_resolved')publicCheckpoint.finished_resolved=true
 if(primitive==='body_received')publicCheckpoint.body_received=true
}
async function publicWithdrawalBody(response){
 const request=response.request()
 publicProgress('body_headers_wait');const headers=await request.allHeaders();if(publicCheckpoint)publicCheckpoint.headers_obtained=true
 check(request.resourceType()==='fetch'&&!Object.keys(headers).some(name=>name.toLowerCase()==='sec-purpose'),'ACTUAL_PUBLIC_WITHDRAWAL_BODY_REQUIRED')
 publicProgress('body_wait')
 const body=await response.body()
 check(Buffer.isBuffer(body)&&body.length<=2048&&/^[ \t\r\n]*\{[ \t\r\n]*"detail"[ \t\r\n]*:[ \t\r\n]*"Listing not found"[ \t\r\n]*\}[ \t\r\n]*$/.test(body.toString('utf8'))&&(response.headers()['cache-control']??'').split(',').some(part=>part.trim().toLowerCase()==='no-store'),'ACTUAL_PUBLIC_WITHDRAWAL_BODY_REQUIRED')
}
async function publicGET(page,path,status,action){
 publicCheckpoint={ordinal:(publicCheckpoint?.ordinal??0)+1,primitive:'action_wait',expected_status:status,observed_status:null,finished_resolved:false,body_received:false,headers_obtained:false}
 const response=await actualGET(page,path,status,action,publicProgress,status===404?publicWithdrawalBody:null);publicProgress('headers_wait');const headers=await response.request().allHeaders();publicCheckpoint.headers_obtained=true
 check(!Object.hasOwn(headers,'cookie')&&!Object.hasOwn(headers,'authorization'),'PUBLIC_LINK_REQUEST_MUST_OMIT_AUTH_HEADERS_REQUIRED')
 publicProgress('headers_checked')
 return response
}
async function heldActualGET(context,path){
 let ready,release,done,actual,request,used=false
 const captured=new Promise(resolve=>{ready=resolve}),gate=new Promise(resolve=>{release=resolve}),finished=new Promise(resolve=>{done=resolve})
 const handler=async route=>{
  const req=route.request(),url=new URL(req.url());if(used||url.origin!==API||url.pathname!==path||req.method()!=='GET')return route.fallback()
  used=true;request=req
  try{actual=await route.fetch({maxRetries:0,maxRedirects:0,timeout:15000});check(actual.status()===200,'HELD_REAL200_REQUIRED');ready();await gate;if(!request.failure())await route.fulfill({response:actual})}
  catch(error){if(!request?.failure())throw error}
  finally{if(actual)await actual.dispose();done()}
 }
 await context.route('**/*',handler)
 return {captured,request:()=>request,release:async()=>{release();await finished},remove:async()=>context.unroute('**/*',handler)}
}
async function workflowPage(context,identity){const page=await context.newPage();workflowRoles.set(page,identity);return page}
async function workflowEnter(page,control){check(await control.isEnabled(),'ENABLED_WORKFLOW_KEYBOARD_CONTROL_REQUIRED');await control.focus();check(await control.evaluate(node=>node===document.activeElement),'ACTUAL_WORKFLOW_KEYBOARD_FOCUS_REQUIRED');await page.keyboard.press('Enter');workflowKeyboardActions++}
async function version(scope,copy,value){await scope.getByText(p(copy,'version',{version:value}),{exact:true}).waitFor({state:'visible'})}
async function choiceGroup(scope,title){const group=scope.getByRole('group',{name:title,exact:true,includeHidden:true});check(await group.count()===1,'UNIQUE_PROFESSIONAL_CHOICE_GROUP_REQUIRED');if(!await group.isVisible()){check(await group.evaluate(node=>node.parentElement?.tagName==='DETAILS'),'SOURCE_FILTER_DETAILS_REQUIRED');await group.locator('xpath=..').locator(':scope > summary').click()}await group.waitFor({state:'visible'});return group}
async function setFacets(scope,copy,values,{allThenRestore=false}={}){
 const region=scope.getByRole('region',{name:p(copy,'facets.heading'),exact:true});await region.waitFor({state:'visible'})
 for(const [field,,catalog] of facetDimensions){
  const group=await choiceGroup(region,p(copy,'facets.'+catalog)),choices=facts.workflows.options[catalog]
  check(await group.getByRole('checkbox').count()===choices.length,'FULL_KNOWN_FACET_CHOICES_REQUIRED')
  if(allThenRestore){for(const option of choices)await group.getByRole('checkbox',{name:option.label[facts.locale],exact:true}).check();check(await group.locator('input:checked').count()===choices.length&&await group.locator('input:disabled').count()===0,'ALL_KNOWN_FACET_SELECTION_REQUIRED')}
  for(const option of choices)await group.getByRole('checkbox',{name:option.label[facts.locale],exact:true}).uncheck()
  check(await group.locator('input:checked').count()===0,'FULL_KNOWN_FACET_CHOICES_REQUIRED')
  for(const id of values[field]??[]){const option=choices.find(value=>value.id===id);check(option,'FULL_KNOWN_FACET_CHOICES_REQUIRED');await group.getByRole('checkbox',{name:option.label[facts.locale],exact:true}).check()}
 }
}
async function profilePUT(page,path,status,action){
 const waiting=page.waitForResponse(response=>new URL(response.url()).origin===API&&new URL(response.url()).pathname===path&&response.request().method()==='PUT'&&response.status()===status)
 await action();const response=await waiting;await response.finished();check(/no-store/i.test(response.headers()['cache-control']??''),'PROFILE_WRITE_NO_STORE_REQUIRED')
 if(status===200)profileSuccesses++;else if(status===409)profileConflicts++;else if(status===403)profileDenials++;else check(false,'FINITE_PROFILE_WRITE_STATUS_REQUIRED')
 return response
}
async function legacyOmit(context,path,current){
 const draft={...current.draft};for(const field of facetFields)delete draft[field]
 const response=await context.request.put(API+path,{data:{expected_version:current.version,draft},headers:{Origin:DASH,'x-ci-workflow-legacy':path==='/professional-profile'?'owned':'managed'},timeout:15000,maxRedirects:0})
 try{check(response.status()===200&&/no-store/i.test(response.headers()['cache-control']??''),'REAL_LEGACY_SAVE_REQUIRED');const saved=await response.json();check(saved.version===3&&facetFields.every(key=>JSON.stringify(saved.draft[key])===JSON.stringify(current.draft[key])),'LEGACY_OMIT_PRESERVES_DECLARATIONS_REQUIRED');profileLegacyRequests++;profileSuccesses++;return saved}finally{await response.dispose()}
}
async function directoryScope(page,copy,filters,expected,action){
 const matches=response=>{const url=new URL(response.url());return url.origin===API&&response.request().method()==='GET'&&response.status()===200&&['/professionals','/professionals/map'].includes(url.pathname)&&['provider_kind','property_class','work_scope','category','area'].every(key=>JSON.stringify(url.searchParams.getAll(key).toSorted())===JSON.stringify((filters[key]??[]).toSorted()))&&['language','q'].every(key=>(url.searchParams.get(key)??'')===(filters[key]??''))}
 const list=page.waitForResponse(response=>new URL(response.url()).pathname==='/professionals'&&matches(response)),map=page.waitForResponse(response=>new URL(response.url()).pathname==='/professionals/map'&&matches(response))
 await action();const [listed,mapped]=await Promise.all([list,map]);await Promise.all([listed.finished(),mapped.finished()]);const a=await listed.json(),b=await mapped.json();workflowReads+=2
 check(a.total===expected.length&&b.total===expected.length&&a.items.length===expected.length&&JSON.stringify(a.items.map(row=>row.id).toSorted())===JSON.stringify(expected.toSorted()),'APPROVED_LIST_MAP_EXACT_SCOPE_REQUIRED')
 check([listed,mapped].every(response=>/no-store/i.test(response.headers()['cache-control']??'')),'DIRECTORY_NO_STORE_REQUIRED')
 await page.getByRole('status').filter({hasText:p(copy,'directory_search.results',{count:expected.length})}).waitFor({state:'visible'})
 check(await page.locator('main article').count()===expected.length,'ACTUAL_APPROVED_CARDS_REQUIRED');await noPrivate(page.locator('main'));directoryParity++
}
async function directoryJourney(copy,context,page){
 workflowStep('directory_filters')
 const cases=facts.workflows.directory_cases.filter(row=>Object.keys(row.filters).length),all=['owned','published_a','published_b','published_c'].map(key=>facts.workflows.profiles[key].id)
 await directoryScope(page,copy,{},all,()=>page.goto(DASH+'/professional-directory',{waitUntil:'domcontentloaded'}));await foreground(page)
 for(const [index,item] of cases.entries()){
  if(index)await directoryScope(page,copy,{},all,()=>button(page,p(copy,'directory_search.clear')).click())
  for(const [query,catalog,title] of [['provider_kind','provider_kinds','facets.provider_kinds'],['property_class','property_classes','facets.property_classes'],['work_scope','work_scopes','facets.work_scopes'],['category','categories','categories'],['area','areas','areas']]){
   if(!(item.filters[query]?.length))continue
   const group=await choiceGroup(page.locator('main form'),p(copy,title));for(const id of item.filters[query]){const option=facts.workflows.options[catalog].find(row=>row.id===id);check(option,'KNOWN_DIRECTORY_CASE_REQUIRED');await group.getByRole('checkbox',{name:option.label[facts.locale],exact:true}).check()}
  }
  if(item.filters.language)await (await labelledControl(page.locator('main form'),'select',p(copy,'languages'))).selectOption(item.filters.language)
  if(item.filters.q)await (await labelledControl(page.locator('main form'),'input',p(copy,'directory_search.search'))).fill(item.filters.q)
  await directoryScope(page,copy,item.filters,item.ids,()=>index===0?workflowEnter(page,button(page,p(copy,'directory_search.apply'))):button(page,p(copy,'directory_search.apply')).click())
  const actual=new URL(page.url());for(const key of ['provider_kind','property_class','work_scope','category','area'])check(JSON.stringify(actual.searchParams.getAll(key).toSorted())===JSON.stringify((item.filters[key]??[]).toSorted()),'ACTUAL_APPLIED_FILTER_URL_REQUIRED')
 }
 await geometry(page,page.locator('main'),facts.locale+'_workflow_directory_filters');workflowDone('directory_filters')
 workflowStep('directory_navigation')
 const previous=cases.at(-1),next=cases[0],params=new URLSearchParams();for(const [key,value] of Object.entries(next.filters))if(Array.isArray(value))value.forEach(item=>params.append(key,item));else params.set(key,value)
 await directoryScope(page,copy,next.filters,next.ids,()=>page.goto(DASH+'/professional-directory?'+params,{waitUntil:'domcontentloaded'}))
 await directoryScope(page,copy,previous.filters,previous.ids,()=>page.goBack({waitUntil:'domcontentloaded'}));directoryNavigation++
 await directoryScope(page,copy,next.filters,next.ids,()=>page.goForward({waitUntil:'domcontentloaded'}));directoryNavigation++
 await directoryScope(page,copy,{},all,()=>button(page,p(copy,'directory_search.clear')).click());workflowDone('directory_navigation')
}
async function managedOpen(page,copy){
 const profile=facts.workflows.profiles.managed
 await page.goto(DASH+'/professionals?workspace=admin',{waitUntil:'domcontentloaded'});await foreground(page)
 const row=page.locator('main article').filter({has:page.getByRole('heading',{name:profile.draft.business_name,exact:true})});await row.waitFor({state:'visible'});check(await row.count()===1,'OWNED_MANAGED_CARD_REQUIRED')
 await button(row,p(copy,'review_profile')).click();let dialog=await ownedDialog(page);await button(dialog,p(copy,'managed_edit')).waitFor({state:'visible'});await button(dialog,p(copy,'managed_edit')).click();dialog=await ownedDialog(page);await dialog.locator('form').waitFor({state:'visible'});return dialog
}
async function profileJourney(copy,sessions,adminPage,ownerPage,memberPage){
 for(const target of ['owned','managed']){
  const own=target==='owned',name=own?'own_editor':'managed_editor',actor=own?'owner':'admin',context=sessions[actor],page=own?ownerPage:adminPage,row=facts.workflows.profiles[target],path=own?'/professional-profile':'/admin/professionals/'+row.id
  workflowStep(name)
  let scope
  if(own){await page.goto(DASH+'/professional-profile',{waitUntil:'domcontentloaded'});await foreground(page);scope=page.locator('main');await scope.locator('form').waitFor({state:'visible'})}else scope=await managedOpen(page,copy)
  await version(scope,copy,1);await setFacets(scope,copy,row.selected_draft,{allThenRestore:true})
  let response=await profilePUT(page,path,200,()=>button(scope,p(copy,'save')).click()),saved=await response.json()
  check(saved.version===2&&facetFields.every(key=>JSON.stringify(saved.draft[key])===JSON.stringify(row.selected_draft[key])),'EXACT_DECLARED_FIRST_SAVE_REQUIRED')
  if(own)check(facetFields.every(key=>JSON.stringify(saved.published_profile[key])===JSON.stringify(row.published[key])),'PRIVATE_DRAFT_MUST_NOT_REPUBLISH_REQUIRED')
  await scope.getByRole('status').filter({hasText:p(copy,own?'saved':'managed_saved')}).waitFor({state:'visible'})
  if(!own){
   await button(scope,p(copy,'managed_close')).click();await button(await ownedDialog(page),p(copy,'close')).click()
   const filters={provider_kind:['contractor'],property_class:['hotel_hospitality'],work_scope:['supply']}
   const waiting=page.waitForResponse(response=>{const url=new URL(response.url());return url.origin===API&&url.pathname==='/admin/professionals'&&response.request().method()==='GET'&&response.status()===200&&Object.entries(filters).every(([key,values])=>JSON.stringify(url.searchParams.getAll(key))===JSON.stringify(values))})
   for(const [,query,catalog] of facetDimensions){const group=await choiceGroup(page.locator('main form'),p(copy,'facets.'+catalog));const id=filters[query][0],option=facts.workflows.options[catalog].find(value=>value.id===id);check(option,'KNOWN_ADMIN_DRAFT_FILTER_REQUIRED');await group.getByRole('checkbox',{name:option.label[facts.locale],exact:true}).check()}
   const filtered=await waiting;await filtered.finished();const result=await filtered.json();workflowReads++
   check(result.total===1&&result.items.length===1&&result.items[0].id===row.id&&result.items[0].listing_status==='hidden'&&JSON.stringify(result.items[0].property_class_ids)===JSON.stringify(row.selected_draft.property_class_ids),'ADMIN_FILTERS_USE_PRIVATE_DRAFT_ONLY_REQUIRED')
   await page.locator('main article').filter({has:page.getByRole('heading',{name:row.draft.business_name,exact:true})}).waitFor({state:'visible'});await geometry(page,page.locator('main'),facts.locale+'_workflow_admin_draft_filters');adminDraftFilterChecks++
  }
  await legacyOmit(context,path,saved)
  if(own){await page.reload({waitUntil:'domcontentloaded'});scope=page.locator('main');await scope.locator('form').waitFor({state:'visible'})}else scope=await managedOpen(page,copy)
  await version(scope,copy,3);await setFacets(scope,copy,{})
  response=await profilePUT(page,path,200,()=>button(scope,p(copy,'save')).click());saved=await response.json();check(saved.version===4&&facetFields.every(key=>saved.draft[key].length===0),'EXPLICIT_CLEAR_ALL_DECLARATIONS_REQUIRED')
  await version(scope,copy,4);await workflowControl(sessions.admin,own?'own_version_advance':'managed_version_advance')
  await (await labelledControl(scope,'input',p(copy,'business_name'))).fill(row.draft.business_name+' stale')
  await profilePUT(page,path,409,()=>button(scope,p(copy,'save')).click());await scope.getByRole('alert').filter({hasText:p(copy,'conflict')}).waitFor({state:'visible'});check(await button(scope,p(copy,'save')).isDisabled(),'PROFESSIONAL_CAS_LOCK_REQUIRED')
  await geometry(page,scope,facts.locale+'_workflow_'+target+'_conflict')
  await button(scope,p(copy,'discard')).click();await button(page,p(copy,'confirm')).click();if(!own)scope=await ownedDialog(page);await version(scope,copy,5)
  await button(scope,p(copy,'draft_preview')).click();await geometry(page,scope,facts.locale+'_workflow_'+target+'_editor');workflowDone(name)
  const loss=own?'own_access_loss':'admin_access_loss';workflowStep(loss)
  const stalePage=await context.newPage(),held=await heldActualGET(context,own?'/professional-profile':'/admin/professionals/'+row.id)
  if(own){await stalePage.goto(DASH+'/professional-profile',{waitUntil:'domcontentloaded'});await held.captured}
  else{await stalePage.goto(DASH+'/professionals?workspace=admin',{waitUntil:'domcontentloaded'});const card=stalePage.locator('main article').filter({has:stalePage.getByRole('heading',{name:row.draft.business_name,exact:true})});await card.waitFor({state:'visible'});await button(card,p(copy,'review_profile')).click();await held.captured;await waitCanceled(stalePage,held.request(),async()=>button(await ownedDialog(stalePage),p(copy,'close')).click())}
  if(own){const group=await choiceGroup(memberPage.locator('main form'),p(copy,'facets.provider_kinds'));await group.getByRole('checkbox',{name:facts.workflows.options.provider_kinds[0].label[facts.locale],exact:true}).check()}
  await (await labelledControl(scope,'input',p(copy,'business_name'))).fill(row.draft.business_name+' refused')
  await workflowControl(sessions.admin,own?'own_unverified':'admin_unverified')
  await profilePUT(page,path,403,()=>button(scope,p(copy,'save')).click());await page.getByRole('alert').filter({hasText:p(copy,own?'profile_access_lost':'admin_access_lost')}).waitFor({state:'visible'})
  check(await page.locator('main form,main [role="dialog"]').count()===0,'DENIAL_MUST_UNMOUNT_PRIVATE_EDITORS_REQUIRED');workflowDenials++
  if(own){
   // A full navigation obeys the outer app auth gate first. It must not be
   // mistaken for a mounted editor receiving a second profile response.
    const originalRequest=held.request(),originalURL=new URL(originalRequest.url()),beforeDocument=await stalePage.evaluate(()=>performance.timeOrigin)
    check(originalRequest.frame()===stalePage.mainFrame()&&originalRequest.method()==='GET'&&!originalRequest.isNavigationRequest()&&originalURL.origin===API&&originalURL.pathname==='/professional-profile','OUTER_AUTH_GATE_MUST_NOT_REMOUNT_PRIVATE_EDITOR_REQUIRED')
    await actualGET(stalePage,'/auth/me',403,async()=>{
     const navigation=await stalePage.reload({waitUntil:'domcontentloaded'}),afterDocument=await stalePage.evaluate(()=>performance.timeOrigin)
     check(navigation!==null&&navigation.status()===200&&navigation.request().isNavigationRequest()&&navigation.frame()===stalePage.mainFrame()&&new URL(navigation.url()).origin===DASH&&Number.isFinite(beforeDocument)&&Number.isFinite(afterDocument)&&beforeDocument!==afterDocument&&held.request()===originalRequest,'OUTER_AUTH_GATE_MUST_NOT_REMOUNT_PRIVATE_EDITOR_REQUIRED')
    });check(await stalePage.getByRole('heading',{name:p(copy,'details_heading'),exact:true}).count()===0,'OUTER_AUTH_GATE_MUST_NOT_REMOUNT_PRIVATE_EDITOR_REQUIRED')
   await actualGET(memberPage,'/professionals',403,()=>button(memberPage,p(copy,'directory_search.apply')).click());await memberPage.getByRole('alert').filter({hasText:p(copy,'directory_search.auth_error')}).waitFor({state:'visible'});check(await memberPage.locator('main article,[role="dialog"]').count()===0,'DENIED_MEMBER_PROJECTIONS_REQUIRED');workflowDenials+=2
  }else{
   await actualGET(stalePage,'/admin/professionals',403,()=>button(stalePage,p(copy,'admin_private_drafts')).click());await stalePage.getByRole('alert').filter({hasText:p(copy,'admin_access_lost')}).waitFor({state:'visible'});workflowDenials++
  }
  await held.release();await held.remove();check(await stalePage.getByRole('heading',{name:p(copy,'details_heading'),exact:true}).count()===0&&await stalePage.getByRole('dialog').count()===0&&!await stalePage.locator('input').evaluateAll((nodes,name)=>nodes.some(node=>node.value===name),row.draft.business_name),'LATE_REAL200_CANNOT_RESTORE_PRIVATE_EDITOR_REQUIRED');retiredReads++
  await stalePage.close();await workflowControl(sessions.admin,own?'own_verified':'admin_verified')
  await actualGET(page,own?'/professional-profile':'/admin/professionals',200,()=>button(page,p(copy,'retry')).click());await page.locator(own?'main form':'main article').first().waitFor({state:'visible'});await geometry(page,page.locator('main'),facts.locale+'_workflow_'+target+'_recovered')
  workflowDone(loss)
 }
}

async function workflowBoundariesFor(sessions,anonymous){
 advance(facts.locale+'_workflow_boundaries')
 const profiles=facts.workflows.profiles,manual=facts.workflows.manual
 const specs=[['directory_public_approved','owner','/professionals/'+profiles.published_a.id,200],['directory_private_hidden','owner','/professionals/'+profiles.managed.id,404],['directory_admin_private','admin','/admin/professionals/'+profiles.managed.id,200],['directory_buyer_admin403','buyer','/admin/professionals/'+profiles.managed.id,403],['directory_realtor_admin403','realtor','/admin/professionals/'+profiles.managed.id,403],['directory_anonymous401',null,'/professionals',401],['manual_buyer_admin403','buyer','/admin/leads',403],['manual_owner_realtor403','owner','/realtor/leads',403],['manual_anonymous401',null,'/admin/leads',401],['manual_admin_valid200','admin','/admin/leads',200],['manual_assignee_valid200','realtor','/realtor/leads',200],['manual_public_link200',null,'/listings/'+manual.valid.property_id,200]]
 for(const [label,actor,path,status] of specs){
  const response=await (actor?sessions[actor]:anonymous).request.get(API+path,{headers:{'x-ci-workflow-boundary':label},timeout:15000,maxRedirects:0})
  try{check(response.status()===status,'ACTUAL_WORKFLOW_BOUNDARY_STATUS_REQUIRED');if(path.startsWith('/professionals')||path.startsWith('/admin/professionals')||path.startsWith('/listings/'))check(/no-store/i.test(response.headers()['cache-control']??''),'WORKFLOW_BOUNDARY_NO_STORE_REQUIRED');workflowBoundaries++}finally{await response.dispose()}
 }
}
const manualRegion=(page,copy)=>page.getByRole('region',{name:m(copy,'title'),exact:true})
const manualPreview=(region,copy)=>region.getByRole('region',{name:m(copy,'preview'),exact:true})
async function manualOpen(page,copy,key,{actor='admin',pipeline=false}={}){
 const item=facts.workflows.manual[key]
 if(actor==='admin')await page.goto(DASH+'/leads?workspace=admin&openId='+item.id,{waitUntil:'domcontentloaded'})
 else{await page.goto(DASH+(pipeline?'/pipeline':'/leads'),{waitUntil:'domcontentloaded'});const name=(pipeline?page.locator('main'):page.locator('div.lg\\:hidden')).getByText(item.name,{exact:true});await name.waitFor({state:'visible'});check(await name.count()===1,'UNIQUE_CURRENT_ASSIGNEE_LEAD_REQUIRED');await name.click()}
 await foreground(page);await panel(page)
 const region=manualRegion(page,copy);await region.waitFor({state:'visible'});check(await region.count()===1,'UNIQUE_MANUAL_FOLLOWUP_REGION_REQUIRED')
 await button(region,m(copy,'open')).click();await manualPreview(region,copy).waitFor({state:'visible'});await noPrivate(region);return region
}
async function inputValue(page,control,wanted){const node=await control.elementHandle();check(node,'CURRENT_LABELLED_INPUT_REQUIRED');await page.waitForFunction(({node,wanted})=>node.isConnected&&node.value===wanted,{node,wanted});await node.dispose()}
async function manualState(page){return page.evaluate(()=>({opens:window.__customerManual.opens.length,copies:window.__customerManual.copies.length}))}
async function manualRequest(page,region,copy,action,expected,observe=null){
 observe?.('handoff_state');const before=await manualState(page);observe?.('handoff_click');await button(region,m(copy,action)).click()
 observe?.('handoff_capture_wait');await page.waitForFunction(count=>window.__customerManual.opens.length===count+1,before.opens)
 observe?.('handoff_value');const actual=await page.evaluate(()=>window.__customerManual.opens.at(-1))
 check(actual.url===expected&&actual.target==='_blank'&&actual.features==='noopener,noreferrer','EXACT_MANUAL_DESTINATION_REQUEST_REQUIRED')
 observe?.('handoff_status_wait');await region.getByRole('status').filter({hasText:m(copy,'requested')}).waitFor({state:'visible'});observe?.('handoff_privacy');await noPrivate(region);manualRequests++
}
function expectedMail(item,subject,body){return 'mailto:'+encodeURIComponent(item.email)+'?subject='+encodeURIComponent(subject)+'&body='+encodeURIComponent(body)}
async function manualJourney(copy,sessions,adminPage,realtorPage){
 const valid=facts.workflows.manual.valid
 workflowStep('manual_drafts');let region=await manualOpen(adminPage,copy,'valid')
 const phrases={en:{welcome:'What can we help',buyer:'budget',seller:'valuation',renter:'occupants',inquiry:'viewing',booking:'Availability'},es:{welcome:'Qué necesita',buyer:'presupuesto',seller:'valoración',renter:'ocupantes',inquiry:'visita',booking:'disponibilidad'}}
 for(const language of ['en','es'])for(const kind of ['welcome','buyer','seller','renter','inquiry','booking']){
  await (await labelledControl(region,'select',m(copy,'language'))).selectOption(language);await (await labelledControl(region,'select',m(copy,'template'))).selectOption(kind)
  const body=await (await labelledControl(region,'textarea',m(copy,'body'))).inputValue();check(body.includes(valid.name)&&body.includes(phrases[language][kind]),'EXPLICIT_LANGUAGE_AND_TEMPLATE_REQUIRED');manualDraftChecks++
 }
 const reviewed='Reviewed synthetic '+facts.locale,subject='Owned '+facts.locale+' subject',body='Owned '+facts.locale+' customer message'
 await (await labelledControl(region,'input',m(copy,'name'))).fill(reviewed);check((await (await labelledControl(region,'textarea',m(copy,'body'))).inputValue()).includes(reviewed),'EDITABLE_RECIPIENT_GREETING_REQUIRED')
 await (await labelledControl(region,'input',m(copy,'subject'))).fill('');await region.getByRole('alert').filter({hasText:m(copy,'invalid')}).waitFor({state:'visible'})
 for(const action of ['copy','email','whatsapp'])check(await button(region,m(copy,action)).isDisabled(),'INVALID_DRAFT_CANNOT_HANDOFF_REQUIRED')
 await (await labelledControl(region,'input',m(copy,'subject'))).fill(subject);await (await labelledControl(region,'textarea',m(copy,'body'))).fill(body)
 check(await manualPreview(region,copy).locator('pre').innerText()===body,'EXACT_EDITABLE_PREVIEW_REQUIRED');await geometry(adminPage,region,facts.locale+'_workflow_manual_editor');workflowDone('manual_drafts')

 workflowStep('manual_handoffs')
 await workflowEnter(adminPage,button(region,m(copy,'copy')));await region.getByRole('status').filter({hasText:m(copy,'copy_failed')}).waitFor({state:'visible'})
 check(await manualPreview(region,copy).locator('pre').innerText()===body&&await (await labelledControl(region,'input',m(copy,'subject'))).inputValue()===subject,'COPY_REFUSAL_RETAINS_EDITABLE_PREVIEW_REQUIRED');manualCopies++;manualCopyRefusals++
 await adminPage.evaluate(()=>{window.__customerManual.rejectClipboard=false});await button(region,m(copy,'copy')).click();await region.getByRole('status').filter({hasText:m(copy,'copied')}).waitFor({state:'visible'})
 check(await adminPage.evaluate(()=>navigator.clipboard.readText())===body,'ACTUAL_OWNED_CLIPBOARD_BYTES_REQUIRED');manualCopies++
 await manualRequest(adminPage,region,copy,'email',expectedMail(valid,subject,body))
 await manualRequest(adminPage,region,copy,'whatsapp','https://wa.me/'+valid.phone.slice(1)+'?text='+encodeURIComponent(body))
 await manualRequest(adminPage,region,copy,'call','tel:'+valid.phone)
 await geometry(adminPage,region,facts.locale+'_workflow_manual_handoffs');workflowDone('manual_handoffs')

 workflowStep('manual_invalid');region=await manualOpen(adminPage,copy,'invalid')
 check(!(await manualPreview(region,copy).innerText()).includes(body)&&(await (await labelledControl(region,'textarea',m(copy,'body'))).inputValue()).includes(facts.workflows.manual.invalid.name),'TARGET_SWITCH_DISCARDS_PRIOR_MANUAL_DRAFT_REQUIRED');manualTargetResets++
 await region.getByText(m(copy,'phone_unavailable'),{exact:true}).waitFor({state:'visible'})
 check(!(await region.innerText()).includes(facts.workflows.manual.invalid.phone),'INVALID_RAW_PHONE_NOT_PROJECTED_REQUIRED')
 check(await button(region,m(copy,'whatsapp')).isDisabled()&&await button(region,m(copy,'call')).isDisabled()&&await button(region,m(copy,'email')).isEnabled(),'RAW_PHONE_EXTENSION_MUST_NOT_GAIN_DESTINATION_REQUIRED')
 await geometry(adminPage,region,facts.locale+'_workflow_manual_invalid');workflowDone('manual_invalid')

 workflowStep('manual_current_reset');region=await manualOpen(adminPage,copy,'valid')
 await (await labelledControl(region,'textarea',m(copy,'body'))).fill('OWNED_TEMPORARY_MESSAGE_TO_DISCARD')
 const unchanged=await manualState(adminPage);await workflowControl(sessions.admin,'manual_contact_change')
 await actualGET(adminPage,'/admin/leads',200,()=>button(region,m(copy,'email')).click());await region.getByRole('status').filter({hasText:m(copy,'changed')}).waitFor({state:'visible'})
 await inputValue(adminPage,await labelledControl(region,'input',m(copy,'name')),valid.name+' changed');check(!(await manualPreview(region,copy).innerText()).includes('OWNED_TEMPORARY_MESSAGE_TO_DISCARD'),'CHANGED_CURRENT_CONTACT_RESETS_DRAFT_REQUIRED');manualResets++
 await workflowControl(sessions.admin,'manual_contact_restore');await actualGET(adminPage,'/admin/leads',200,()=>button(region,m(copy,'copy')).click())
 await inputValue(adminPage,await labelledControl(region,'input',m(copy,'name')),valid.name);check(JSON.stringify(await manualState(adminPage))===JSON.stringify(unchanged),'STALE_DRAFT_MUST_NOT_COPY_OR_HANDOFF_REQUIRED');manualResets++
 await geometry(adminPage,region,facts.locale+'_workflow_manual_reset');workflowDone('manual_current_reset')

 workflowStep('manual_public_link');publicCheckpoint=null
 const publicURL='https://ilovedrrealty.com/listing/'+valid.property_id+'/'
 await publicGET(adminPage,'/listings/'+valid.property_id,200,()=>button(region,m(copy,'check_link')).click());manualPublicChecks++
 publicProgress('link_ready_wait');await region.getByRole('status').filter({hasText:m(copy,'link_ready')}).waitFor({state:'visible'});publicProgress('opt_in_inspect');const include=region.getByRole('checkbox');check(await include.count()===1&&!await include.isChecked(),'PUBLIC_LINK_REQUIRES_EXPLICIT_OPT_IN_REQUIRED')
 publicProgress('unchecked_preview');check(!(await manualPreview(region,copy).locator('pre').innerText()).includes(publicURL),'UNCHECKED_PUBLIC_LINK_EXCLUDED_REQUIRED');publicProgress('opt_in_check');await include.check()
 publicProgress('read_subject_preview');const currentSubject=await (await labelledControl(region,'input',m(copy,'subject'))).inputValue(),withLink=await manualPreview(region,copy).locator('pre').innerText()
 check(withLink.endsWith('\n\n'+publicURL),'CANONICAL_PUBLIC_LINK_ONLY_REQUIRED')
 await publicGET(adminPage,'/listings/'+valid.property_id,200,()=>manualRequest(adminPage,region,copy,'email',expectedMail(valid,currentSubject,withLink),publicProgress));manualPublicChecks++
 publicProgress('withdraw_control');await workflowControl(sessions.admin,'manual_property_withdrawn');publicProgress('withdraw_state');const outgoing=await manualState(adminPage)
 await publicGET(adminPage,'/listings/'+valid.property_id,404,()=>button(region,m(copy,'whatsapp')).click());manualPublicChecks++
 publicProgress('withdraw_notice_wait');await region.getByRole('status').filter({hasText:m(copy,'link_unavailable')}).waitFor({state:'visible'});publicProgress('withdraw_assertions');check(JSON.stringify(await manualState(adminPage))===JSON.stringify(outgoing)&&await region.getByRole('checkbox').count()===0&&!(await manualPreview(region,copy).locator('pre').innerText()).includes(publicURL),'WITHDRAWN_LINK_BLOCKS_HANDOFF_AND_CLEARS_LINK_REQUIRED')
 publicProgress('restore_control');await workflowControl(sessions.admin,'manual_property_restore');publicProgress('link_lost_geometry');await geometry(adminPage,region,facts.locale+'_workflow_manual_link_lost');workflowDone('manual_public_link')

 workflowStep('manual_cancel')
 await (await labelledControl(region,'textarea',m(copy,'body'))).fill('OWNED_CANCELED_MESSAGE')
 const held=await heldActualGET(sessions.admin,'/admin/leads');await button(region,m(copy,'refresh')).click();await held.captured
 await waitCanceled(adminPage,held.request(),()=>button(region,m(copy,'close')).click());check(await manualPreview(region,copy).count()===0,'CLOSE_REMOVES_MANUAL_DRAFT_REQUIRED');await held.release();await held.remove();retiredReads++
 await button(region,m(copy,'open')).click();await manualPreview(region,copy).waitFor({state:'visible'});check(!(await region.innerText()).includes('OWNED_CANCELED_MESSAGE'),'CANCELED_READ_CANNOT_RESTORE_DRAFT_REQUIRED');manualResets++;workflowDone('manual_cancel')

 workflowStep('manual_assignment_loss');region=await manualOpen(realtorPage,copy,'valid',{actor:'realtor'})
 await (await labelledControl(region,'textarea',m(copy,'body'))).fill('OWNED_REALTOR_DRAFT')
 const before=await manualState(realtorPage);await workflowControl(sessions.admin,'manual_assignee_admin')
 await actualGET(realtorPage,'/realtor/leads',200,()=>button(region,m(copy,'email')).click());await region.getByRole('alert').filter({hasText:m(copy,'denied')}).waitFor({state:'visible'})
 check(await region.locator('input,select,textarea,pre').count()===0&&JSON.stringify(await manualState(realtorPage))===JSON.stringify(before),'CURRENT_ASSIGNEE_INTERSECTION_CLEARS_DRAFT_REQUIRED');workflowDenials++;manualResets++
 await workflowControl(sessions.admin,'manual_assignee_restore');await actualGET(realtorPage,'/realtor/leads',200,()=>button(region,m(copy,'refresh')).click());await manualPreview(region,copy).waitFor({state:'visible'});await geometry(realtorPage,region,facts.locale+'_workflow_manual_assignee_recovered');workflowDone('manual_assignment_loss')

 workflowStep('manual_auth_loss');await workflowControl(sessions.admin,'manual_realtor_unverified')
 await actualGET(realtorPage,'/auth/me',403,()=>button(region,m(copy,'call')).click());await region.getByRole('alert').filter({hasText:m(copy,'denied')}).waitFor({state:'visible'})
 check(await region.locator('input,select,textarea,pre').count()===0&&JSON.stringify(await manualState(realtorPage))===JSON.stringify(before),'CURRENT_AUTHORIZATION_REQUIRED_BEFORE_HANDOFF');workflowDenials++;manualResets++
 await geometry(realtorPage,region,facts.locale+'_workflow_manual_denied');await workflowControl(sessions.admin,'manual_realtor_verified');workflowDone('manual_auth_loss')

 workflowStep('manual_pipeline');region=await manualOpen(realtorPage,copy,'valid',{actor:'realtor',pipeline:true})
 const pipelineSubject=await (await labelledControl(region,'input',m(copy,'subject'))).inputValue(),pipelineBody=await manualPreview(region,copy).locator('pre').innerText()
 await manualRequest(realtorPage,region,copy,'email',expectedMail(valid,pipelineSubject,pipelineBody));await geometry(realtorPage,region,facts.locale+'_workflow_manual_pipeline');workflowDone('manual_pipeline')
}
async function customerWorkflows(copy,sessions,anonymous,adminPage){
 inWorkflow=true
 const member=await workflowPage(sessions.owner,'owner_directory'),own=await workflowPage(sessions.owner,'owner_editor'),realtor=await workflowPage(sessions.realtor,'realtor_manual')
 await directoryJourney(copy,sessions.owner,member)
 await profileJourney(copy,sessions,adminPage,own,member)
 await manualJourney(copy,sessions,adminPage,realtor)
 check(JSON.stringify(workflowJourneys)===JSON.stringify(workflowNames),'ALL_ORDERED_CUSTOMER_WORKFLOW_JOURNEYS_REQUIRED')
 check(profileUIRequests===8&&profileLegacyRequests===2&&profileSuccesses===6&&profileConflicts===2&&profileDenials===2,'EXACT_CUSTOMER_PROFILE_REQUESTS_REQUIRED')
 check(workflowControls===14&&workflowBoundaries===12&&retiredReads===3,'EXACT_WORKFLOW_CONTROL_BOUNDARY_RETIREMENT_REQUIRED')
 check(directoryParity===14&&directoryNavigation===2&&adminDraftFilterChecks===1&&manualTargetResets===1&&manualDraftChecks===12&&manualRequests===5&&manualCopies===2&&manualCopyRefusals===1&&manualPublicChecks===3,'EXACT_MANUAL_AND_DIRECTORY_BEHAVIOR_REQUIRED')
 check(workflowPresence===3&&workflowDenials===7&&manualResets===5&&workflowGeometries.length===32&&workflowKeyboardActions===2,'EXACT_EXTRA_PRIVACY_GEOMETRY_REQUIRED')
 for(const page of [member,own,realtor])await page.close()
 inWorkflow=false
}

async function run(){
 advance('fixture')
 const filename=process.env.ILDR_BROWSER_FIXTURES,info=await lstat(filename)
 check(info.isFile()&&!info.isSymbolicLink()&&info.uid===process.getuid()&&(info.mode&0o077)===0&&info.size>0&&info.size<=131072,'OWNED_BOUNDED_FIXTURE_REQUIRED')
 const prepared=JSON.parse(await readFile(filename,'utf8'));check(prepared.scope==='customer-workflows'&&prepared.accounts&&prepared.facts,'EXACT_FIXTURE_SCOPE_REQUIRED');({accounts,facts}=prepared)
 facts.ordered_items=facts.signup_items
 check(['en','es'].includes(facts.locale)&&Object.keys(accounts).sort().join(',')===actors.toSorted().join(',')&&actors.every(actor=>uuid(facts.ids[actor])&&facts.roles[actor]===actor),'FOUR_SYNTHETIC_ROLES_REQUIRED')
 check(facts.signup_items.length===11&&facts.seed_assignment_items.length===11&&Object.values(facts.leads).length===4&&Object.values(facts.leads).every(row=>uuid(row.id)),'SOURCE_BOUND_PRIVATE_SEED_FACTS_REQUIRED')
 check(facts.workflows&&Object.keys(facts.workflows.profiles).sort().join(',')==='managed,owned,published_a,published_b,published_c'&&Object.values(facts.workflows.profiles).every(row=>uuid(row.id)&&row.version===1&&row.draft)&&['valid','invalid'].every(key=>uuid(facts.workflows.manual[key].id)&&uuid(facts.workflows.manual[key].property_id)),'EXACT_CUSTOMER_WORKFLOW_FACTS_REQUIRED')
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
  // Exercise the genuine initial Buyer role before the retained seller flow
  // intentionally promotes that synthetic account to Owner.
  inWorkflow=true;await workflowBoundariesFor(sessions,anonymous);inWorkflow=false
  await assignmentJourney(copy,sessions,page)
  await signupJourney(copy,sessions.admin,page)
  await customerWorkflows(copy,sessions,anonymous,page)
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
  console.log('CUSTOMER_WORKFLOWS_BROWSER_RESULT '+JSON.stringify({status:'PASS',locale:facts.locale,journeys:16,geometry:26,contexts:5,presence:2,actual_logins:4,
   assignment_requests:9,successful_responses:8,unique_request_ids:8,persisted_assignment_intents:7,exact_replays:1,uncertain_commits:1,agent_refusals:1,known204_get_only_recoveries:1,
   account_no_email_events:2,valuation_notices:1,seller_notices:3,control_transitions:2,api_boundary_checks:16,assignment_ui_page_checks:2,signup_ui_page_checks:2,
   canceled_history_reads:2,signup_read_recoveries:1,auth_loss_checks:1,keyboard_saves:1,closed_contexts:5,page_errors:0,unexpected_mutations:0,external_writes:0,
   tracking_requests:0,blocked_external_requests:blockedExternal,
   workflow_journeys:workflowJourneys.length,workflow_geometry:workflowGeometries.length,workflow_presence:workflowPresence,workflow_controls:workflowControls,workflow_boundary_checks:workflowBoundaries,workflow_read_checks:workflowReads,workflow_keyboard_actions:workflowKeyboardActions,
   profile_ui_requests:profileUIRequests,profile_legacy_requests:profileLegacyRequests,profile_successful_saves:profileSuccesses,profile_cas_conflicts:profileConflicts,profile_auth_refusals:profileDenials,
   directory_scope_checks:directoryParity,directory_url_navigation:directoryNavigation,admin_draft_filter_checks:adminDraftFilterChecks,manual_target_reset_checks:manualTargetResets,workflow_denial_clear_checks:workflowDenials,retired_response_checks:retiredReads,
   manual_template_checks:manualDraftChecks,manual_destination_requests:manualRequests,manual_copy_attempts:manualCopies,manual_native_clipboard_successes:manualCopies-manualCopyRefusals,manual_controlled_copy_refusals:manualCopyRefusals,manual_current_resets:manualResets,manual_public_link_checks:manualPublicChecks}))
 }catch(error){fail(error)}finally{clearTimeout(watchdog);if(browser){for(const context of contexts)if(!closed.has(context))await context.close().catch(()=>{});await browser.close()}}
}
run().catch(fail)
