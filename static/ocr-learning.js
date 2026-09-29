'use strict';
const learningFields=['region','category','kana','serial'];
let learningImage=null, learningGeneration=0, learningDraft=null;
function resetLearning(){
  learningGeneration++;learningImage=null;
  $('learning-review').hidden=true;
  $('learning-confirm').checked=false;
}
function fieldBox(name){return ['x1','y1','x2','y2'].map(part=>Number($('learning-'+name+'-'+part).value)/100);}
function drawLearning(){
  $('learning-confirm').checked=false;
  if(!learningImage)return;
  for(const name of learningFields){
    const [x1,y1,x2,y2]=fieldBox(name);
    const canvas=$('learning-'+name+'-image');
    canvas.width=Math.max(1,Math.round((x2-x1)*learningImage.width));
    canvas.height=Math.max(1,Math.round((y2-y1)*learningImage.height));
    if(x2>x1&&y2>y1&&x1>=0&&y1>=0&&x2<=1&&y2<=1)
      canvas.getContext('2d').drawImage(learningImage,Math.round(x1*learningImage.width),Math.round(y1*learningImage.height),
        Math.round(x2*learningImage.width)-Math.round(x1*learningImage.width),Math.round(y2*learningImage.height)-Math.round(y1*learningImage.height),0,0,canvas.width,canvas.height);
  }
}
function prepareLearning(savedFields=null){
  resetLearning();
  if(!learningDraft?.has_image||!learningDraft.plate_candidates.length)return;
  const candidate=learningDraft.plate_candidates[Number($('learning-candidate').value)];
  const detected=candidate?.fields||{};
  const defaults={region:[0,0,.55,.45],category:[.55,0,1,.45],kana:[0,.45,.2,1],serial:[.2,.45,1,1]};
  for(const name of learningFields){
    const box=savedFields?.[name]?.box||defaults[name];
    ['x1','y1','x2','y2'].forEach((part,i)=>$('learning-'+name+'-'+part).value=String(box[i]*100));
    // Registration fields are also the learning answers. OCR values are the
    // initial before-correction data; saved reviews take precedence.
    $('learning-text-'+name).value=savedFields?.[name]?.text??detected[name]??'';
  }
  const generation=learningGeneration;
  const img=new Image();
  img.onload=()=>{if(generation!==learningGeneration)return;learningImage=img;$('learning-review').hidden=false;$('learning-full-image').src=img.src;drawLearning();};
  img.onerror=()=>{if(generation===learningGeneration)message('この候補は学習画像として取得できません。画像保存設定と候補枠を確認してください。');};
  img.src='/api/ocr-learning/preview/'+encodeURIComponent(learningDraft.observation_id)+'/'+Number($('learning-candidate').value);
}
async function importLearningObservation(observationId, candidateIndex=0, savedFields=null){
  const draft=await api('/api/observations/'+encodeURIComponent(observationId)+'/registration');
  learningDraft=draft;
  $('learning-candidate').replaceChildren(...draft.plate_candidates.map((item,index)=>new Option((index+1)+': '+(item.text||'候補'),String(index))));
  $('learning-candidate').value=String(candidateIndex);
  showPage('learning');prepareLearning(savedFields);
  $('learning-review').scrollIntoView({behavior:'smooth'});
}
$('learning-candidate').onchange=()=>prepareLearning();
function learningPayload(requireConfirmation=true){
  if(!learningImage||!learningDraft)throw new Error('学習用画像を読み込めません。画像の保存と候補の範囲を確認してください。');
  if(requireConfirmation&&!$('learning-confirm').checked)throw new Error('4項目それぞれの学習画像と正解を確認してください。');
  const fields={};
  for(const name of learningFields)fields[name]={text:$('learning-text-'+name).value,box:fieldBox(name)};
  return {observation_id:learningDraft.observation_id,candidate_index:Number($('learning-candidate').value),fields,confirmed:true};
}
for(const name of learningFields){
  for(const part of ['x1','y1','x2','y2'])$('learning-'+name+'-'+part).oninput=drawLearning;
  $('learning-text-'+name).addEventListener('input',()=>{$('learning-confirm').checked=false;});
}
$('learning-save').onclick=async()=>{
  try{
    const payload={learning:learningPayload()};
    for(const id of learningFields)payload[id]=$('learning-text-'+id).value;
    await api('/api/ocr-learning/samples',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    message('確認したナンバーと画像を保存しました。');
    await refreshLearning();
  }catch(error){message(error.message);}
};
let learningRefreshBusy=false;
async function refreshLearning(){
  if(learningRefreshBusy)return;
  learningRefreshBusy=true;
  try{
    const data=await api('/api/ocr-learning');
    const autoCounts=data.automatic_counts||{};
    $('learning-status').textContent='手動確認済み '+data.count+'件 / 自動確認済み（学習用） '+(data.automatic_confirmed||0)+'件 / 確認待ち '+(autoCounts.pending||0)+'件 / 運用方式: Lipla-jp';
    $('learning-auto-candidates').replaceChildren();
    for(const item of data.automatic||[]){
      const row=document.createElement('div');row.className='job';
      const label=document.createElement('span');label.textContent=item.plate_key+' · OCR '+Math.round(item.confidence*100)+'% · '+(item.last_error?'自動保存できません: '+item.last_error:item.status==='pseudo'?'学習用候補':'確認待ち');
      const review=document.createElement('button');review.textContent='確認・修正';
      review.onclick=async()=>{try{await importLearningObservation(item.observation_id,item.candidate_index);}catch(error){message(error.message);}};
      const exclude=document.createElement('button');exclude.textContent='学習から除外';
      exclude.onclick=async()=>{try{await api('/api/ocr-learning/auto/'+encodeURIComponent(item.observation_id)+'/'+item.candidate_index,{method:'DELETE'});await refreshLearning();}catch(error){message(error.message);}};
      row.append(label,review,exclude);$('learning-auto-candidates').append(row);
    }
    const training=await api('/api/paddle-training');
    $('paddle-training-status').textContent='PaddleOCR追加学習: '+({idle:'待機',running:'学習中',completed:'完了（未適用）',failed:'失敗',interrupted:'中断',insufficient_data:'データ不足',unavailable:'学習環境未準備'}[training.state]||training.state)+(training.error?' / '+training.error:'');
    $('paddle-retry').hidden=training.state!=='failed'||!!training.retry_queued;
    const stage={detector:'プレート検出器',recognizer:'PaddleOCR',validation:'評価',export:'推論形式への変換'}[training.stage]||training.stage;
    const counts=training.report||{};
    $('paddle-training-details').textContent=[stage?'工程: '+stage:'',counts.det_train!==undefined?'検出器 学習 '+counts.det_train+'件 / 評価 '+counts.det_val+'件':'',training.exit_code!==undefined?'終了コード: '+training.exit_code:''].filter(Boolean).join(' / ');
    $('paddle-training-log').textContent=training.recent_log||'ログはまだありません。';
    const comparison=training.comparison||{state:'idle'};
    $('paddle-compare').disabled=training.state!=='completed'||comparison.state==='running';
    $('paddle-compare-status').textContent='同一画像での比較: '+({idle:'未実施',running:'実行中',completed:'完了',failed:'失敗',interrupted:'中断'}[comparison.state]||comparison.state)+(comparison.error?' / '+comparison.error:'');
    const target=$('paddle-comparison');target.replaceChildren();
    const report=comparison.report;
    if(report&&comparison.dataset===training.dataset){
      const caption=document.createElement('p');caption.textContent=(report.evaluation_partition==='test'?'学習・モデル選択に未使用のテスト画像':'検証用画像（学習中のモデル選択に使用）')+' '+report.evaluated+'件（除外 '+report.skipped+'件） / IoU 0.50 / 車両画像からの処理時間';target.append(caption);
      const table=document.createElement('table');
      const head=document.createElement('tr');
      for(const title of ['方式','検出率','ナンバー完全一致率','平均遅延 (ms)','95%遅延 (ms)','処理速度 (FPS)']){const cell=document.createElement('th');cell.textContent=title;head.append(cell);}table.append(head);
      for(const [title,key] of [['Lipla-jp','lipla'],['学習済みPaddleOCR','paddle']]){
        const metrics=report[key];if(!metrics)continue;
        const row=document.createElement('tr');
        for(const value of [title,(metrics.plate_recall_at_iou_50*100).toFixed(1)+'%',(metrics.exact_plate_accuracy*100).toFixed(1)+'%',metrics.average_latency_ms.toFixed(1),metrics.p95_latency_ms.toFixed(1),metrics.throughput_fps.toFixed(1)]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}table.append(row);
      }target.append(table);
      const paired=document.createElement('p');paired.textContent='両方正解 '+report.paired.both_correct+'件 / PaddleOCRのみ正解 '+report.paired.paddle_only+'件 / Lipla-jpのみ正解 '+report.paired.lipla_only+'件';target.append(paired);
      if(report.diagnostics?.length){
        const isolated=document.createElement('p');isolated.textContent='正しいプレート画像でのPaddleOCR単体完全一致率: '+(report.ocr_isolated_exact*100).toFixed(1)+'%';target.append(isolated);
        const guide=document.createElement('p');guide.className='hint';guide.textContent='緑枠: Lipla-jpの確認済み位置 / 青枠: 専用検出器の候補（信頼度0.01以上）。通常の判定は信頼度0.20以上、幅48px・高さ24px以上、IoU 0.50以上です。';target.append(guide);
        for(const item of report.diagnostics){
          const detail=document.createElement('details');const title=document.createElement('summary');
          title.textContent='サンプル '+item.sample.slice(0,8)+' / 候補 '+item.detection.boxes.length+'件 / 最大IoU '+item.detection.max_iou.toFixed(2)+' / OCR単体 '+(item.ocr.exact?'一致':'不一致');detail.append(title);
          const img=document.createElement('img');img.loading='lazy';img.style.maxWidth='100%';img.alt='正解枠と検出候補枠';img.src='/api/paddle-training/diagnostics/'+encodeURIComponent(item.sample)+'/image';detail.append(img);
          const reasons=document.createElement('p');reasons.textContent=item.detection.boxes.map(b=>'確度 '+b.confidence.toFixed(2)+' / IoU '+b.iou.toFixed(2)+' / '+({low_confidence:'低信頼度',small_box:'小さい枠',low_iou:'位置ずれ',matched:'検出成功'}[b.reason]||b.reason)).join('、')||'候補なし';detail.append(reasons);
          const ocr=document.createElement('p');ocr.textContent='正しい範囲からのOCR ('+(item.ocr.layout==='four_fields'?'4項目':'上下2行')+'): '+item.ocr.crops.map(c=>c.truth+' → '+(c.prediction||'空')).join(' / ');detail.append(ocr);target.append(detail);
        }
      }
    }
    $('learning-samples').replaceChildren();
    for(const sample of data.samples){
      const row=document.createElement('div');const label=document.createElement('span');
      label.textContent=sample.top_text+' / '+sample.bottom_text+(sample.source==='automatic'?' （自動確認済み・Lipla推定値） ':' （手動確認済み・元のOCR: '+sample.original_text+'） ');
      const button=document.createElement('button');button.textContent='学習対象から削除';
      button.onclick=async()=>{try{await api(sample.source==='automatic'?'/api/ocr-learning/auto/'+encodeURIComponent(sample.observation_id)+'/'+sample.candidate_index:'/api/ocr-learning/samples/'+sample.id,{method:'DELETE'});await refreshLearning();}catch(error){message(error.message);}};
      const edit=document.createElement('button');edit.textContent='正解・画像範囲を編集';
      edit.disabled=!sample.has_observation;
      if(!sample.has_observation)edit.title='元の認識履歴は削除されています。学習データ自体は保持されています。';
      edit.onclick=async()=>{
        try{
          await importLearningObservation(sample.observation_id,sample.candidate_index,sample.fields);
          const values=sample.plate_key.split('|');learningFields.forEach((name,i)=>$('learning-text-'+name).value=sample.fields?.[name]?.text??values[i]);
        }catch(error){message(error.message);}
      };
      row.append(label,edit,button);$('learning-samples').append(row);
    }
  }finally{learningRefreshBusy=false;}
}
$('learning-refresh').onclick=()=>refreshLearning().catch(e=>message(e.message));
$('paddle-retry').onclick=async()=>{try{await api('/api/paddle-training/retry',{method:'POST'});$('paddle-retry').hidden=true;message('再学習を予約しました。約30秒後に状態を確認してください。');}catch(e){message(e.message);}};
$('paddle-compare').onclick=async()=>{try{await api('/api/paddle-training/compare',{method:'POST'});message('比較を予約しました。');await refreshLearning();}catch(e){message(e.message);}};
refreshLearning().catch(e=>message(e.message));
setInterval(()=>refreshLearning().catch(()=>{}),10000);
