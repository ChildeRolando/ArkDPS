'use strict';
// Explicit test-only source assembly. The shared HP entry/leave owner remains
// unchanged; archived research reducers are inserted only for reference gates.
const fs=require('node:fs'),path=require('node:path');
const patches=require('./hp_research_patches.json');
function assembleResearchRuntime(runtime){
 runtime=runtime.replace(/\r\n?/g,'\n');
 for(const {marker,product,research,research_file}of patches){
  const target=marker+'\n'+product;
  if(runtime.split(target).length!==2)throw Error('HP research assembly point changed: '+marker);
  const inserted=research_file?fs.readFileSync(path.join(__dirname,research_file),'utf8'):research;
  runtime=runtime.replace(target,inserted);
 }
 return runtime;
}
module.exports={assembleResearchRuntime};
