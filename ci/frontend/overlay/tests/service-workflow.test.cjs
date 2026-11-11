const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const A='11111111-1111-4111-8111-111111111111', B='22222222-2222-4222-8222-222222222222', C='33333333-3333-4333-8333-333333333333', D='44444444-4444-4444-8444-444444444444'
const E='55555555-5555-4555-8555-555555555555', NOW='2026-11-02T10:00:00Z'
const plain = value => JSON.parse(JSON.stringify(value))
const terms = changes => ({amount:'1234.50',currency:'USD',pricing_basis:'fixed',scope_text:'Synthetic agreed service',exclusions:'Synthetic exclusion',estimated_days:2,valid_until:'2026-11-30',...changes})
const proposal = changes => ({...terms(),id:D,recipient_id:C,revision_no:1,parent_proposal_id:null,kind:'provider_quote',status:'submitted',version:1,is_expired:false,created_at:NOW,...changes})
const recipient = changes => ({id:C,user_code:72,member_alias:'Synthetic provider',status:'accepted',version:2,current_proposal:proposal(),capabilities:{can_revoke:true,can_counter:true,can_award:true,can_withdraw:false},...changes})
const managed = changes => ({id:A,view:'managed',version:1,status:'open',source:{property_id:B,listing_id:null,title:'Private synthetic property',property_class:'house_villa'},category_id:'air_conditioning',title:'Synthetic request',brief:'Shared synthetic brief',area_label:'Synthetic coarse area',requester_alias:'Synthetic requester',desired_start_on:null,created_at:NOW,updated_at:NOW,recipients:[recipient()],project_id:null,can_open_project:false,capabilities:{can_cancel:true,can_compare:true,can_award:true},...changes})
const received = changes => ({id:A,view:'received',category_id:'air_conditioning',property_class:'house_villa',title:'Shared synthetic request',brief:'Shared synthetic brief',area_label:'Synthetic coarse area',requester_alias:'Synthetic requester',desired_start_on:'2026-11-10',created_at:NOW,recipient:{id:C,status:'accepted',version:2},current_proposal:proposal(),project_id:null,can_open_project:false,capabilities:{can_accept:false,can_decline:true,can_quote:true,can_withdraw:true,can_accept_counter:false,can_decline_counter:false},...changes})
const prefs = changes => ({service_opt_in:false,member_alias:'',category_ids:[],property_class_ids:[],version:0,updated_at:null,can_manage:true,...changes})
const page = (items,extra={}) => ({items,total:items.length,page:1,page_size:30,...extra})
const requestInput = changes => ({submission_id:E,property_id:B,listing_id:null,category_id:'air_conditioning',property_class:'house_villa',title:'Synthetic request',brief:'Deliberately shared synthetic scope',area_label:'Synthetic area',requester_alias:'Synthetic requester',desired_start_on:null,recipient_user_codes:[72],shared_brief_acknowledged:true,...changes})
function harness(initial) {
  const calls=[],cache=new Map();let response=initial,failure
  const client=Object.fromEntries(['get','post','put'].map(method=>[method,async(url,...args)=>{calls.push({method,url,args});if(failure)throw failure;return {data:response}}]))
  function load(name) {
    if(cache.has(name))return cache.get(name)
    const exports={};cache.set(name,exports)
    const file=path.join(root,'apps/dashboard/src/api',name+'.ts')
    const compiled=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.CommonJS,esModuleInterop:false}}).outputText
    vm.runInNewContext(compiled,{exports,Date,Intl,Set,Error,require(id){if(id==='./axios')return {default:client};if(name==='serviceWorkflow'&&id==='./propertyNetwork')return load('propertyNetwork');assert.fail('Unapproved API test dependency: '+id)}})
    return exports
  }
  return {api:load('serviceWorkflow'),calls,reply:value=>{response=value;failure=undefined},fail:value=>{failure=value}}
}
test('service preference read is off by default without a write or profile publication', async()=>{
  const h=harness(prefs()),result=await h.api.getServicePreferences()
  assert.equal(result.service_opt_in,false);assert.equal(result.version,0)
  assert.deepEqual(h.calls.map(c=>c.method),['get']);assert.equal(h.calls[0].url,'/property-network/me/service-preferences')
  assert.equal(h.calls[0].args[0].withCredentials,true)
})
test('discovery accepts only the minimal opted-in alias projection and exact page identity',async()=>{
  const item={user_code:72,member_alias:'Synthetic alias',category_ids:['air_conditioning']}
  const h=harness(page([item])),signal={synthetic:true}
  assert.deepEqual(plain((await h.api.getServiceProviders('air_conditioning','house_villa',1,signal)).items),[item])
  assert.equal(h.calls[0].args[0].signal,signal)
  for(const extra of [{email:'FORBIDDEN'},{phone:'FORBIDDEN'},{user_id:B},{property_class_ids:['house_villa']},{professional_profile_id:D}]){
    h.reply(page([{...item,...extra}]));await assert.rejects(h.api.getServiceProviders('air_conditioning','house_villa'),/Invalid private service data/)
  }
})
test('received projection rejects private dossiers, competitors and global activity metadata',async()=>{
  const h=harness(received());const row=await h.api.getServiceRequest(A,'received')
  assert.equal(h.calls[0].args[0].params.view,'received')
  assert.equal(row.recipient.id,C);assert.equal(Object.hasOwn(row,'version'),false)
  for(const extra of [{source:{property_id:B}},{address:'FORBIDDEN'},{recipients:[recipient()]},{version:99},{status:'awarded'},{updated_at:NOW}]){
    h.reply(received(extra));await assert.rejects(h.api.getServiceRequest(A,'received'),/Invalid private service data/)
  }
  h.reply(managed());await assert.rejects(h.api.getServiceRequest(A,'received'),/Invalid private service data/)
})
test('managed projection checks source XOR, unique recipients and strict capabilities',async()=>{
  const h=harness(managed());assert.equal((await h.api.getServiceRequest(A,'managed')).source.property_id,B)
  for(const changes of [{source:{...managed().source,listing_id:D}},{recipients:[recipient(),recipient()]},{capabilities:{can_cancel:'false',can_compare:true,can_award:true}}]){
    h.reply(managed(changes));await assert.rejects(h.api.getServiceRequest(A,'managed'),/Invalid private service data/)
  }
})
test('informational prices retain decimal precision and reject malformed or out-of-range amounts',()=>{
  const h=harness()
  assert.equal(h.api.serviceAmount('9999999999.99'),'9999999999.99')
  assert.equal(h.api.serviceAmountMinor('9999999999.99'),999999999999)
  assert.equal(h.api.serviceAmountMinor('0.01'),1)
  assert.equal(h.api.serviceAmountMinor('12.5'),1250)
  for(const bad of ['0','0.00','-1','01.20','1.234','1e3','10000000000',' 2.00',1.25,null])assert.throws(()=>h.api.serviceAmount(bad),/Invalid private service data/)
})
test('create sends exactly the deliberate shared fields and rejects invalid sources before transport',async()=>{
  const h=harness(managed())
  await h.api.runServiceAction({operation:'request_create',audience:'managed',input:requestInput()})
  assert.deepEqual(plain(h.calls[0].args[0]),requestInput())
  for(const changes of [{listing_id:D},{property_id:null},{shared_brief_acknowledged:false},{recipient_user_codes:[72,72]},{custodian_id:D}]){
    await assert.rejects(h.api.runServiceAction({operation:'request_create',audience:'managed',input:requestInput(changes)}),/Invalid private service data/)
  }
  assert.equal(h.calls.length,1)
})
test('self opt-in uses explicit preference fields and cannot carry another actor or role',async()=>{
  const input={submission_id:E,expected_version:0,service_opt_in:true,member_alias:'Synthetic member',category_ids:['air_conditioning'],property_class_ids:['house_villa']}
  const h=harness(prefs({...input,version:1,updated_at:NOW}))
  // Server preference response deliberately has no submission or expected-version input fields.
  h.reply(prefs({service_opt_in:true,member_alias:input.member_alias,category_ids:input.category_ids,property_class_ids:input.property_class_ids,version:1,updated_at:NOW}))
  await h.api.runServiceAction({operation:'preference_save',audience:'preferences',expected_actor_id:B,input})
  assert.equal(h.calls[0].method,'put');assert.deepEqual(plain(h.calls[0].args[0]),input)
  for(const extra of [{user_id:B},{role:'admin'},{public_contact_consent:true}])await assert.rejects(h.api.runServiceAction({operation:'preference_save',audience:'preferences',expected_actor_id:B,input:{...input,...extra}}),/Invalid private service data/)
  assert.equal(h.calls.length,1)
})
test('provider acceptance and quote carry only their own versions and immutable proposal inputs',async()=>{
  const h=harness(received())
  await h.api.runServiceAction({operation:'recipient_accept',audience:'received',request_id:A,recipient_id:C,input:{submission_id:E,expected_recipient_version:2}})
  assert.deepEqual(plain(h.calls[0].args[0]),{submission_id:E,expected_recipient_version:2})
  await h.api.runServiceAction({operation:'provider_quote',audience:'received',request_id:A,recipient_id:C,input:{submission_id:E,expected_recipient_version:2,current_proposal_id:D,expected_proposal_version:1,terms:terms()}})
  assert.equal(h.calls[1].url,'/property-network/service-requests/'+A+'/recipients/'+C+'/proposals')
  assert.equal(Object.hasOwn(h.calls[1].args[0],'expected_request_version'),false)
  await assert.rejects(h.api.runServiceAction({operation:'recipient_accept',audience:'received',request_id:A,recipient_id:C,input:{submission_id:E,expected_recipient_version:2,expected_request_version:99}}),/Invalid private service data/)
})
test('counter proposals preserve exact original versions and do not accept free-form reason fields',async()=>{
  const h=harness(managed())
  const input={submission_id:E,expected_request_version:1,expected_recipient_version:2,current_proposal_id:D,expected_proposal_version:1,terms:terms({currency:'DOP',amount:'2000.25'})}
  await h.api.runServiceAction({operation:'owner_counter',audience:'managed',request_id:A,recipient_id:C,input})
  assert.deepEqual(plain(h.calls[0].args[0]),input)
  await assert.rejects(h.api.runServiceAction({operation:'request_cancel',audience:'managed',request_id:A,input:{submission_id:E,expected_request_version:1,action_message:'not in this contract'}}),/Invalid private service data/)
  assert.equal(h.calls.length,1)
})
test('award requires exact acknowledged project scope and keeps only a current authorized project link',async()=>{
  const winners=[recipient({status:'awarded',current_proposal:proposal({status:'awarded'})})]
  const request=managed({status:'awarded',project_id:B,can_open_project:true,recipients:winners})
  const h=harness({request,project:{id:B,owner_email:'FORBIDDEN',private_notes:'FORBIDDEN'}})
  const action={operation:'request_award',audience:'managed',request_id:A,input:{submission_id:E,expected_request_version:1,recipient_id:C,expected_recipient_version:2,proposal_id:D,expected_proposal_version:1,project_title:'Synthetic agreed work',project_summary:'Only the approved scope',project_scope_acknowledged:true}}
  assert.deepEqual(plain((await h.api.runServiceAction(action)).project),{id:B})
  await assert.rejects(h.api.runServiceAction({...action,input:{...action.input,project_scope_acknowledged:false}}),/Invalid private service data/)
  h.reply({request:managed({status:'awarded',project_id:null,can_open_project:false,recipients:winners}),project:null})
  assert.equal((await h.api.runServiceAction(action)).project,null)
})
test('result resolution binds original key, operation and caller-whitelisted versions',async()=>{
  const action={operation:'recipient_accept',audience:'received',request_id:A,recipient_id:C,input:{submission_id:E,expected_recipient_version:2}}
  const result={submission_id:E,operation:'recipient_accept',target_id:C,applied_at:NOW,result_versions:{recipient_version:3},current:received()}
  const h=harness(result);assert.equal((await h.api.getServiceSubmission(action)).submission_id,E)
  assert.equal(h.calls[0].url,'/property-network/service-actions/submissions/'+E)
  for(const change of [{submission_id:B},{operation:'request_cancel'},{target_id:D},{result_versions:{request_version:99}},{current:managed()}]){
    h.reply({...result,...change});await assert.rejects(h.api.getServiceSubmission(action),/Invalid private service data/)
  }
})
test('prior terms reader is recipient-scoped, paged and rejects another thread or duplicate proposals',async()=>{
  const h=harness(page([proposal({id:D,revision_no:2}),proposal({id:E,revision_no:1})]))
  assert.equal((await h.api.getServiceProposals(A,C,1,undefined,'received')).items.length,2)
  assert.equal(h.calls[0].args[0].params.view,'received')
  assert.equal(h.calls[0].url,'/property-network/service-requests/'+A+'/recipients/'+C+'/proposals')
  h.reply(page([proposal({recipient_id:B})]));await assert.rejects(h.api.getServiceProposals(A,C),/Invalid private service data/)
  h.reply(page([proposal(),proposal()]));await assert.rejects(h.api.getServiceProposals(A,C),/Invalid private service data/)
  await assert.rejects(h.api.getServiceProposals(A,C,1001),/Invalid private service data/)
})
test('provider activity rejects global result versions and raw event fields',async()=>{
  const event={id:E,action:'provider_quote',occurred_at:NOW,recipient_id:C,proposal_id:D,result_versions:{recipient_version:2,proposal_version:1}}
  const h=harness(page([event]));assert.equal((await h.api.getServiceEvents(A,'received',1,undefined,C)).items[0].recipient_id,C)
  assert.equal(h.calls[0].args[0].params.view,'received')
  for(const extra of [{recipient_id:B},{result_versions:{request_version:2}},{actor_id:B},{message:'FORBIDDEN'}]){
    h.reply(page([{...event,...extra}]));await assert.rejects(h.api.getServiceEvents(A,'received',1,undefined,C),/Invalid private service data/)
  }
})
test('request pages never turn malformed data, duplicate rows or revoked received threads into an empty success',async()=>{
  const h=harness(page([received()],{can_create:true}))
  assert.equal((await h.api.getServiceRequests('received','all')).items.length,1)
  for(const raw of [page([received(),received()],{can_create:true}),page([received({recipient:{id:C,status:'revoked',version:3}})],{can_create:true}),page([],{can_create:true,page:2}),{items:null,total:0,page:1,page_size:30,can_create:true}]){
    h.reply(raw);await assert.rejects(h.api.getServiceRequests('received','all'),/Invalid private service data/)
  }
})
test('currency groups remain distinct and malformed zero or cross-currency terms are rejected',async()=>{
  const h=harness({request_id:A,request_version:1,groups:[{currency:'USD',items:[recipient()]}],unquoted:[]})
  assert.equal((await h.api.getServiceComparison(A)).groups[0].currency,'USD')
  h.reply({request_id:A,request_version:1,groups:[{currency:'DOP',items:[recipient()]}],unquoted:[]})
  await assert.rejects(h.api.getServiceComparison(A),/Invalid private service data/)
  h.reply(received({current_proposal:proposal({amount:'0'})}));await assert.rejects(h.api.getServiceRequest(A,'received'),/Invalid private service data/)
})
test('transport failures retain their identity and a mutation is never automatically repeated',async()=>{
  const h=harness(),failure={isAxiosError:true,response:{status:503}}
  h.fail(failure)
  await assert.rejects(h.api.runServiceAction({operation:'request_cancel',audience:'managed',request_id:A,input:{submission_id:E,expected_request_version:1}}),error=>error===failure)
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].args[1].timeout,15000)
  for(const status of [401,403,404,409]){const error={isAxiosError:true,response:{status}};h.fail(error);await assert.rejects(h.api.getServiceRequest(A,'managed'),cause=>cause===error)}
})

test('outcome targets bind the authenticated actor and immutable request source selection',async()=>{
  const action={operation:'preference_save',audience:'preferences',expected_actor_id:B,input:{submission_id:E}}
  const result={submission_id:E,operation:'preference_save',target_id:B,applied_at:NOW,result_versions:{preference_version:1},current:prefs({version:1})}
  const h=harness(result);assert.equal((await h.api.getServiceSubmission(action)).target_id,B)
  h.reply({...result,target_id:C});await assert.rejects(h.api.getServiceSubmission(action),/Invalid private service data/)
  const create={operation:'request_create',audience:'managed',input:requestInput()}
  const current=managed({title:create.input.title,brief:create.input.brief,area_label:create.input.area_label,requester_alias:create.input.requester_alias})
  const created={submission_id:E,operation:'request_create',target_id:A,applied_at:NOW,result_versions:{request_version:1},current}
  h.reply(created);assert.equal((await h.api.getServiceSubmission(create)).target_id,A)
  for(const change of [{target_id:C},{current:{...current,source:{...current.source,property_id:C}}},{current:{...current,recipients:[recipient({user_code:73})]}}]){
    h.reply({...created,...change});await assert.rejects(h.api.getServiceSubmission(create),/Invalid private service data/)
  }
})

test('direct and resolved awards bind the exact frozen winning recipient and proposal while permitting a revoked project link',async()=>{
  const OTHER='66666666-6666-4666-8666-666666666666',OTHER_PROPOSAL='77777777-7777-4777-8777-777777777777'
  const action={operation:'request_award',audience:'managed',request_id:A,recipient_id:C,input:{submission_id:E,expected_request_version:1,recipient_id:C,expected_recipient_version:2,proposal_id:D,expected_proposal_version:1,project_title:'Synthetic approved title',project_summary:'Synthetic approved scope',project_scope_acknowledged:true}}
  const winner=recipient({status:'awarded',version:3,current_proposal:proposal({status:'awarded',version:2})})
  const valid={request:managed({status:'awarded',recipients:[winner],project_id:null,can_open_project:false}),project:null}
  const wrongWinner={...valid,request:{...valid.request,recipients:[
    recipient({status:'closed',current_proposal:proposal({status:'closed'})}),
    recipient({id:OTHER,user_code:73,status:'awarded',current_proposal:proposal({id:OTHER_PROPOSAL,recipient_id:OTHER,status:'awarded'})}),
  ]}}
  const wrongProposal={...valid,request:{...valid.request,recipients:[recipient({status:'awarded',current_proposal:proposal({id:OTHER_PROPOSAL,status:'awarded'})})]}}
  const wrongRecipientState={...valid,request:{...valid.request,recipients:[recipient({status:'accepted',current_proposal:proposal({status:'awarded'})})]}}
  const wrongProposalState={...valid,request:{...valid.request,recipients:[recipient({status:'awarded',current_proposal:proposal({status:'submitted'})})]}}
  const h=harness(valid)
  assert.equal((await h.api.runServiceAction(action)).project,null,'Current project ACL loss does not invalidate the correct retained award')
  const result=current=>({submission_id:E,operation:'request_award',target_id:A,applied_at:NOW,result_versions:{request_version:2,recipient_version:3,proposal_version:2},current})
  h.reply(result(valid));assert.equal((await h.api.getServiceSubmission(action)).current.project,null)
  for(const malformed of [wrongWinner,wrongProposal,wrongRecipientState,wrongProposalState]){
    h.reply(malformed)
    await assert.rejects(h.api.runServiceAction(action),cause=>cause instanceof h.api.ServiceContractError&&!(cause instanceof h.api.ServiceInputError),'A sent wrong award is a response-contract failure, never an unsent input failure')
    h.reply(result(malformed))
    await assert.rejects(h.api.getServiceSubmission(action),cause=>cause instanceof h.api.ServiceContractError&&!(cause instanceof h.api.ServiceInputError))
  }
  assert.equal(h.calls.filter(call=>call.method==='post').length,5,'Only five explicit test sends occurred; no automatic retry')
})
