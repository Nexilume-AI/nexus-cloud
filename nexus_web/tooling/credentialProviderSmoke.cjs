const {chromium}=require('playwright');
const fs=require('node:fs'),http=require('node:http'),assert=require('node:assert/strict');
const html=fs.readFileSync('../nexus_server/apps/agents/credential_page.html','utf8').replaceAll('__NONCE__','smoke');
const endpoint='/api/v1/agent-authorizations/test/';let posted;
const detail={status:'pending',agent_name:'Test Agent',provider:'nexus',provider_name:'Nexus Cloud',base_url:'https://cloud.example/v1',model:'test-model',expires_at:new Date(Date.now()+300000).toISOString(),nexus_router_delegation:false,nexus_routers:[],saved_credentials:[],providers:[{id:'nexus',name:'Nexus Cloud',base_url:'https://cloud.example/v1'},{id:'custom_openai',name:'OpenAI-compatible',base_url:''}]};
const server=http.createServer(async(req,res)=>{
 if(req.url===endpoint+'page/'){res.writeHead(200,{'Content-Type':'text/html'});return res.end(html);}
 assert.equal(req.url,endpoint,'Personal UI must use its supported authorization endpoint');
 if(req.method==='POST'){let raw='';for await(const c of req)raw+=c;posted=JSON.parse(raw);}
 res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({...detail,status:posted?'authorized':'pending'}));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+endpoint+'page/');await page.locator('#form').waitFor({state:'visible'});
  assert.equal(await page.locator('#nexusFields').isVisible(),false);assert.equal(await page.locator('#secret').isVisible(),true);
  assert.equal(await page.locator('#baseUrl').inputValue(),'https://cloud.example/v1');assert.equal(await page.locator('#baseUrl').isEditable(),false);
  await page.locator('#secret').fill('discard-me');await page.locator('#consent').check();
  await page.locator('#providerChoice').selectOption('custom_openai');
  assert.equal(await page.locator('#secret').inputValue(),'');assert.equal(await page.locator('#consent').isChecked(),false);
  await page.locator('#baseUrl').fill('https://api.example/v1');await page.locator('#secret').fill('synthetic-key');
  await page.locator('#authorize').click();assert.equal(posted,undefined);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('#consent').check();await page.locator('#authorize').click();await page.locator('#ready').waitFor({state:'visible'});
  assert.equal(posted.provider,'custom_openai');assert.equal(posted.secret,'synthetic-key');assert.equal(posted.consent,true);assert.equal(posted.router_id,undefined);
  assert.equal(await page.locator('#secret').inputValue(),'');assert.deepEqual(errors,[]);
  console.log('PASS: Personal provider selection, explicit consent, capability boundary, secret clearing, mobile layout');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
