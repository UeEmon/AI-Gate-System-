'use strict';
let csvValidated=null;
function invalidateCSV(){csvValidated=null;$('csv-commit').disabled=true;$('csv-result').textContent='';}
$('csv-file').onchange=invalidateCSV;$('csv-mode').onchange=invalidateCSV;
async function transferCSV(preview){
  const file=preview?$('csv-file').files[0]:csvValidated?.file;
  const mode=preview?$('csv-mode').value:csvValidated?.mode;
  if(!file)return;
  if(file.size>5*1024*1024){$('csv-result').textContent='CSVは5MB以下にしてください。';return;}
  $('csv-preview').disabled=true;$('csv-commit').disabled=true;$('csv-file').disabled=true;$('csv-mode').disabled=true;
  const form=new FormData();form.append('file',file);form.append('mode',mode);form.append('preview',preview?'1':'0');
  try{
    const data=await api('/api/vehicles/import.csv',{method:'POST',body:form});
    csvValidated=preview?{file,mode}:null;
    $('csv-result').textContent=(preview?'検証結果（未保存）':'取り込み完了')+': 追加 '+data.added+'件 / 更新 '+data.updated+'件 / スキップ '+data.skipped+'件';
    if(!preview)await loadVehicles();
  }catch(error){csvValidated=null;$('csv-result').textContent=error.message;}
  finally{$('csv-preview').disabled=false;$('csv-commit').disabled=!csvValidated;$('csv-file').disabled=false;$('csv-mode').disabled=false;}
}
$('csv-form').onsubmit=e=>{e.preventDefault();transferCSV(true);};$('csv-commit').onclick=()=>transferCSV(false);
