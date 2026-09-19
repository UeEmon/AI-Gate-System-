'use strict';
let learningImage=null, learningGeneration=0;
function resetLearning(){
  learningGeneration++;learningImage=null;
  $('learning-review').hidden=true;
  $('learning-confirm').checked=false;$('learning-with-registration').checked=false;
  $('learning-top').value='';$('learning-bottom').value='';
}
function drawLearning(){
  $('learning-confirm').checked=false;
  if(!learningImage)return;
  const split=Math.round(learningImage.height*Number($('learning-split').value)/100);
  for(const [id,y,h] of [['learning-top-image',0,split],['learning-bottom-image',split,learningImage.height-split]]){
    const canvas=$(id);canvas.width=learningImage.width;canvas.height=h;
    canvas.getContext('2d').drawImage(learningImage,0,y,learningImage.width,h,0,0,canvas.width,h);
  }
}
function prepareLearning(){
  resetLearning();
  if(!registrationDraft?.has_image||!registrationDraft.plate_candidates.length)return;
  const generation=learningGeneration;
  const img=new Image();
  img.onload=()=>{if(generation!==learningGeneration)return;learningImage=img;$('learning-review').hidden=false;$('learning-split').value='45';drawLearning();};
  img.onerror=()=>{if(generation===learningGeneration)message('この候補は学習画像として取得できません。画像保存設定と候補枠を確認してください。');};
  img.src='/api/ocr-learning/preview/'+encodeURIComponent(registrationDraft.observation_id)+'/'+Number($('registration-candidate').value);
}
function learningPayload(){
  if(!learningImage||!registrationDraft||!$('learning-confirm').checked)throw new Error('学習用画像と上下段の正解を確認してください。');
  return {observation_id:registrationDraft.observation_id,candidate_index:Number($('registration-candidate').value),
    top_text:$('learning-top').value,bottom_text:$('learning-bottom').value,
    split:Number($('learning-split').value)/100,confirmed:true};
}
$('learning-split').oninput=drawLearning;
for(const id of ['learning-top','learning-bottom','region','category','kana','serial'])$(id).addEventListener('input',()=>{$('learning-confirm').checked=false;});
$('learning-fill').onclick=()=>{
  $('learning-top').value=$('region').value+$('category').value;
  $('learning-bottom').value=$('kana').value+$('serial').value;
  $('learning-confirm').checked=false;
};
$('learning-save').onclick=async()=>{
  try{
    const payload={learning:learningPayload()};
    for(const id of ['region','category','kana','serial'])payload[id]=$(id).value;
    await api('/api/ocr-learning/samples',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    message('修正内容と画像を学習データとして保存しました。モデルへの反映には再学習が必要です。');
    await refreshLearning();
  }catch(error){message(error.message);}
};
async function activateLearning(id){
  try{await api('/api/ocr-learning/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});await refreshLearning();message('モデルを選択しました。認識処理を停止して再開すると反映されます。');}catch(error){message(error.message);}
}
let learningRefreshBusy=false;
async function refreshLearning(){
  if(learningRefreshBusy)return;
  learningRefreshBusy=true;
  try{
    const data=await api('/api/ocr-learning');
    $('learning-status').textContent='保存済み '+data.count+'件 / 使用モデル: '+(data.active||'標準OCR');
    $('learning-train').disabled=data.runs.some(r=>r.status==='running');
    $('learning-runs').replaceChildren();
    const names={running:'学習中',completed:'完了',failed:'失敗',interrupted:'中断'};
    for(const run of data.runs){
      const row=document.createElement('div');
      const label=document.createElement('p');
      label.textContent=date(run.created_at)+' · '+(names[run.status]||run.status)+(run.error?' · '+run.error:'');
      if(run.report){const r=run.report;label.textContent+=' · 比較元: '+(r.baseline_model||'標準OCR')+' · 評価 '+r.validation_lines+'行 · 文字誤り率 '+(r.baseline.cer*100).toFixed(1)+'% → '+(r.candidate.cer*100).toFixed(1)+'% · 行一致率 '+(r.baseline.line_accuracy*100).toFixed(1)+'% → '+(r.candidate.line_accuracy*100).toFixed(1)+'% · '+(r.eligible?'適用可能':'改善基準に未達');}
      row.append(label);
      if(run.report?.eligible){const button=document.createElement('button');button.textContent='このモデルを適用';button.disabled=data.active===run.id;button.onclick=()=>activateLearning(run.id);row.append(button);}
      $('learning-runs').append(row);
    }
    $('learning-samples').replaceChildren();
    for(const sample of data.samples){
      const row=document.createElement('div');const label=document.createElement('span');
      label.textContent=sample.top_text+' / '+sample.bottom_text+' （元のOCR: '+sample.original_text+'） ';
      const button=document.createElement('button');button.textContent='学習対象から削除';
      button.onclick=async()=>{try{await api('/api/ocr-learning/samples/'+sample.id,{method:'DELETE'});await refreshLearning();}catch(error){message(error.message);}};
      row.append(label,button);$('learning-samples').append(row);
    }
  }finally{learningRefreshBusy=false;}
}
$('learning-train').onclick=async()=>{
  $('learning-train').disabled=true;
  try{await api('/api/ocr-learning/train',{method:'POST'});message('再学習を開始しました。状態は自動更新されます。');}catch(error){message(error.message);}finally{await refreshLearning().catch(e=>message(e.message));}
};
$('learning-reset').onclick=()=>activateLearning(null);
$('learning-refresh').onclick=()=>refreshLearning().catch(e=>message(e.message));
refreshLearning().catch(e=>message(e.message));
setInterval(()=>refreshLearning().catch(()=>{}),10000);
