'use strict';
const fs=require('fs'),crypto=require('crypto');
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const CATEGORY_PREFIX={'图片':'I','文本':'T','模块':'M','背景':'B'};
function loadUserRegistry(input){
  if(!input)return null;
  const bytes=typeof input==='string'?fs.readFileSync(input):Buffer.from(JSON.stringify(input));
  const registry=typeof input==='string'?JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/,'')):input;
  if(registry.schema!=='dt-user-element-registry-v1'||!registry.pieces)throw Error('Unsupported user identity registry');
  return {registry,sha256:hash(bytes),source:typeof input==='string'?input:'explicit object'};
}
function pieceFor(layout,strict=true){const p=String(layout.id).replace(/^V2-/,'');if(strict&&!/^[PDH]\d{2}$/.test(p))throw Error('Unsupported piece identity');return p;}
function userIdentityFor(layout,e,loaded){
  const piece=pieceFor(layout,Boolean(loaded));
  if(!loaded)return {internal_id:e.id,psd_layer_id:parseInt(hash(e.id).slice(0,7),16),short_id:e.id,qualified_id:piece+'/'+e.id,category:null,active:true};
  const row=loaded.registry.pieces[piece]?.entries[e.id];
  const category=e.type==='text'?'文本':e.role==='background'?'背景':e.type==='image'?'图片':'模块';
  if(!row||!row.active||row.category!==category||row.internal_id!==e.id||row.psd_layer_id!==parseInt(hash(e.id).slice(0,7),16)||row.qualified_id!==piece+'/'+row.short_id||!row.short_id.startsWith(CATEGORY_PREFIX[row.category]))throw Error('Invalid/missing/retired user identity: '+e.id);
  return row;
}
function resolveReference(layout,reference,loaded){
  if(typeof reference!=='string')throw Error('Element reference must be a string');
  if(layout.elements.some(e=>e.id===reference))return reference;
  if(reference.startsWith('V2-'))throw Error('Unknown internal element reference');
  if(!loaded)throw Error('Short reference requires a persistent user registry');
  const piece=pieceFor(layout),pieces=reference.split('/');
  if(pieces.length>2||(pieces.length===2&&pieces[0]!==piece))throw Error('Cross-piece reference mismatch');
  const short=pieces.at(-1),rows=Object.values(loaded.registry.pieces[piece]?.entries||{}).filter(r=>r.short_id===short&&r.active&&layout.elements.some(e=>e.id===r.internal_id));
  if(rows.length!==1)throw Error('Unknown/ambiguous/retired short reference');
  return rows[0].internal_id;
}
function assertRegistryFrozen(baseline,loaded){
  const expected=baseline?.userIdentity?.registrySHA256;
  if(expected&&(!loaded||loaded.sha256!==expected))throw Error('Persistent user registry changed since the frozen render; explicit mapping migration/new baseline required');
}
function identityXMP(layout,entries){
  const content=JSON.stringify({schema:'dt-psd-internal-element-identity-v1',piece:pieceFor(layout,false),entries});
  const escaped=content.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  return '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" xmlns:dt="https://dt.local/ns/element-id/1.0/"><dt:elementIdentityMap>'+escaped+'</dt:elementIdentityMap></rdf:Description></rdf:RDF></x:xmpmeta>';
}
module.exports={loadUserRegistry,userIdentityFor,pieceFor,resolveReference,identityXMP,assertRegistryFrozen};
