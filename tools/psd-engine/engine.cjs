'use strict';
// Original local component renderer. No network calls or dependency installation.
const fs=require('fs'), path=require('path'), crypto=require('crypto');
const {review:reviewExportIntegrity}=require('./export-integrity.cjs');
const {drawNumberedReview}=require('./numbered-review.cjs');
const {withCurrentRevision}=require('./revision-guard.cjs');
const {loadUserRegistry,userIdentityFor,resolveReference,identityXMP,assertRegistryFrozen}=require('./user-identity.cjs');
const {dependencies}=require('./package.json');

function dependencyError(message, cause) {
  const error=new Error(message,{cause});
  error.code='DT_PSD_DEPENDENCY';
  return error;
}

function isLocalAbsolute(directory) {
  return process.platform==='win32'
    ? /^[a-z]:[\\/]/i.test(directory)
    : path.isAbsolute(directory);
}

const configured=(process.env.DT_PSD_DEPENDENCIES||'').split(path.delimiter).filter(Boolean);
if(configured.some(directory=>!isLocalAbsolute(directory))) {
  throw dependencyError('DT_PSD_DEPENDENCIES must contain absolute local node_modules paths.');
}
const dependencyRoots=[path.join(__dirname,'node_modules'),...configured];

function loadDependency(name) {
  const version=dependencies[name];
  for(const root of dependencyRoots) {
    const packageDirectory=path.join(root,name);
    const packageFile=path.join(packageDirectory,'package.json');
    let metadata;
    try {
      if(!isLocalAbsolute(fs.realpathSync(packageDirectory))){
        throw dependencyError('PSD dependency must resolve to a local drive: '+name);
      }
      metadata=JSON.parse(fs.readFileSync(packageFile,'utf8'));
    } catch(error) {
      if(error.code==='ENOENT'||error.code==='ENOTDIR')continue;
      if(error.code==='DT_PSD_DEPENDENCY')throw error;
      throw dependencyError('Unable to read the PSD dependency manifest for '+name+'.',error);
    }
    if(metadata.name!==name||metadata.version!==version) {
      throw dependencyError('PSD dependency '+name+' must be exactly '+version+'.');
    }
    try {
      const entry=require.resolve(packageDirectory);
      return {lib:require(entry),path:entry,version};
    } catch(error) {
      throw dependencyError('Unable to load PSD dependency '+name+'@'+version+'.',error);
    }
  }
  throw dependencyError('Missing local PSD dependency '+name+'@'+version+
    '. Provide local node_modules or set DT_PSD_DEPENDENCIES.');
}

const canvasDependency=loadDependency('@napi-rs/canvas');
const {createCanvas,loadImage,GlobalFonts,ImageData}=canvasDependency.lib;
const sharp=loadDependency('sharp').lib;
const psdLibrary=loadDependency('ag-psd');
if(['initializeCanvas','writePsdBuffer','readPsd'].some(name=>typeof psdLibrary.lib[name]!=='function')) {
  throw dependencyError('The pinned ag-psd package does not expose the required export/readback API.');
}
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const canonical=x=>JSON.stringify(x,(_,v)=>v && typeof v==='object' && !Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const clone=x=>JSON.parse(JSON.stringify(x));
const readJSON=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
const writeJSON=(p,x)=>fs.writeFileSync(p,JSON.stringify(x,null,2)+'\n');
const H01_FONT_PREVIEW_BASELINE=path.join(__dirname,'h01-font-preview-baseline-v1');
function validate(l){
  if(l.schema!=='dt-layout-v1')throw Error('Unsupported layout schema');
  if(!Number.isInteger(l.width)||!Number.isInteger(l.height)||l.width<1||l.height<1||l.width*l.height>50000000)throw Error('Invalid or excessive canvas dimensions');
  if(!Array.isArray(l.elements)||!l.elements.length||l.elements.length>200)throw Error('Invalid element count');
  const ids=new Set();for(const e of l.elements){
    if(typeof e.id!=='string'||!/^[A-Za-z][\w-]+$/.test(e.id)||ids.has(e.id))throw Error('Invalid or duplicate element ID');ids.add(e.id);
    if(!['image','text','rect','ellipse','line'].includes(e.type))throw Error('Unsupported element type: '+e.type);
    for(const k of ['x','y','width','height'])if(!Number.isFinite(e[k]))throw Error(e.id+' invalid '+k);
    if(e.width<1||e.height<1||e.width*e.height>50000000)throw Error('Invalid element size: '+e.id);
    if(e.opacity!==undefined&&(!Number.isFinite(e.opacity)||e.opacity<0||e.opacity>1))throw Error('Invalid opacity');
    if(e.type==='text'&&(typeof e.text!=='string'||!Number.isFinite(e.fontSize)||e.fontSize<=0))throw Error('Invalid text');
    if(e.type==='image'&&typeof e.path!=='string')throw Error('Missing image path');
    if(e.radialGradient!==undefined)validateSoftShadow(e);
  }
}
function rectPath(c,w,h,r=0){c.beginPath();c.roundRect(0,0,w,h,Math.min(r,w/2,h/2));}
function validateSoftShadow(e){
  if(e.type!=='ellipse'||e.role!=='contact-shadow')throw Error('Radial soft shadow must be a separate contact-shadow ellipse');
  if(e.width<4||e.height<4)throw Error('Soft shadow bounds need at least four pixels per axis');
  if((e.opacity??1)!==1)throw Error('Soft shadow opacity must be baked in RGBA stops; PSD layer opacity remains 1');
  if(e.stroke||e.fill!==undefined)throw Error('Soft contact shadow cannot have an additional hard stroke or solid fill');
  const stops=e.radialGradient&&e.radialGradient.stops;
  if(!Array.isArray(stops)||stops.length<2||stops.length>8)throw Error('Soft shadow needs 2..8 RGBA stops');
  let last=-1;
  for(const stop of stops){
    if(!Number.isFinite(stop.offset)||stop.offset<0||stop.offset>1||stop.offset<=last)throw Error('Shadow offsets must strictly increase from 0 to 1');
    const m=typeof stop.color==='string'&&stop.color.match(/^rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*\)$/i);
    if(!m||[m[1],m[2],m[3]].some(v=>Number(v)>255))throw Error('Shadow color must be bounded rgba(r,g,b,a)');
    last=stop.offset;
  }
  if(stops[0].offset!==0||stops[stops.length-1].offset!==1)throw Error('Shadow stops need explicit center 0 and edge 1');
  const edge=stops[stops.length-1].color.match(/,\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*\)$/i);
  if(Number(edge[1])!==0)throw Error('Soft shadow edge alpha must be zero');
}
function drawSoftShadow(c,e,w,h){
  validateSoftShadow(e);
  c.save();c.scale(w,h);
  const gradient=c.createRadialGradient(0.5,0.5,0,0.5,0.5,0.5-1/Math.min(w,h));
  for(const stop of e.radialGradient.stops)gradient.addColorStop(stop.offset,stop.color);
  c.fillStyle=gradient;c.fillRect(0,0,1,1);c.restore();
}
function measureTextInk(context,e){
  const family=e.fontFamily||'Microsoft YaHei',weight=e.fontWeight||400;
  const installed=GlobalFonts.families.some(font=>font.family.toLowerCase()===family.toLowerCase());
  if(!installed)throw Error('Font unavailable, refusing silent substitution: '+family);
  context.font=weight+' '+e.fontSize+'px "'+family+'"';
  context.textBaseline='top';
  context.textAlign=e.align||'left';
  const width=Math.ceil(e.width),pad=e.padding||0,leading=e.fontSize*(e.lineHeight||1.25);
  const lines=e.text.split(/\r?\n/);
  const x=e.align==='center'?width/2:e.align==='right'?width-pad:pad;
  const y=pad+(e.textInsetY||0);
  const measured=lines.map(line=>context.measureText(line));
  const inkBounds=measured.map((m,i)=>({
    left:x-m.actualBoundingBoxLeft,right:x+m.actualBoundingBoxRight,
    top:y+i*leading-m.actualBoundingBoxAscent,bottom:y+i*leading+m.actualBoundingBoxDescent
  }));
  return {family,weight,pad,leading,lines,x,y,inkBounds,maxWidth:Math.max(...measured.map(m=>m.width))};
}
function reboxTextTop(e){
  const {inkBounds}=measureTextInk(createCanvas(1,1).getContext('2d'),e);
  const top=Math.min(...inkBounds.map(bounds=>bounds.top));
  if(top>=0)return e;
  const added=Math.ceil(-top);
  if(e.y-added<0)throw Error('Text ascender exceeds page bounds: '+e.id);
  // Increase transparent headroom while leaving every authored line origin at its original page coordinate.
  return {...e,y:e.y-added,height:e.height+added,textInsetY:(e.textInsetY||0)+added};
}
async function component(e,base){
  const w=Math.ceil(e.width),h=Math.ceil(e.height),canvas=createCanvas(w,h),c=canvas.getContext('2d');
  const warnings=[];let asset=null,textMetrics=null;
  if(e.type==='image'){
    const p=path.resolve(base,e.path);if(/^https?:/i.test(e.path))throw Error('Remote image paths are forbidden');
    const bytes=fs.readFileSync(p);let im,decoder='napi-rs/canvas',decodeFallbackReason=null;try{im=await loadImage(bytes);}catch(error){decodeFallbackReason=error.message;const raw=await sharp(bytes).ensureAlpha().raw().toBuffer({resolveWithObject:true});if(raw.info.channels!==4)throw Error('Fallback decoder did not return RGBA');im=createCanvas(raw.info.width,raw.info.height);im.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(raw.data),raw.info.width,raw.info.height),0,0);decoder='Existing sharp raw RGBA decoder; no source-file rewrite';}
    asset={path:p,sha256:hash(bytes),bytes:bytes.length,width:im.width,height:im.height,decoder,decodeFallbackReason,pixelText:e.pixel_text||'not_independently_audited'};
    if(e.asset_sha256&&e.asset_sha256!==asset.sha256)throw Error('Asset changed since layout freeze: '+e.id);
    const src=e.sourceRect?{x:e.sourceRect.x,y:e.sourceRect.y,width:e.sourceRect.width??e.sourceRect.w,height:e.sourceRect.height??e.sourceRect.h}:{x:0,y:0,width:im.width,height:im.height};
    if(!['x','y','width','height'].every(k=>Number.isInteger(src[k]))||src.x<0||src.y<0||src.width<1||src.height<1||src.x+src.width>im.width||src.y+src.height>im.height)throw Error('Invalid sourceRect: '+e.id);asset.sourceRect=src;
    const fit=e.fit||'contain';if(!['contain','cover','stretch'].includes(fit))throw Error('Invalid fit');
    let dw=w,dh=h,dx=0,dy=0;if(fit!=='stretch'){const scale=fit==='cover'?Math.max(w/src.width,h/src.height):Math.min(w/src.width,h/src.height);dw=src.width*scale;dh=src.height*scale;dx=(w-dw)/2;dy=(h-dh)/2;}
    c.drawImage(im,src.x,src.y,src.width,src.height,dx,dy,dw,dh);
  }else if(e.type==='text'){
    const {family,weight,pad,leading:lh,lines,x,y,inkBounds,maxWidth:max}=measureTextInk(c,e);
    c.fillStyle=e.fill||'#000000';
    if(inkBounds.some(b=>b.left<0||b.top<0||b.right>w||b.bottom>h))throw Error('Actual glyph ink exceeds component bounds: '+e.id+' '+JSON.stringify(inkBounds));
    if(max>w-pad*2+0.1)warnings.push('text_width_overflow');if(lines.length*lh>h-pad*2+0.1)warnings.push('text_height_overflow');
    for(let i=0;i<lines.length;i++)c.fillText(lines[i],x,y+i*lh);
    textMetrics={fontFamily:family,fontWeight:weight,fontSize:e.fontSize,lineHeight:lh,lineCount:lines.length,maxMeasuredWidth:max,availableWidth:w-pad*2,inkBounds};
  }else{
    c.fillStyle=e.fill||'#000000';c.strokeStyle=e.stroke||e.fill||'#000000';c.lineWidth=e.strokeWidth||1;
    if(e.type==='rect'){rectPath(c,w,h,e.radius||0);if(e.fill)c.fill();if(e.stroke)c.stroke();}
    if(e.type==='ellipse'&&e.radialGradient)drawSoftShadow(c,e,w,h);else if(e.type==='ellipse'){c.beginPath();c.ellipse(w/2,h/2,w/2,h/2,0,0,Math.PI*2);if(e.fill)c.fill();if(e.stroke)c.stroke();}
    if(e.type==='line'){c.beginPath();c.moveTo(0,h/2);c.lineTo(w,h/2);c.stroke();}
  }
  const pixels=Buffer.from(c.getImageData(0,0,w,h).data),semantic=clone(e);delete semantic.x;delete semantic.y;if(asset){semantic.path=asset.path;semantic.asset_sha256=asset.sha256;}
  return {canvas,pixels,asset,textMetrics,warnings,record:{id:e.id,psdLayerId:parseInt(hash(e.id).slice(0,7),16),type:e.type,role:e.role||null,x:e.x,y:e.y,width:w,height:h,opacity:e.opacity??1,native_text_requested:e.type==='text'?e.text:null,origin:e.provenance||(e.type==='text'?'Authored native text content':e.type==='image'?'Explicit local image asset':e.radialGradient?'Authored raster soft contact-shadow component':'Authored geometric component raster preview'),render_parameters:e.radialGradient?{radialGradient:clone(e.radialGradient)}:null,content_sha256:hash(pixels),position_sha256:hash(canonical({x:e.x,y:e.y,width:w,height:h,opacity:e.opacity??1})),semantic_sha256:hash(canonical(semantic)),asset,textMetrics,warnings}};
}

// V3: real PSD groups, contiguous category runs retain exact interleaved z order.
const categoryOf=e=>e.type==='text'?'文本':e.role==='background'?'背景':e.type==='image'?'图片':'模块';
function groupID(key){return 0x40000000+parseInt(hash('v3-group|'+key).slice(0,7),16);}
function elementLabel(e){
  if(e.label)return e.label;
  if(e.type==='text')return e.text.replace(/[\r\n]+/g,'／').slice(0,32);
  if(e.role==='contact-shadow')return '独立接触阴影';
  if(e.type==='image')return e.role==='background'?'场景背景':e.sourceRect?'局部细节图片':'产品图片';
  if(e.role==='background')return '色块与底板';
  return {rect:'装饰底板',ellipse:'椭圆装饰',line:'分隔线'}[e.type]||'独立图形';
}
function groupLeaves(layout,leaves){
  const roots=[],used=new Set(leaves.map(e=>e.id));
  let root=null,lastScreen=null,lastCategory=null,current=null,counts={};
  for(let i=0;i<layout.elements.length;i++){
    const e=layout.elements[i],match=e.id.match(/-S(\d{2})-/),screen=match?'S'+match[1]:layout.id.split('-').slice(-1)[0];
    if(screen!==lastScreen){
      const id=groupID(layout.id+'|screen|'+screen);if(used.has(id))throw Error('Group ID collision');used.add(id);
      root={name:(match?'分屏_':'版面_')+screen,id,opened:true,blendMode:'pass through',opacity:1,children:[]};roots.push(root);
      lastScreen=screen;lastCategory=null;counts={};
    }
    const category=categoryOf(e);
    if(category!==lastCategory){
      counts[category]=(counts[category]||0)+1;
      const serial=String(counts[category]).padStart(2,'0');
      const id=groupID(layout.id+'|'+screen+'|'+category+'|'+serial);if(used.has(id))throw Error('Group ID collision');used.add(id);
      current={name:category+'_'+serial+(category==='模块'?'｜阴影与图形':''),id,opened:true,blendMode:'pass through',opacity:1,children:[]};root.children.push(current);lastCategory=category;
    }
    current.children.push(leaves[i]);
  }
  return roots;
}
function flattenLeaves(nodes){const out=[];for(const n of nodes||[]){if(n.children)out.push(...flattenLeaves(n.children));else out.push(n);}return out;}
function summarizeGroups(nodes){return (nodes||[]).map(n=>n.children?{name:n.name,id:n.id,blendMode:n.blendMode,opacity:n.opacity,opened:n.opened,children:summarizeGroups(n.children)}:{name:n.name,id:n.id});}
function countGroups(nodes){return (nodes||[]).reduce((n,e)=>n+(e.children?1+countGroups(e.children):0),0);}

function textDescriptor(e){
  const ctx=createCanvas(1,1).getContext('2d');ctx.font=(e.fontWeight||400)+' '+e.fontSize+'px "'+(e.fontFamily||'Microsoft YaHei')+'"';const first=e.text.split(/\r?\n/)[0];ctx.textBaseline='top';const tm=ctx.measureText(first);ctx.textBaseline='alphabetic';const am=ctx.measureText(first);const baselineOffset=(e.textInsetY||0)+am.actualBoundingBoxAscent-tm.actualBoundingBoxAscent;
  const hex=(e.fill||'#000000').replace('#','');const rgb=hex.length===6?[0,2,4].map(i=>parseInt(hex.slice(i,i+2),16)):[0,0,0];
  return {text:e.text,transform:[1,0,0,1,e.x+(e.padding||0),e.y+(e.padding||0)+baselineOffset],style:{font:{name:e.psdFontName||e.fontFamily||'MicrosoftYaHei'},fontSize:e.fontSize,leading:e.fontSize*(e.lineHeight||1.25),autoLeading:false,fillColor:{r:rgb[0],g:rgb[1],b:rgb[2]},fauxBold:e.fontWeight>=600},paragraphStyle:{justification:e.align||'left'},shapeType:'box',boxBounds:[0,0,e.width,e.height]};
}
async function render(layout,base,out,options={}){
  validate(layout);const userRegistry=loadUserRegistry(options.userRegistry||layout.user_id_registry);const identities=layout.elements.map(e=>userIdentityFor(layout,e,userRegistry));if(new Set(identities.map(x=>x.short_id)).size!==identities.length)throw Error('Duplicate user IDs in piece');if(options.exclusiveOutput){
    fs.mkdirSync(path.dirname(out),{recursive:true});
    fs.mkdirSync(out);
  }else{
    if(fs.existsSync(out)&&fs.readdirSync(out).length)throw Error('Refusing to overwrite a nonempty version directory');
    fs.mkdirSync(out,{recursive:true});
  }
  const canvas=createCanvas(layout.width,layout.height),ctx=canvas.getContext('2d'),parts=[];
  fs.mkdirSync(path.join(out,'layers'),{recursive:true});
  for(const e of layout.elements){const p=await component(e,base);parts.push(p);ctx.save();ctx.globalAlpha=e.opacity??1;ctx.drawImage(p.canvas,e.x,e.y);ctx.restore();fs.writeFileSync(path.join(out,'layers',e.id+'.png'),p.canvas.toBuffer('image/png'));}
  const integrity=reviewExportIntegrity(layout,parts);writeJSON(path.join(out,'export-integrity.json'),integrity);if(!integrity.pass)throw Error('Export integrity gate failed: '+canonical(integrity.errors));
  const png=canvas.toBuffer('image/png');fs.writeFileSync(path.join(out,'original.png'),png);
  const normalized=clone(layout);if(userRegistry)normalized.user_id_registry=userRegistry.source==='explicit object'?clone(userRegistry.registry):path.resolve(userRegistry.source);normalized.elements.forEach((e,i)=>{if(parts[i].asset){e.path=parts[i].asset.path;e.asset_sha256=parts[i].asset.sha256;}});writeJSON(path.join(out,'layout.json'),normalized);
  const {canvas:review,index:reviewIndex}=drawNumberedReview({artwork:canvas,layout,parts,identities,createCanvas});
  fs.writeFileSync(path.join(out,'review-numbered.png'),review.toBuffer('image/png'));
  writeJSON(path.join(out,'review-numbered.json'),reviewIndex);
  for(let i=0;i<parts.length;i++){parts[i].record.userDisplayId=identities[i].short_id;parts[i].record.qualifiedReference=identities[i].qualified_id;parts[i].record.internalId=identities[i].internal_id;}
  const compositeRGBA=Buffer.from(ctx.getImageData(0,0,layout.width,layout.height).data);
  const manifest={exportIntegrity:integrity,schema:'dt-render-manifest-v1',layoutId:layout.id,width:layout.width,height:layout.height,elementCount:parts.length,elementIds:layout.elements.map(e=>e.id),layout_sha256:hash(canonical(normalized)),original_png_sha256:hash(png),composite_rgba_sha256:hash(compositeRGBA),layers:parts.map(p=>p.record),provenance:'Explicit component layout; no automatic flat-image decomposition',nativeText:{status:'NOT_EXPORTED',count:0},photoshopApplicationCompatibility:'NOT_TESTED',runtime:{node:process.version,canvas:canvasDependency.version}};
  if(options.revision) manifest.managedRevision={base:options.revision.current,version:options.revision.next};
  const {lib}=psdLibrary;lib.initializeCanvas((w,h)=>createCanvas(w,h));
  manifest.userIdentity={status:userRegistry?'PERSISTENT_REGISTRY_APPLIED':'LEGACY_NAMES',piece:layout.id.replace(/^V2-/,''),registrySHA256:userRegistry?.sha256||null,entries:identities};
  const children=layout.elements.map((e,i)=>({name:identities[i].short_id+' | '+categoryOf(e)+' | '+elementLabel(e),id:parseInt(hash(e.id).slice(0,7),16),left:e.x,top:e.y,right:e.x+Math.ceil(e.width),bottom:e.y+Math.ceil(e.height),opacity:e.opacity??1,blendMode:'normal',canvas:parts[i].canvas,...(e.type==='text'?{text:textDescriptor(e)}:{})}));
  const groupedChildren=groupLeaves(layout,children);const psd={width:layout.width,height:layout.height,canvas,children:groupedChildren,imageResources:{xmpMetadata:identityXMP(layout,identities)}};const bytes=lib.writePsdBuffer(psd,{generateThumbnail:false});fs.writeFileSync(path.join(out,'layered.psd'),bytes);
  const decoded=lib.readPsd(bytes,{skipThumbnail:true});manifest.userIdentity.xmpReadbackExact=decoded.imageResources?.xmpMetadata===identityXMP(layout,identities);if(!manifest.userIdentity.xmpReadbackExact)throw Error('PSD internal identity XMP mismatch');const flattened=flattenLeaves(decoded.children);manifest.psdGrouping={strategy:'Per-screen containers; contiguous category runs preserve z order',categories:['文本','图片','模块','背景'],leafOrder:flattened.map(e=>e.id),groupCount:countGroups(decoded.children),tree:summarizeGroups(decoded.children),nativeSectionDividers:'WRITTEN_AND_READ_BACK',groupIDNamespace:'0x40000000..0x4fffffff; original leaf IDs unchanged'};
  const readback=decoded.canvas;if(!readback)throw Error('PSD readback omitted composite');const pixels=Buffer.from(readback.getContext('2d').getImageData(0,0,layout.width,layout.height).data);fs.writeFileSync(path.join(out,'psd-readback.png'),readback.toBuffer('image/png'));
  const native=flattened.filter(e=>e.text);manifest.nativeText={status:'TYPE_DESCRIPTORS_READ_BACK',count:native.length,texts:native.map(e=>({name:e.name,text:e.text.text,style:e.text.style,transform:e.text.transform}))};
  const recomposite=createCanvas(layout.width,layout.height),rc=recomposite.getContext('2d');for(const e of flattened){if(!e.canvas)throw Error('Missing PSD decoded layer canvas');rc.save();rc.globalAlpha=e.opacity??1;rc.drawImage(e.canvas,e.left||0,e.top||0);rc.restore();}
  const recompositePixels=Buffer.from(rc.getImageData(0,0,layout.width,layout.height).data);fs.writeFileSync(path.join(out,'psd-layers-recomposite.png'),recomposite.toBuffer('image/png'));
  const diffs=(a,b)=>{if(a.length!==b.length)return {exact:false,reason:'length_mismatch',expectedLength:a.length,actualLength:b.length};let n=0,m=0;for(let i=0;i<a.length;i++){const d=Math.abs(a[i]-b[i]);if(d)n++;if(d>m)m=d;}return {differentChannels:n,maxAbsoluteDifference:m,exact:n===0};};
  manifest.psd={status:'EXPORTED_AND_READ_BACK',libraryPath:psdLibrary.path,libraryVersion:psdLibrary.version,sha256:hash(bytes),bytes:bytes.length,width:decoded.width,height:decoded.height,layerCount:flattened.length,groupCount:countGroups(decoded.children),groupTree:summarizeGroups(decoded.children),layerNames:flattened.map(e=>e.name),embeddedCompositeComparison:diffs(compositeRGBA,pixels),decodedLayerRecompositeComparison:diffs(compositeRGBA,recompositePixels),nativeTextDescriptorCount:native.length,photoshopApplicationCompatibility:'NOT_TESTED'};
  const structuralErrors=[];if(decoded.width!==layout.width||decoded.height!==layout.height)structuralErrors.push('canvas_dimensions');if(flattened.length!==layout.elements.length)structuralErrors.push('layer_count');
  for(let i=0;i<layout.elements.length;i++){const e=layout.elements[i],actual=flattened[i];if(!actual){structuralErrors.push(e.id+':missing');continue;}if(!actual.name.startsWith(identities[i].short_id+' | '))structuralErrors.push(e.id+':identity');if(actual.left!==e.x||actual.top!==e.y)structuralErrors.push(e.id+':position');if(e.type==='text'){if(!actual.text||actual.text.text.replace(/\r\n|\r/g,'\n')!==e.text.replace(/\r\n|\r/g,'\n'))structuralErrors.push(e.id+':native_text');const s=actual.text&&actual.text.style;if(!s||s.fontSize!==e.fontSize||!s.font||s.font.name!==(e.psdFontName||e.fontFamily||'MicrosoftYaHei'))structuralErrors.push(e.id+':native_font');}if(e.type!=='text'&&actual.text)structuralErrors.push(e.id+':unexpected_text');}
  manifest.psd.structuralErrors=structuralErrors;manifest.psd.nativeTextExpected=layout.elements.filter(e=>e.type==='text').length;
  if(structuralErrors.length)manifest.psd.status='STRUCTURE_MISMATCH';else if(!manifest.psd.embeddedCompositeComparison.exact||!manifest.psd.decodedLayerRecompositeComparison.exact)manifest.psd.status='PIXEL_MISMATCH';
  writeJSON(path.join(out,'manifest.json'),manifest);return manifest;
}
function frozenDiff(before,after,selected){
  const map=new Map(after.layers.map(e=>[e.id,e]));const changed=[];for(const e of before.layers){if(e.id===selected)continue;const n=map.get(e.id);if(!n||['content_sha256','position_sha256','semantic_sha256'].some(k=>e[k]!==n[k]))changed.push(e.id);}
  return {selectedId:selected,idsStable:canonical(before.elementIds)===canonical(after.elementIds),unselectedCompared:before.layers.filter(e=>e.id!==selected).length,unselectedChanged:changed,pass:!changed.length&&canonical(before.elementIds)===canonical(after.elementIds)};
}
async function applyEdit(layout,base,patch,out,newLayoutPath,baseline){
  return withCurrentRevision({base,out,newLayoutPath,layout,baseline}, revision =>
    applyComponentPatch(layout,revision.baseDir,patch,out,newLayoutPath,baseline,revision));
}

async function applyFontPreview(layout,base,patch,out,baseline){
  // The localhost font prototype previews the explicitly identified frozen H01.
  // This does not issue a managed revision and is not a general branch command.
  const target=path.resolve(out);
  const input=fs.realpathSync(base);
  if(layout.id!=='V2-H01'||input!==fs.realpathSync(H01_FONT_PREVIEW_BASELINE)||
     target===input||target.startsWith(input+path.sep)||path.basename(target)!=='render'||
     path.basename(path.dirname(path.dirname(target)))!=='application-tests'||
     !/^[0-9a-f]{32}$/.test(path.basename(path.dirname(target)))||
     !patch.changes||Object.keys(patch.changes).sort().join(',')!=='fontFamily,psdFontName'||
     !layout.elements.some(e=>e.id===patch.selectedId&&e.type==='text')){
    throw Error('INVALID_FROZEN_H01_FONT_PREVIEW');
  }
  return applyComponentPatch(layout,base,patch,out,undefined,baseline,undefined,true);
}

async function applyComponentPatch(layout,base,patch,out,newLayoutPath,baseline,revision,reboxFontPreviewTarget=false){
  if(!patch.selectedId||!patch.changes||typeof patch.changes!=='object')throw Error('Invalid edit patch');
  const frozenRegistry=loadUserRegistry(layout.user_id_registry);assertRegistryFrozen(baseline,frozenRegistry);
  const selectedInternalId=resolveReference(layout,patch.selectedId,frozenRegistry);
  const next=clone(layout),element=next.elements.find(e=>e.id===selectedInternalId);if(!element)throw Error('Selected element not found');
  const allowed=new Set(['x','y','width','height','text','path','sourceRect','radialGradient','provenance','pixel_text','fontSize','fontFamily','fontWeight','lineHeight','fill','stroke','strokeWidth','radius','fit','padding','align','opacity','psdFontName','textInsetY']);for(const k of Object.keys(patch.changes))if(!allowed.has(k))throw Error('Edit field is not permitted: '+k);
  Object.assign(element,patch.changes);if(patch.changes.path!==undefined&&patch.changes.path!==layout.elements.find(e=>e.id===selectedInternalId).path)delete element.asset_sha256;
  if(reboxFontPreviewTarget)Object.assign(element,reboxTextTop(element));
  validate(next);
  const oldParts=[];for(const e of layout.elements)oldParts.push((await component(e,base)).record);
  const before={layers:oldParts,elementIds:layout.elements.map(e=>e.id)};
  if(!baseline)throw Error('A persisted baseline manifest is required for edits');const baselineCheck=frozenDiff(baseline,before,null);if(!baselineCheck.pass)throw Error('Input no longer matches frozen baseline: '+canonical(baselineCheck));
  const after=await render(next,base,out,{exclusiveOutput:true,revision});
  const check={...frozenDiff(baseline,after,selectedInternalId),requestedReference:patch.selectedId,baseline:'persisted_manifest',baselineLayoutSHA256:baseline.layout_sha256};
  if(revision) Object.assign(check,{baseVersion:revision.current,resultVersion:revision.next});
  writeJSON(path.join(out,'edit-check.json'),check);
  if(!check.pass)throw Error('Unselected layer changed');
  if(revision&&after.psd.status!=='EXPORTED_AND_READ_BACK')throw Error('Managed revision PSD verification failed');
  const persistedLayout=path.join(out,'layout.json');
  const actualLayout=readJSON(persistedLayout);
  if(newLayoutPath&&path.relative(path.resolve(persistedLayout),path.resolve(newLayoutPath))!==''){
    fs.writeFileSync(newLayoutPath,JSON.stringify(actualLayout,null,2)+'\n',{flag:'wx'});
  }
  if(revision){
    writeJSON(path.join(out,'revision-complete.json'), {
      version:revision.next, manifest_sha256:hash(fs.readFileSync(path.join(out,'manifest.json'))),
      edit_check_sha256:hash(fs.readFileSync(path.join(out,'edit-check.json')))
    });
  }
  return {layout:actualLayout,manifest:after,check};
}
async function fixture(out){
  fs.mkdirSync(out,{recursive:true});
  const l={schema:'dt-layout-v1',id:'FIXTURE',width:640,height:480,elements:[{id:'F01-E001',type:'rect',role:'background',x:0,y:0,width:640,height:480,fill:'#f4eadc'},{id:'F01-E002',type:'ellipse',role:'product-mock',x:180,y:150,width:200,height:220,fill:'#bb8d63'},{id:'F01-E003',type:'text',role:'text',x:45,y:35,width:540,height:80,text:'Generic editable text',fontFamily:'Arial',fontSize:32,fill:'#362b25'},{id:'F01-E004',type:'rect',role:'icon',x:530,y:380,width:50,height:50,fill:'#506354',radius:10}]};
  const first=await render(l,out,path.join(out,'v0'));writeJSON(path.join(out,'original-layout.json'),l);
  const patches=[{selectedId:'F01-E002',changes:{x:210,y:165}},{selectedId:'F01-E002',changes:{width:220,height:235}},{selectedId:'F01-E003',changes:{text:'Edited native text'}}];
  const runs=[];let current=readJSON(path.join(out,'v0','layout.json')),baseline=first;
  for(let i=0;i<patches.length;i++){
    const r=await applyEdit(current,path.join(out,'v'+i),patches[i],path.join(out,'v'+(i+1)),path.join(out,'layout-v'+(i+1)+'.json'),baseline);
    current=r.layout;baseline=r.manifest;runs.push(r.check);
  }
  const rollback=await render(l,out,path.join(out,'rollback'));const rollbackPass=rollback.original_png_sha256===first.original_png_sha256&&rollback.layout_sha256===first.layout_sha256&&canonical(rollback.layers)===canonical(first.layers);
  const result={scope:'Synthetic geometry and generic English only, not product business acceptance',threeConsecutiveEdits:runs,rollback:{pass:rollbackPass,original_png_sha256:first.original_png_sha256,restored_png_sha256:rollback.original_png_sha256},nativePSD:first.psd,pass:runs.every(x=>x.pass)&&rollbackPass};writeJSON(path.join(out,'fixture-result.json'),result);return result;
}
async function main(){const [cmd,a,b,c,d]=process.argv.slice(2);let result;if(cmd==='render')result=await render(readJSON(a),path.dirname(path.resolve(a)),path.resolve(b));else if(cmd==='edit')result=await applyEdit(readJSON(a),path.dirname(path.resolve(a)),readJSON(b),path.resolve(d),path.resolve(c),readJSON(path.join(path.dirname(path.resolve(a)),'manifest.json')));else if(cmd==='fixture')result=await fixture(path.resolve(a));else throw Error('Usage: engine.cjs render layout.json out | edit rendered-version/layout.json patch.json new-layout.json out | fixture out');console.log(JSON.stringify(result,null,2));}
if(require.main===module)main().catch(e=>{console.error(e.stack);process.exitCode=1;});
module.exports={validate,component,render,applyEdit,applyFontPreview,frozenDiff,fixture,categoryOf,flattenLeaves,summarizeGroups,groupLeaves,elementLabel,reboxTextTop,H01_FONT_PREVIEW_BASELINE};
