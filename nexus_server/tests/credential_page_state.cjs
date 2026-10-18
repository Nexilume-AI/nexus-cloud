// State-level UI regression: hidden fields must not block keyless Nexus approval.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
  constructor(){this.value='';this.options=[];this.hidden=false;this.required=false;this.checked=false;this.textContent='';}
  replaceChildren(...items){this.options=items;this.value=items[0]?.value||'';}
  add(item){this.options.push(item);}
  addEventListener(){}
}
const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const detail={agent_name:'Test',provider:'nexus',provider_name:'Nexus Cloud',base_url:'https://cloud.nexilume.com/api/v1/openai/v1',
  status:'pending',model:'model-a',tenant_id:'tenant',caller_type:'user',expires_at:'2026-10-19T00:00:00Z',
  providers:[{id:'nexus',name:'Nexus Cloud',base_url:'https://cloud.nexilume.com/api/v1/openai/v1'},
    {id:'custom_openai',name:'Custom OpenAI-compatible',base_url:''}],
  nexus_router_delegation:true, nexus_routers:[{router_id:'router-1',name:'Router One',models:['model-a','model-b']}],saved_credentials:[]};
let posted;
const ctx=vm.createContext({document:{getElementById:get,cookie:'',querySelectorAll:()=>[]},
 location:{pathname:'/api/v1/agent-authorizations/test/page/'},Option:function(text,value){return {text,value};},
 addEventListener(){},fetch:async(url,opts)=>{if(opts.body)posted=JSON.parse(opts.body);return {status:200,ok:true,json:async()=>detail};},console});
const html=fs.readFileSync(require('node:path').join(__dirname,'../apps/agents/credential_page.html'),'utf8');
vm.runInContext(html.split('<script nonce="__NONCE__">')[1].split('</script>')[0],ctx);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(get('endpointFields').hidden,true);assert.equal(get('credentialFields').hidden,true);
 assert.equal(get('secret').required,false);assert.equal(get('model').required,false);
 assert.equal(get('nexusModel').required,true);assert.equal(get('nexusModel').value,'model-a');
 assert.equal(get('routerChoice').value,'router-1');
 get('ttl').value='300';get('consent').checked=true;
 await vm.runInContext("decide('authorize')",ctx);
 assert.equal(posted.router_id,'router-1');assert.equal(posted.model,'model-a');
 assert.ok(!('secret' in posted));assert.ok(!('base_url' in posted));
 get('providerChoice').value='custom_openai';vm.runInContext('providerChanged()',ctx);
 assert.equal(get('nexusFields').hidden,true);assert.equal(get('nexusModel').required,false);
 assert.equal(get('secret').required,true);assert.equal(get('model').required,true);
 assert.equal(get('baseUrl').readOnly,false);assert.equal(get('consent').checked,false);
 vm.runInContext('state({...'+JSON.stringify(detail)+',nexus_router_delegation:false})',ctx);
 assert.equal(get('nexusFields').hidden,true);assert.equal(get('secret').required,true);
 assert.equal(get('model').required,true);assert.equal(get('routerChoice').required,false);
 console.log('PASS keyless Nexus UI validation/payload and custom-provider field switching');
})().catch(error=>{console.error(error);process.exitCode=1;});
