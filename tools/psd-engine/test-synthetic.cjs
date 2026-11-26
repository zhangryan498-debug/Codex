'use strict';

const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {render}=require('./engine.cjs');

async function main() {
  const tempRoot=fs.realpathSync(os.tmpdir());
  const scratch=fs.realpathSync(fs.mkdtempSync(path.join(tempRoot,'dt-psd-synthetic-')));
  if(path.dirname(scratch)!==tempRoot || !path.basename(scratch).startsWith('dt-psd-synthetic-')) {
    throw new Error('Unexpected scratch directory for PSD smoke test.');
  }
  try {
    const output=path.join(scratch,'export');
    const layout={
      schema:'dt-layout-v1',
      id:'SYNTHETIC-PSD-SMOKE',
      width:160,
      height:96,
      elements:[
        {id:'QA-E001',type:'rect',role:'background',x:0,y:0,width:160,height:96,fill:'#f7f4ee'},
        {id:'QA-E002',type:'text',role:'text',x:12,y:14,width:136,height:40,text:'Editable text',
          fontFamily:'Arial',fontSize:18,textInsetY:4,fill:'#202020'},
      ],
    };
    const manifest=await render(layout,scratch,output);
    assert.equal(manifest.psd.status,'EXPORTED_AND_READ_BACK');
    assert.equal(manifest.psd.libraryVersion,'34.2.1');
    assert.equal(manifest.psd.layerCount,2);
    assert.equal(manifest.psd.nativeTextDescriptorCount,1);
    assert.equal(manifest.nativeText.texts[0].text,'Editable text');
    assert(manifest.userIdentity.xmpReadbackExact);
    assert(manifest.psd.embeddedCompositeComparison.exact);
    assert(manifest.psd.decodedLayerRecompositeComparison.exact);
    assert(fs.statSync(path.join(output,'original.png')).size>0);
    assert.equal(fs.readFileSync(path.join(output,'layered.psd')).subarray(0,4).toString(),'8BPS');
    console.log('PSD smoke passed: two layers, one editable text layer, exact composite and layer readback.');
  } finally {
    fs.rmSync(scratch,{recursive:true,force:true});
  }
}

main().catch(error=>{console.error(error.message);process.exitCode=1;});
