// Isolated production-component browser acceptance; no real account or model API.
import { createServer } from 'vite';
import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const output = new URL('../../tmp/cloud-capabilities-ui/', import.meta.url);
await mkdir(output, { recursive: true });
const fixture = `
import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ModelCapabilitiesEditor} from '/src/components/ModelCapabilities.tsx';
import {NexilumeDialog} from '/src/components/NexilumeControls.tsx';
import {setLocale} from '/src/localization/index.ts';
import '/src/styles.css';
setLocale(new URLSearchParams(location.search).get('lang') || 'en-US');
const h=React.createElement;
const base={context_window:128000,max_input_tokens:64000,max_output_tokens:32768,supports_reasoning:true,
reasoning_effort_levels:['none','low','high'],default_reasoning_effort:'low',supported_openai_params:['messages','reasoning_effort']};
function App(){
const [data,setData]=useState({override:null,model_info:base,inherited_model_info:base,can_edit:true,
fields:{reasoning_efforts:{value:base.reasoning_effort_levels,sources:['upstream'],updated_at:'2026-11-05T02:00:00Z',stale:false,upstream_confirmed:true}},inherited_fields:{}});
const [revision,setRevision]=useState(0); const [saved,setSaved]=useState(false);
window.acceptanceBackgroundUpdate=()=>setData(d=>({...d,override:{default_reasoning_effort:'high'}}));
return h(NexilumeDialog,{open:true,title:'Reasoning model',onClose(){}},h(ModelCapabilitiesEditor,{key:revision,data,provider:true,busy:false,saved,
onSave(override,expected){window.acceptanceSave={override,expected};setData(d=>({...d,override,model_info:{...base,...override},
fields:{reasoning_efforts:{value:override?.reasoning_effort_levels??base.reasoning_effort_levels,sources:[override?'provider_override':'upstream'],updated_at:'2026-11-05T02:00:00Z',stale:false,upstream_confirmed:!override}}}));setRevision(x=>x+1);setSaved(true);},
onRefresh(){setData(d=>({...d,model_info:{...base,reasoning_effort_levels:['none','low','high','max']}}));},onReload(){}}));}
createRoot(document.getElementById('root')).render(h(App));
`;
const server = await createServer({ configFile: false, server: { host: '127.0.0.1', port: 0 },
  plugins: [{ name: 'model-capabilities-fixture', resolveId(id) { if(id==='/capabilities-fixture.js')return id; },
    load(id) { if(id==='/capabilities-fixture.js')return fixture; },
    configureServer(vite) { vite.middlewares.use('/capabilities-preview', async (req,res,next)=>{try{
      res.setHeader('Content-Type','text/html'); res.end(await vite.transformIndexHtml(req.url,
      '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/capabilities-fixture.js"></script></body></html>'));
    }catch(error){next(error);}}); } }], esbuild: { jsx: 'automatic' } });
await server.listen();
let browser;
try {
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1100,height:900}});
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const url=server.resolvedUrls.local[0]+'capabilities-preview';
  await page.goto(url);
  await expect(page.getByTestId('reasoning-levels')).toHaveText('none · low · high');
  await page.getByRole('button',{name:'Refresh from upstream'}).click();
  await expect(page.getByTestId('reasoning-levels')).toContainText('max');
  await page.getByLabel('Override model capabilities').check();
  await page.getByLabel('Supported reasoning levels',{exact:true}).fill('none, low, ultra');
  await page.getByLabel('Default effort',{exact:true}).selectOption('none');
  // A background metadata refresh must preserve draft and original CAS token.
  await page.evaluate(()=>window.acceptanceBackgroundUpdate());
  await expect(page.getByLabel('Supported reasoning levels',{exact:true})).toHaveValue('none, low, ultra');
  await page.getByRole('button',{name:'Save capabilities'}).click();
  const saved=await page.evaluate(()=>window.acceptanceSave);
  expect(saved.expected).toBeNull();
  expect(saved.override).toEqual({reasoning_effort_levels:['none','low','ultra'],default_reasoning_effort:'none'});
  await expect(page.getByRole('status')).toHaveText('Capabilities saved and available to clients.');
  await page.screenshot({path:fileURLToPath(new URL('configured.png',output))});
  await page.getByRole('button',{name:'Restore automatic discovery'}).click();
  await expect(page.getByLabel('Override model capabilities')).not.toBeChecked();
  expect((await page.evaluate(()=>window.acceptanceSave)).override).toBeNull();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:fileURLToPath(new URL('mobile.png',output))});
  const overflow=await page.locator('.nexilume-dialog,.nexilume-dialog__body').evaluateAll(elements=>elements.some(el=>el.scrollWidth>el.clientWidth+1));
  expect(overflow).toBe(false);
  await page.goto(url+'?lang=zh-CN');
  await expect(page.getByLabel('手动覆盖模型能力')).toBeVisible();
  expect(errors).toEqual([]);
  await writeFile(new URL('result.json',output),JSON.stringify({passed:true,upstreamRefresh:true,exactNone:true,futureEnum:true,
    concurrentDraftPreserved:true,originalCasPreserved:true,restoreInheritance:true,mobileOverflow:false,chinese:true,modelCalls:0},null,2));
  console.log('Model capabilities browser acceptance passed.');
} finally { await browser?.close(); await server.close(); }
