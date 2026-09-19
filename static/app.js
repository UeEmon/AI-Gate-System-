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
function setControls(){
  $('start').disabled=!!state.active||state.busy;$('stop').disabled=!state.active||state.busy;
  $('delete-history').disabled=!!state.active||state.busy;
}
for(const input of document.querySelectorAll('input[name="kind"]'))input.addEventListener('change',()=>{
  const file=input.value==='file'&&input.checked;
  const browser=input.value==='browser'&&input.checked;
  $('file-fields').hidden=!file;$('camera-fields').hidden=file||browser;$('browser-fields').hidden=!browser;
});
let browserStream=null,browserTimer=null,browserJob=null;
async function stopBrowserCamera(){if(browserTimer){clearInterval(browserTimer);browserTimer=null;}if(browserStream){browserStream.getTracks().forEach(track=>track.stop());browserStream=null;}browserJob=null;}
async function startBrowserCamera(jobId){
  if(!navigator.mediaDevices?.getUserMedia)throw new Error('このブラウザはWebカメラ入力に対応していません。HTTPSまたはlocalhostで開いてください。');
  browserStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment',width:{ideal:1920},height:{ideal:1080}},audio:false});
  const video=$('browser-camera');video.hidden=false;video.srcObject=browserStream;await video.play();browserJob=jobId;
  const canvas=document.createElement('canvas');const context=canvas.getContext('2d',{willReadFrequently:false});
  let sending=false;
  browserTimer=setInterval(()=>{
    if(sending||browserJob!==jobId||video.readyState<2)return;
    sending=true;
    canvas.width=Math.min(video.videoWidth,1920);
    canvas.height=Math.round(video.videoHeight*canvas.width/video.videoWidth);
    context.drawImage(video,0,0,canvas.width,canvas.height);
    canvas.toBlob(async blob=>{
      try{
        if(!blob||browserJob!==jobId)return;
        const response=await fetch('/api/jobs/'+jobId+'/browser-frame',{method:'POST',headers:{'Content-Type':'image/jpeg','X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content},body:blob});
        if(!response.ok)throw new Error('frame upload failed');
      }catch(error){message('Webカメラ映像の送信に失敗しました。');}
      finally{sending=false;}
    },'image/jpeg',.92);
  },200);
}
$('start-form').addEventListener('submit',async event=>{
  event.preventDefault();state.busy=true;setControls();message();
  try{const form=new FormData(event.target);const data=await api('/api/jobs',{method:'POST',body:form});state.selected=data.id;state.active=data.id;state.page=1;if(form.get('kind')==='browser')await startBrowserCamera(data.id);}
  catch(error){if(state.active&&browserJob===null){try{await api('/api/jobs/'+state.active+'/stop',{method:'POST'});}catch(_ignored){}}message(error.message);}finally{state.busy=false;await refresh();setControls();}
});
$('stop').addEventListener('click',async()=>{
  if(!state.active)return;state.busy=true;setControls();
  try{const job=state.active;await api('/api/jobs/'+job+'/stop',{method:'POST'});if(browserJob===job)await stopBrowserCamera();message('停止を要求しました。');}
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
    const image=cell('');if(item.has_image){const a=document.createElement('a');a.textContent='画像を確認';a.href='/api/observations/'+encodeURIComponent(item.id)+'/image';a.target='_blank';a.rel='noopener';image.append(a);}
    const register=document.createElement('button');register.type='button';register.className='registration-import';register.textContent='登録に取り込む';register.onclick=()=>importRegistration(item.id);image.append(register);tr.append(image);$('results').append(tr);
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
    for(const job of data.jobs){const row=document.createElement('div');row.className='job';const detail=document.createElement('div');const title=document.createElement('strong');title.textContent=job.label;const info=document.createElement('small');info.className='sub';info.textContent=date(job.created_at)+' · '+(job.kind==='camera'?'カメラ':job.kind==='browser'?'端末Webカメラ':'ファイル')+' · '+labels[job.status];detail.append(title,info);
      if(job.error){const error=document.createElement('p');error.className='error-text';error.textContent=job.error;detail.append(error);}const button=document.createElement('button');button.textContent='表示';button.onclick=()=>{state.selected=job.id;$('job-filter').value=job.id;state.page=1;refresh();};row.append(detail,button);$('jobs').append(row);}
    $('job-status').textContent=current?(current.status==='running'&&current.progress.phase==='loading'?'モデル準備中':labels[current.status]):'待機中';
    $('frame-count').textContent=current?.progress.frames_processed??'—';$('observation-count').textContent=current?.progress.observations??'—';$('source-label').textContent=current?.label||'未選択';
    const show=!!current?.has_preview;$('preview').hidden=!show;$('preview-empty').hidden=show;
    if(show)$('preview').src='/api/jobs/'+current.id+'/preview?t='+Date.now();
    await renderObservations(); await renderAlerts();$('connection').textContent='接続中';
  }catch(error){$('connection').textContent='接続エラー';message(error.message);}finally{state.refreshing=false;setControls();}
}
for(const id of ['job-filter','vehicle-filter'])$(id).addEventListener('change',()=>{state.page=1;refresh();});
$('prev').onclick=()=>{state.page--;refresh();};$('next').onclick=()=>{state.page++;refresh();};$('refresh').onclick=refresh;
async function deleteSelectedHistory(){
  const scopes=[];
  if($('delete-processing-history').checked)scopes.push('processing');
  if($('delete-recognition-history').checked)scopes.push('recognition');
  if(!scopes.length){message('削除する履歴を選択してください。');return;}
  try{
    const summary=await api('/api/history/summary');
    const targets=[];
    if(scopes.includes('processing'))targets.push('処理履歴 '+summary.processing+'件');
    if(scopes.includes('recognition'))targets.push('認識履歴 '+summary.recognition+'件');
    const retained='登録車両 '+summary.vehicles_retained+'件、通知 '+summary.alerts_retained+'件、OCR学習データ '+summary.learning_samples_retained+'件は残ります。';
    if(!confirm(targets.join('、')+'を一括削除します。\n'+retained+'\nこの操作は元に戻せません。'))return;
    state.busy=true;setControls();
    const result=await api('/api/history',{method:'DELETE',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({scopes,confirmation:'DELETE HISTORY'})});
    state.selected=null;state.page=1;$('job-filter').value='';
    const warning=result.file_errors.length?' 一部ファイルを削除できませんでした: '+result.file_errors.join('、'):'';
    message('処理履歴 '+result.deleted.processing+'件、認識履歴 '+result.deleted.recognition+'件を削除しました。'+warning);
  }catch(error){message(error.message);}
  finally{state.busy=false;await refresh();setControls();}
}
$('delete-history').onclick=deleteSelectedHistory;
refresh();setInterval(refresh,1500);

let alertPage=1;
const vehicleNames={car:'乗用車',kei:'軽自動車',motorcycle:'二輪車',bus:'バス',truck:'トラック'};
const deliveryNames={pending:'送信待ち',sending:'送信中',sent:'メールサーバー受付済み',retry:'再試行待ち',failed:'失敗',disabled:'未設定',waiting:'保存待ち',uploaded:'S3保存済み'};
let registrationDraft=null, registrationRequest=0;
function resetVehicle(){
  registrationRequest++;registrationDraft=null;
  if(typeof resetLearning==='function')resetLearning();
  $('vehicle-form').reset();$('vehicle-id').value='';$('vehicle-enabled').checked=true;
  $('registration-review').hidden=true;$('registration-image').hidden=true;$('registration-image').removeAttribute('src');
  $('registration-candidate').replaceChildren();
}
function chooseRegistrationCandidate(){
  const candidate=registrationDraft?.plate_candidates[Number($('registration-candidate').value)];
  for(const id of ['region','category','kana','serial'])$(id).value=candidate?.fields?.[id]??'';
  // Initialize the learning answers after the selected OCR values are shown.
  if(typeof prepareLearning==='function')prepareLearning();
  const plateHint=candidate?.kei_strength==='strong'?' · 軽自動車プレートとして検出':
    candidate?.kei_strength==='review'?' · 図柄入り軽ナンバーの可能性あり（車種を確認）':'';
  $('registration-confidence').textContent='車種の信頼度: '+pct(registrationDraft?.confidence)+' / OCR: '+pct(candidate?.confidence)+
    (!candidate?.fields?' · ナンバーを読み取れません。再撮影または手入力してください。':candidate.confidence<.7?' · OCR 70%未満のため通常結果には表示されません。修正・確認してください。':' · 内容を確認して登録してください。')+plateHint;
}
async function importRegistration(observationId){
  const requestId=++registrationRequest;
  try{
    const draft=await api('/api/observations/'+encodeURIComponent(observationId)+'/registration');
    if(requestId!==registrationRequest)return;
    resetVehicle();registrationDraft=draft;
    $('registered-type').value=draft.vehicle_type;
    $('registration-source').textContent='取り込んだ読取結果: '+date(draft.processed_at)+' · フレーム '+draft.frame_index;
    const options=draft.plate_candidates.map((c,i)=>new Option((i+1)+': '+(c.text||'候補')+' / '+pct(c.confidence),String(i)));
    $('registration-candidate').replaceChildren(...(options.length?options:[new Option('候補なし — 手入力または再撮影','')]));
    $('registration-candidate').disabled=!options.length;
    $('registration-image').hidden=!draft.has_image;
    if(draft.has_image)$('registration-image').src='/api/observations/'+encodeURIComponent(draft.observation_id)+'/image';
    chooseRegistrationCandidate();$('registration-review').hidden=false;
    $('vehicle-form').scrollIntoView({behavior:'smooth'});$('region').focus({preventScroll:true});
    message('読み取り結果を取り込みました。内容を確認し「登録・更新」で保存してください。');
  }catch(error){if(requestId===registrationRequest)message(error.message);}
}
$('registration-candidate').onchange=chooseRegistrationCandidate;
$('registration-image').onerror=()=>{$('registration-image').hidden=true;};
$('registration-camera').onclick=()=>{
  const selected=document.querySelector('input[name="kind"]:checked');
  if(selected?.value==='file'){
    const camera=document.querySelector('input[name="kind"][value="browser"]');camera.checked=true;camera.dispatchEvent(new Event('change'));
  }
  $('start-form').scrollIntoView({behavior:'smooth'});
  message('カメラを選んで認識を開始し、対象車両の履歴から「登録に取り込む」を押してください。');
};
$('vehicle-reset').onclick=resetVehicle;
async function loadVehicles(){
  const data=await api('/api/vehicles');$('vehicle-list').replaceChildren();
  for(const v of data.items){const row=document.createElement('div');row.className='job';const desc=document.createElement('div');desc.textContent=v.plate+' · '+vehicleNames[v.vehicle_type]+' · '+v.label+' · '+(v.enabled?'有効':'無効')+(v.watch?' · 通知対象':'');const edit=document.createElement('button');edit.textContent='編集';edit.onclick=()=>{resetVehicle();const parts=v.plate_key.split('|');['region','category','kana','serial'].forEach((id,i)=>$(id).value=parts[i]);$('vehicle-id').value=v.id;$('registered-type').value=v.vehicle_type;$('vehicle-label').value=v.label;$('watch').checked=!!v.watch;$('vehicle-enabled').checked=!!v.enabled;$('vehicle-form').scrollIntoView({behavior:'smooth'});};const remove=document.createElement('button');remove.textContent='削除';remove.onclick=()=>deleteVehicle(v);row.append(desc,edit,remove);$('vehicle-list').append(row);}
}
$('vehicle-form').onsubmit=async event=>{
  event.preventDefault();const payload={region:$('region').value,category:$('category').value,kana:$('kana').value,serial:$('serial').value,vehicle_type:$('registered-type').value,label:$('vehicle-label').value,watch:$('watch').checked,enabled:$('vehicle-enabled').checked};
  try{if(typeof learningPayload==='function'&&$('learning-with-registration').checked)payload.learning=learningPayload();await api('/api/vehicles'+($('vehicle-id').value?'/'+$('vehicle-id').value:''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});resetVehicle();await loadVehicles();message(payload.learning?'登録車両と学習データを保存しました。再学習後にモデルへ反映できます。':'登録車両を保存しました。');}catch(error){message(error.message);}
};
async function renderAlerts(){
  const data=await api('/api/alerts?page='+alertPage);$('unread-count').textContent=data.unread+'件 未確認';
  $('delivery-state').textContent='メール: '+(data.email_configured?'設定済み':'未設定')+' / S3: '+(data.s3_configured?'設定済み':'未設定');
  $('alert-list').replaceChildren();if(!data.items.length)$('alert-list').textContent='通知はありません';
  for(const item of data.items){
    const card=document.createElement('article');card.className='alert-card '+item.reason+(item.acknowledged?' acknowledged':'');
    const heading=document.createElement('div');heading.className='alert-title';const title=document.createElement('strong');title.textContent=item.reason_label+' · '+(item.plate||'読取不可')+' · '+vehicleNames[item.vehicle_type];const time=document.createElement('small');time.textContent=date(item.created_at);heading.append(title,time);
    const detail=document.createElement('div');detail.className='alert-detail';detail.textContent='映像: '+({recording:'録画中',ready:'保存済み',partial:'短縮保存（入力終了・停止）',failed:'保存失敗'}[item.media_status])+' / メール: '+deliveryNames[item.email_status]+' / S3: '+deliveryNames[item.s3_status];
    if(item.media_error)detail.textContent+=' / '+item.media_error;if(item.email_error)detail.textContent+=' / メールエラー: '+item.email_error;
    const actions=document.createElement('div');actions.className='alert-actions';
    if(item.has_media){const link=document.createElement('a');link.href='/api/alerts/'+item.id+'/media';link.target='_blank';link.rel='noopener';link.textContent='保存映像・画像を開く';actions.append(link);}
    if(!item.acknowledged){const button=document.createElement('button');button.textContent='確認済みにする';button.onclick=async()=>{try{await api('/api/alerts/'+item.id+'/ack',{method:'POST'});await renderAlerts();}catch(error){message(error.message);}};actions.append(button);}
    if(data.email_configured&&['failed','disabled'].includes(item.email_status)){const retry=document.createElement('button');retry.textContent='メール再送';retry.onclick=async()=>{try{await api('/api/alerts/'+item.id+'/retry-email',{method:'POST'});await renderAlerts();}catch(error){message(error.message);}};actions.append(retry);}
    card.append(heading,detail,actions);$('alert-list').append(card);
  }
  const pages=Math.max(1,Math.ceil(data.total/data.page_size));$('alerts-page').textContent=alertPage+' / '+pages;$('alerts-prev').disabled=alertPage<=1;$('alerts-next').disabled=alertPage>=pages;
}
$('alerts-prev').onclick=()=>{alertPage--;renderAlerts().catch(e=>message(e.message));};$('alerts-next').onclick=()=>{alertPage++;renderAlerts().catch(e=>message(e.message));};
loadVehicles().catch(e=>message(e.message));

async function deleteVehicle(vehicle){
  if(!confirm(vehicle.plate+' の登録を削除しますか？ 次回以降は未登録車両として扱われます。認識履歴と学習データは残ります。'))return;
  try{
    await api('/api/vehicles/'+encodeURIComponent(vehicle.id),{method:'DELETE'});
    if($('vehicle-id').value===vehicle.id)resetVehicle();
    await loadVehicles();message('登録車両を削除しました。');
  }catch(error){message(error.message);}
}
