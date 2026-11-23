'use strict';
const $ = id => document.getElementById(id);
let state = null, dirty = false, activeTab = 'overview', toastTimer;
const titles = {overview: '运行概览', settings: '连接设置', connect: '接入流影'};
const formatBytes = n => n >= 1073741824 ? (n/1073741824).toFixed(2)+' GB' : n >= 1048576 ? (n/1048576).toFixed(1)+' MB' : n >= 1024 ? (n/1024).toFixed(1)+' KB' : n+' B';
function toast(message, error=false) { clearTimeout(toastTimer); $('toast').textContent=message; $('toast').className=error?'error':''; $('toast').hidden=false; toastTimer=setTimeout(()=>$('toast').hidden=true,5000); }
async function api(path, data, timeout=20000) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),timeout);
  try {
    const response = await fetch('/admin/'+path,{method:data?'POST':'GET',credentials:'same-origin',signal:controller.signal,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});
    if(response.status===401&&path!=='login')showLogin();
    const text=await response.text();
    if(!text.trim())throw Error('加速站返回空响应（HTTP '+response.status+'），请检查容器状态或 NAS 反向代理超时');
    let value;
    try{value=JSON.parse(text);}catch{throw Error('加速站返回非 JSON 响应（HTTP '+response.status+'），请检查 NAS 反代及容器日志');}
    if(!response.ok)throw Error(value.error||'请求失败（HTTP '+response.status+'）');
    if(!value||typeof value!=='object'||Array.isArray(value))throw Error('加速站响应格式异常，请确认程序已完整更新');
    return value;
  } catch(error){
    if(controller.signal.aborted)throw Error('加速站响应超时，请检查代理链路或 NAS 反代超时设置');
    if(error instanceof TypeError)throw Error('无法连接加速站，请检查 NAS 网络和反向代理');
    throw error;
  } finally {clearTimeout(timer);}
}
async function busy(button, action, text='处理中…') {
  if(button.disabled)return;const old=button.textContent;button.disabled=true;button.textContent=text;
  try {await action();} catch(e){toast(e.message||'网络连接失败',true);} finally{button.disabled=false;button.textContent=old;}
}
function showLogin(){state=null;$('login').hidden=false;$('dashboard').hidden=true;$('password').value='';}
function showTab(tab){activeTab=tab;document.querySelectorAll('.tab').forEach(el=>el.hidden=el.id!==tab);document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.tab===tab));$('page-title').textContent=titles[tab];}
function draft(){return {proxy_url:$('proxy-url').value.trim(),public_url:$('public-url').value.trim(),cache_mb:Number($('cache-mb').value)};}
function editFields(){const c=state.config;$('proxy-url').value=c.proxy_url;$('public-url').value=c.public_url;$('cache-mb').value=c.cache_mb;dirty=false;$('dirty-hint').textContent='保存后对新请求生效';}
function render(){
  const c=state.config,counts=state.counts;
  if($('app-version'))$('app-version').textContent='YINGQIAO / '+(state.version||'后端版本未知，请完整更新项目');
  $('requests').textContent=counts.requests.toLocaleString();$('hit-rate').textContent=counts.image_requests?Math.round(counts.cache_hits/counts.image_requests*100)+'%':'—';
  $('hits').textContent=counts.cache_hits+' 次命中 / '+counts.image_requests+' 次图片请求';$('transferred').textContent=formatBytes(counts.transferred);
  $('cache-used').textContent=formatBytes(state.cache.bytes);$('cache-count').textContent=state.cache.files+' 张图片 / 上限 '+c.cache_mb+' MB';
  $('route-summary').textContent=c.proxy_url?'通过 HTTP 代理':'直接连接 TMDB';
  $('uptime').textContent=state.uptime>=3600?Math.floor(state.uptime/3600)+' 小时 '+Math.floor(state.uptime%3600/60)+' 分钟':Math.floor(state.uptime/60)+' 分钟';
  const base=(c.public_url||location.origin).replace(/\/$/,'')+'/r/'+c.access_key;
  $('api-address').value=base+'/3';$('image-address').value=base+'/t/p';
  $('events').replaceChildren();$('empty-events').hidden=!!state.events.length;
  state.events.forEach(event=>{const tr=document.createElement('tr');const values=[event.kind,event.cached?'缓存命中':event.status<400?'已转发 '+event.status:'上游 '+event.status,event.ms+' ms',new Date(event.at*1000).toLocaleTimeString('zh-CN',{hour12:false})];values.forEach((v,i)=>{const td=document.createElement('td');if(i===1){const tag=document.createElement('span');tag.className='badge '+(event.cached?'cache':event.status>=400?'bad':'');tag.textContent=v;td.append(tag);}else td.textContent=v;tr.append(td);});$('events').append(tr);});
}
async function refresh(edit=false){state=await api('state');$('login').hidden=true;$('dashboard').hidden=false;render();if(edit)editFields();}
function renderTest(id,results){const parent=$(id);parent.replaceChildren();results.forEach(result=>{const el=document.createElement('div');el.className='test-result'+(result.pending?' pending':!result.ok?' failed':'');const left=document.createElement('div'),title=document.createElement('b'),message=document.createElement('p'),right=document.createElement('span');title.textContent=result.name;message.textContent=result.message;right.textContent=result.pending?'检测中…':result.skipped?'直连':result.ms+' ms';left.append(title,message);el.append(left,right);parent.append(el);});}
const diagnosticReports={};
async function test(id,config){
 const rows=[['proxy','NAS → Clash 代理'],['api','TMDB API 网络'],['image','TMDB 图片网络']].map(([target,name])=>({target,name,pending:true,message:'正在从 NAS 发起检测…'}));
 const copyButton=document.querySelector('[data-copy-report="'+id+'"]');if(copyButton)copyButton.hidden=true;
 renderTest(id,rows);
 await Promise.all(rows.map(async(row,index)=>{
  const started=performance.now();
  try{const value=await api('network-test',{proxy_url:config.proxy_url,target:row.target},12000);if(!value.result||value.result.target!==row.target)throw Error('检测响应不完整，请确认后端程序已更新');rows[index]=value.result;}
  catch(error){rows[index]={...row,pending:false,ok:false,reachable:false,error:'relay_response',ms:Math.round(performance.now()-started),message:error.message};}
  renderTest(id,rows);
 }));
 diagnosticReports[id]=JSON.stringify({checked_at:new Date().toISOString(),route:config.proxy_url?'HTTP 网络代理':'直连',proxy_url:config.proxy_url||'',results:rows},null,2);
 if(copyButton)copyButton.hidden=false;
}
async function copyReport(id){
 const text=diagnosticReports[id];if(!text)return;
 try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);}else{const area=document.createElement('textarea');area.className='copy-buffer';area.value=text;document.body.append(area);area.select();const ok=document.execCommand('copy');area.remove();if(!ok)throw Error('复制失败');}toast('诊断结果已复制，不包含管理员密码、TMDB 密钥或加速访问密钥');}
 catch{toast('复制失败，请截取检测结果发给维护者',true);}
}
function confirmAction(title,message){return new Promise(resolve=>{const dialog=$('confirm-dialog');$('confirm-title').textContent=title;$('confirm-text').textContent=message;dialog.returnValue='cancel';dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.showModal();});}
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>showTab(button.dataset.tab)));
document.querySelectorAll('[data-copy]').forEach(button=>button.addEventListener('click',async()=>{const input=$(button.dataset.copy);try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(input.value);toast('地址已复制');}else{input.focus();input.select();if(document.execCommand('copy'))toast('地址已复制');else toast('已选中地址，请手动复制');}}catch{input.focus();input.select();toast('已选中地址，请手动复制');}}));
$('login-form').addEventListener('submit',event=>{event.preventDefault();$('login-error').textContent='';busy($('login-button'),async()=>{try{await api('login',{password:$('password').value});await refresh(true);showTab('overview');}catch(e){$('login-error').textContent=e.message;throw e;}},'正在登录…');});
$('settings-form').addEventListener('submit',event=>{event.preventDefault();busy($('save-settings'),async()=>{await api('config',draft());await refresh(true);toast('设置已保存，新请求将使用新配置');});});
['proxy-url','public-url','cache-mb'].forEach(id=>$(id).addEventListener('input',()=>{dirty=true;$('dirty-hint').textContent='有未保存的更改';}));
$('test-draft').addEventListener('click',()=>busy($('test-draft'),()=>test('settings-test',draft()),'正在验证网络…'));
$('quick-test').addEventListener('click',()=>busy($('quick-test'),()=>test('overview-test',state.config),'正在验证网络…'));
document.querySelectorAll('[data-copy-report]').forEach(button=>button.addEventListener('click',()=>copyReport(button.dataset.copyReport)));
$('refresh').addEventListener('click',()=>busy($('refresh'),()=>refresh(),'刷新中…'));
$('logout').addEventListener('click',()=>busy($('logout'),async()=>{await api('logout',{});showLogin();}));
$('mobile-logout').addEventListener('click',()=>busy($('mobile-logout'),async()=>{await api('logout',{});showLogin();}));
$('rotate').addEventListener('click',async()=>{if(await confirmAction('重置加速访问密钥？','旧的加速地址会立即失效，需要重新复制地址并更新流影配置。'))busy($('rotate'),async()=>{await api('rotate',{});await refresh();toast('密钥已重置，请更新流影中的地址');});});
$('clear-cache').addEventListener('click',async()=>{if(await confirmAction('清空图片缓存？','已缓存的图片将被移除，下次访问时会重新下载。连接配置不会改变。'))busy($('clear-cache'),async()=>{await api('cache/clear',{});await refresh();toast('图片缓存已清空');});});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
refresh(true).catch(e=>{showLogin();if(e.message!=='请先登录')$('login-error').textContent='暂时无法连接加速站，请刷新重试';});
setInterval(()=>{if(state&&activeTab==='overview'&&!document.hidden)refresh().catch(()=>{});},15000);
