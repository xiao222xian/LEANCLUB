'use strict';
const $ = s=>document.querySelector(s);
let editKey = new URLSearchParams(location.hash.slice(1)).get('edit') || '';
let revision = null, qrValue = null;
const form = $('#club-form'), publish = $('#publish');
async function api(path, body) {
 const response=await fetch('/api/club/'+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});
 const data=await response.json(); if(!response.ok)throw new Error(data.error||'暂时无法连接，请稍后重试。');return data;
}
function error(message){$('#form-error').textContent=message;$('#form-error').hidden=!message;}
function preview(src){$('#qr-preview').src=src||'';$('#qr-preview').hidden=!src;$('#clear-qr').hidden=!src;}
async function start(){
 try {
  const catalog=await api('schools');
  for(const school of catalog.schools){const option=document.createElement('option');option.value=school.name;$('#school-options').append(option);}
  if(editKey){const {club}=await api('edit',{editKey});$('#school').value=club.name;$('#school').readOnly=true;$('#contact').value=club.contact;$('#count').value=club.count;revision=club.revision;preview(club.qr);publish.firstChild.textContent='保存并发布 ';}
  $('#loading').hidden=true;form.hidden=false;
 }catch(e){$('#loading').textContent=e.message;}
}
$('#qr').addEventListener('change',async()=>{
 error('');const file=$('#qr').files[0];if(!file)return;
 if(!['image/jpeg','image/png'].includes(file.type)||file.size>2*1024*1024){$('#qr').value='';error('请选择2MB以内的JPG或PNG二维码图片。');return;}
 try {qrValue=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('图片读取失败，请重试。'));reader.readAsDataURL(file);});preview(qrValue);}catch(e){error(e.message);}
});
$('#clear-qr').addEventListener('click',()=>{qrValue='';$('#qr').value='';preview('');});
form.addEventListener('submit',async e=>{
 e.preventDefault();error('');publish.disabled=true;publish.textContent='正在发布…';
 try {
  const data=await api('submit',{school:$('#school').value.trim(),contact:$('#contact').value.trim(),count:Number($('#count').value),qr:qrValue,editKey,revision,consent:true});
  editKey=data.editKey;revision=data.revision;
  const link=location.origin+location.pathname+'#edit='+encodeURIComponent(editKey);
  history.replaceState(null,'','#edit='+encodeURIComponent(editKey));
  $('#edit-link').value=link;$('#view-school').href='/schools?school='+encodeURIComponent(data.id);
  $('#school').readOnly=true;form.hidden=true;$('#success').hidden=false;$('#success').scrollIntoView({block:'start',behavior:'smooth'});
 }catch(e){error(e.message);}finally{publish.disabled=false;publish.innerHTML='保存并发布 <span>↗</span>';}
});
$('#copy-link').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('#edit-link').value);$('#copy-status').textContent='已复制，请妥善保存。';}catch(e){$('#edit-link').focus();$('#edit-link').select();$('#copy-status').textContent='请长按链接复制并保存。';}});
$('#edit-again').addEventListener('click',()=>{$('#success').hidden=true;form.hidden=false;form.scrollIntoView({block:'start',behavior:'smooth'});});
start();
