'use strict';
// Generic export integrity gate. No fixed screen, element ID, font, or text-layer count.
function review(layout,parts){
 const errors=[],rows=[];const allowances=layout.exportReview?.allowOcclusion||[];
 for(let i=0;i<parts.length;i++){
  const a=parts[i],e=layout.elements[i],w=a.canvas.width,h=a.canvas.height;let ink=0,outside=0,overlap=0,blocked=0;
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
   if(!a.pixels[(y*w+x)*4+3]||(e.opacity??1)===0)continue;ink++;
   const px=x+e.x,py=y+e.y;if(px<0||py<0||px>=layout.width||py>=layout.height){outside++;continue;}
   let trans=1;
   for(let j=i+1;j<parts.length;j++){
    const b=parts[j],be=layout.elements[j],bx=Math.floor(px-be.x),by=Math.floor(py-be.y);
    if(bx>=0&&by>=0&&bx<b.canvas.width&&by<b.canvas.height)trans*=1-b.pixels[(by*b.canvas.width+bx)*4+3]/255*(be.opacity??1);
   }
   if(trans<0.999)overlap++;if(trans<0.01)blocked++;
  }
  const declared=allowances.find(x=>x.id===e.id&&typeof x.reason==='string'&&x.reason.trim().length>3);
  const intrinsic=e.role==='background'||e.role==='contact-shadow';
  const reasons=[];
  if(outside)reasons.push('visible_ink_outside_page');
  if(!intrinsic&&!declared&&((e.type==='text'||e.type==='image')?overlap>0:(ink>0&&blocked===ink)))reasons.push('unreviewed_occlusion');
  if(!ink&&(e.opacity??1)>0&&e.role!=='contact-shadow')reasons.push('empty_visible_component');
  if(a.warnings.length)reasons.push(...a.warnings);
  if(e.type==='image'&&e.fit==='cover'&&!declared)reasons.push('cover_crop_requires_review');
  if(reasons.length)errors.push({id:e.id,reasons});
  rows.push({id:e.id,type:e.type,role:e.role||null,inkPixels:ink,pageClippedInkPixels:outside,overlappedInkPixels:overlap,mostlyBlockedInkPixels:blocked,occlusionApproval:declared?.reason|| (intrinsic?'semantic background/contact-shadow':'none'),warnings:a.warnings});
 }
 return {schema:'generic-export-integrity-v1',pass:errors.length===0,errors,layers:rows,scope:'Actual layer alpha, page bounds, text ink preflight, image cover crop review. Fractional layer positions use nearest pixel sampling; visual review remains required.',limitations:['Does not detect defects already baked into a flattened source image','Does not prove Photoshop application reflow compatibility','Intentional overlap requires an explicit reviewed reason for the element']};
}
module.exports={review};
