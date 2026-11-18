"""Small same-origin device approval page; no credential is present in its URL."""
import secrets
from django.http import HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET


@require_GET
@ensure_csrf_cookie
def page(request):
    nonce = secrets.token_urlsafe(20)
    html = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect Relay · Nexus</title><style nonce="NONCE">
*{box-sizing:border-box}body{margin:0;background:#f7f7f5;color:#202321;font:15px/1.6 system-ui,sans-serif}main{max-width:640px;margin:8vh auto;padding:36px;background:white;border-radius:20px}h1{font-size:28px;letter-spacing:-1px;margin:0}p{color:#646963}label{display:block;margin-top:20px;font-weight:550}input,select,button{font:inherit;border:1px solid #d9ded9;border-radius:9px;padding:10px 14px}input,select{width:100%;margin:6px 0}button{cursor:pointer;background:white;margin:16px 8px 0 0}button.primary{background:#203f35;color:white;border-color:transparent}button:disabled{opacity:.5;cursor:default}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 ui-monospace,monospace;background:#f7f7f5;padding:16px;border-radius:10px}a{color:#255847}#notice{white-space:pre-wrap}article{margin:20px 0;padding-top:12px;border-top:1px solid #eee}@media(max-width:680px){main{margin:20px;padding:24px}}
</style><main><h1>Connect your Relay</h1><p>Approve only a device you started. It can register the models and endpoints shown below in your selected Organization. It cannot access unrelated resources.</p>
<div id="notice" role="status"></div><a id="signin" href="/login" hidden>Sign in to Nexus, then return here</a>
<label for="code">Device code</label><input id="code" autocomplete="off" maxlength="16" placeholder="ABCDE-FGHJK"><button id="review">Review device</button>
<section id="details" hidden><h2 id="name"></h2><pre id="models"></pre><label for="org">Organization</label><select id="org"></select><p>Access lasts 30 days. Credentials refresh automatically. An outbound binding permits this device to forward requests and their Provider credential only to the listed upstream. Existing Provider adoption preserves its Key and Router links. Revoke at any time to block new requests and disconnect forwarding.</p><button id="allow" class="primary">Connect Relay</button><button id="deny">Decline</button></section>
<section><h2>Your Relay devices</h2><div id="devices"></div></section></main>
<script nonce="NONCE">
const base='/api/v1/auth/relay/', el=id=>document.getElementById(id); let code='';
function csrf(){const c=document.cookie.split('; ').find(x=>x.startsWith('csrftoken='));return c?decodeURIComponent(c.slice(10)):''}
async function api(path,data){const r=await fetch(base+path,{method:data?'POST':'GET',credentials:'same-origin',headers:data?{'Content-Type':'application/json','X-CSRFToken':csrf()}:{},body:data?JSON.stringify(data):undefined});const j=await r.json();if(!r.ok){if(r.status===401||r.status===403)el('signin').hidden=false;throw Error((j.error&&j.error.message)||j.detail||'Request failed. Check your login and permissions.')}el('signin').hidden=true;return j.data===undefined?j:j.data}
function notice(v){el('notice').textContent=v}
async function review(){try{code=el('code').value;const d=await api('approval/?code='+encodeURIComponent(code));el('name').textContent=d.name;el('models').textContent=d.models.map(m=>m.name+'\n'+m.model+' · '+m.protocol+'\n'+m.endpoint+(m.transport==='outbound'?'\nOutbound forwarding'+(m.provider_id?' · Existing Provider '+m.provider_id:''):'')).join('\n\n');el('org').replaceChildren(...d.organizations.map(o=>{const e=document.createElement('option');e.value=o.id;e.textContent=o.name;return e}));el('details').hidden=d.status!=='pending';notice(d.status==='pending'?'Check the code matches your device.':'Device request: '+d.status)}catch(e){notice(e.message)}}
async function decide(approve){el('allow').disabled=el('deny').disabled=true;try{const d=await api('approval/',approve?{code,approve,tenant_id:el('org').value}:{code,approve});el('details').hidden=true;notice(d.status==='approved'?'Approved. Return to your device to finish connecting.':'Request declined.');await devices()}catch(e){notice(e.message)}finally{el('allow').disabled=el('deny').disabled=false}}
async function devices(){try{const d=await api('devices/');el('devices').replaceChildren(...d.devices.map(v=>{const a=document.createElement('article'),t=document.createElement('div');t.textContent=v.name+' · '+v.status;a.append(t);if(v.status==='active'||v.status==='approved'){const b=document.createElement('button');b.textContent='Revoke device';b.onclick=async()=>{if(!confirm('Revoke '+v.name+' and disable its model sources?'))return;b.disabled=true;try{await api('devices/'+v.id+'/revoke/',{});await devices()}catch(e){notice(e.message);b.disabled=false}};a.append(b)}return a}))}catch(e){notice(e.message)}}
el('review').onclick=review;el('allow').onclick=()=>decide(true);el('deny').onclick=()=>decide(false);
el('code').value=new URLSearchParams(location.search).get('code')||'';devices();if(el('code').value)review();
</script></html>'''.replace("NONCE", nonce)
    response = HttpResponse(html)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Content-Security-Policy"] = f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response
