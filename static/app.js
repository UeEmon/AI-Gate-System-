'use strict';
const $ = id => document.getElementById(id);
const state = {active: null, selected: null, page: 1, busy: false, refreshing: false};
const labels = {starting:'準備中', running:'処理中', stopping:'停止中', stopped:'停止済み', completed:'完了', failed:'失敗', interrupted:'中断'};
function message(text='') {$('message').textContent=text; $('message').hidden=!text;}
async function api(url, options={}) {
  options.headers={...options.headers, 'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content};
  const response=await fetch(url,options);
  const data=await response.json().catch(()=>({error:'サーバー応答を確認できません。再読み込みしてください。'}));
  if(!response.ok) throw new Error(data.error||'処理に失敗しました。');
  return data;
}
function cell(text, className='') {const td=document.createElement('td');td.textContent=text;td.className=className;return td;}
function date(value){return new Date(value).toLocaleString('ja-JP');}
function pct(value){return Number.isFinite(value)?Math.round(value*100)+'%':'—';}
function setControls(){ $('start').disabled=!!state.active||state.busy; $('stop').disabled=!state.active||state.busy; }
for(const input of document.querySelectorAll('input[name="kind"]'))input.addEventListener('change',()=>{
  const file=input.value==='file'&&input.checked;
  $('file-fields').hidden=!file;$('camera-fields').hidden=file;
});
$('start-form').addEventListener('submit',async event=>{
  event.preventDefault();state.busy=true;setControls();message();
  try{const data=await api('/api/jobs',{method:'POST',body:new FormData(event.target)});state.selected=data.id;state.active=data.id;state.page=1;}
  catch(error){message(error.message);}finally{state.busy=false;await refresh();setControls();}
});
$('stop').addEventListener('click',async()=>{
  if(!state.active)return;state.busy=true;setControls();
  try{await api('/api/jobs/'+state.active+'/stop',{method:'POST'});message('停止を要求しました。');}
  catch(error){message(error.message);}finally{state.busy=false;await refresh();setControls();}
});
async function renderObservations(){
  const params=new URLSearchParams({page:state.page,job:$('job-filter').value,vehicle:$('vehicle-filter').value});
  const data=await api('/api/observations?'+params);
  if(data.page>1&&data.items.length===0){state.page=1;return renderObservations();}
  $('result-total').textContent=data.total+'件';$('results').replaceChildren();
  if(!data.items.length){const tr=document.createElement('tr');const td=cell('認識結果はありません','empty');td.colSpan=5;tr.append(td);$('results').append(tr);}
  for(const item of data.items){
    const tr=document.createElement('tr');const plate=item.plate_candidates?.[0];
    tr.append(cell(date(item.processed_at)),cell(item.vehicle_type_ja||item.vehicle_type));
    const td=cell(plate?.text||'読取候補なし','plate');const note=document.createElement('span');note.className='sub';note.textContent=plate?'要確認 · フレーム '+item.frame_index:'車両検出のみ';td.append(note);tr.append(td);
    tr.append(cell(pct(item.confidence)+' / '+pct(plate?.confidence)));
    const image=cell('—');if(item.has_image){image.textContent='';const a=document.createElement('a');a.textContent='画像を確認';a.href='/api/observations/'+encodeURIComponent(item.id)+'/image';a.target='_blank';a.rel='noopener';image.append(a);}tr.append(image);$('results').append(tr);
  }
  const pages=Math.max(1,Math.ceil(data.total/data.page_size));$('page-label').textContent=state.page+' / '+pages;$('prev').disabled=state.page<=1;$('next').disabled=state.page>=pages;
}
async function refresh(){
  if(state.refreshing)return;state.refreshing=true;
  try{
    const data=await api('/api/jobs');state.active=data.active_id;
    if(!state.selected&&data.jobs.length)state.selected=state.active||data.jobs[0].id;
    const current=data.jobs.find(job=>job.id===state.selected);
    const existing=$('job-filter').value;
    const options=[new Option('すべての処理',''),...data.jobs.map(job=>new Option(date(job.created_at)+' · '+job.label,job.id))];
    $('job-filter').replaceChildren(...options);$('job-filter').value=existing;
    $('jobs').replaceChildren();
    if(!data.jobs.length){const p=document.createElement('p');p.textContent='処理履歴はありません';p.className='hint';$('jobs').append(p);}
    for(const job of data.jobs){const row=document.createElement('div');row.className='job';const detail=document.createElement('div');const title=document.createElement('strong');title.textContent=job.label;const info=document.createElement('small');info.className='sub';info.textContent=date(job.created_at)+' · '+(job.kind==='camera'?'カメラ':'ファイル')+' · '+labels[job.status];detail.append(title,info);
      if(job.error){const error=document.createElement('p');error.className='error-text';error.textContent=job.error;detail.append(error);}const button=document.createElement('button');button.textContent='表示';button.onclick=()=>{state.selected=job.id;$('job-filter').value=job.id;state.page=1;refresh();};row.append(detail,button);$('jobs').append(row);}
    $('job-status').textContent=current?(current.status==='running'&&current.progress.phase==='loading'?'モデル準備中':labels[current.status]):'待機中';
    $('frame-count').textContent=current?.progress.frames_processed??'—';$('observation-count').textContent=current?.progress.observations??'—';$('source-label').textContent=current?.label||'未選択';
    const show=!!current?.has_preview;$('preview').hidden=!show;$('preview-empty').hidden=show;
    if(show)$('preview').src='/api/jobs/'+current.id+'/preview?t='+Date.now();
    await renderObservations();$('connection').textContent='接続中';
  }catch(error){$('connection').textContent='接続エラー';message(error.message);}finally{state.refreshing=false;setControls();}
}
for(const id of ['job-filter','vehicle-filter'])$(id).addEventListener('change',()=>{state.page=1;refresh();});
$('prev').onclick=()=>{state.page--;refresh();};$('next').onclick=()=>{state.page++;refresh();};$('refresh').onclick=refresh;
refresh();setInterval(refresh,1500);
