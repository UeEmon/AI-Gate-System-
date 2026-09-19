'use strict';
class RegistrationQueue {
  constructor(limit=100){this.limit=limit;this.items=new Map();this.cursor=0;this.skipped=0;}
  accept(rows){
    for(const row of rows){
      if(row.cursor<=this.cursor)continue;
      const draft=row.draft;
      if(draft){
        const existing=this.items.get(draft.key);
        if(existing){
          existing.reads++;
          if(existing.vehicle_type!==draft.vehicle_type){existing.conflict=true;existing.selected=false;}
        }else{
          if(this.items.size>=this.limit)return false;
          this.items.set(draft.key,{...draft,reads:1,selected:false,conflict:false});
        }
      }else{this.skipped++;}
      this.cursor=row.cursor;
    }
    return true;
  }
  selected(){return [...this.items.values()].filter(item=>item.selected&&!item.conflict);}
}
if(typeof module!=='undefined')module.exports={RegistrationQueue};
if(typeof document!=='undefined'){
  let queue=new RegistrationQueue(),job=null,active=false,busy=false,saving=false;
  let note='';
  function renderQueue(){
    $('queue-status').textContent=(active?'取り込み中':'停止中')+' · 候補 '+queue.items.size+'件 · 選択 '+queue.selected().length+'件 · 読取不可・登録済み '+queue.skipped+'レコード'+(note?' · '+note:'');
    $('queue-start').disabled=active||busy||saving;$('queue-stop').disabled=!active;
    $('queue-save').disabled=saving||!queue.selected().length;$('queue-clear').disabled=active||busy||saving;
    $('queue-list').replaceChildren();
    for(const item of queue.items.values()){
      const row=document.createElement('div');row.className='job';
      const label=document.createElement('label'),check=document.createElement('input');
      check.type='checkbox';check.checked=item.selected;check.disabled=item.conflict||saving;
      check.onchange=()=>{item.selected=check.checked;renderQueue();};
      label.append(check,document.createTextNode(' '+item.key.split('|').join(' ')+' · '+vehicleNames[item.vehicle_type]+' · OCR '+pct(item.confidence)+' · '+item.reads+'回'+(item.conflict?' · 車種不一致（個別確認が必要）':'')));
      const actions=document.createElement('div');actions.className='alert-actions';
      if(item.has_image){const image=document.createElement('a');image.textContent='画像';image.href='/api/observations/'+encodeURIComponent(item.observation_id)+'/image';image.target='_blank';image.rel='noopener';actions.append(image);}
      const edit=document.createElement('button');edit.type='button';edit.textContent='個別確認・修正';edit.disabled=saving;
      edit.onclick=()=>{item.selected=false;renderQueue();importRegistration(item.observation_id);};
      actions.append(edit);row.append(label,actions);$('queue-list').append(row);
    }
  }
  async function poll(){
    if(!active||busy||saving)return;
    busy=true;
    try{
      const data=await api('/api/registration-feed?'+new URLSearchParams({job,after:queue.cursor}));
      if(active&&!queue.accept(data.items)){active=false;note='100件に達しました。登録後に取り込みを再開してください。';}
    }catch(error){active=false;note=error.message;}
    finally{busy=false;renderQueue();}
  }
  $('queue-start').onclick=()=>{
    const selected=state.selected||state.active;
    if(!selected){message('先にカメラの認識を開始するか、処理履歴で対象の処理を表示してください。');return;}
    if(job&&job!==selected){
      if(queue.items.size){message('別の処理へ切り替える前に候補を登録するか、候補クリアを押してください。');return;}
      queue=new RegistrationQueue();
    }
    job=selected;active=true;note='';renderQueue();poll();
  };
  $('queue-stop').onclick=()=>{active=false;note='認識処理は継続します。';renderQueue();};
  $('queue-clear').onclick=()=>{queue=new RegistrationQueue();job=null;note='';renderQueue();};
  $('queue-save').onclick=async()=>{
    if(saving||busy)return;
    const selected=queue.selected();if(!selected.length)return;
    saving=true;renderQueue();
    try{
      const result=await api('/api/vehicles/batch',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({items:selected.map(item=>({...item.fields,vehicle_type:item.vehicle_type,label:'',watch:false,enabled:true}))})});
      for(const item of result.added)queue.items.delete(item.key);
      for(const key of result.skipped)queue.items.delete(key);
      note=result.added.length+'件登録、'+result.skipped.length+'件は登録済みのためスキップ';
      await loadVehicles();
    }catch(error){note=error.message;}
    finally{saving=false;renderQueue();}
  };
  setInterval(poll,1500);renderQueue();
}
