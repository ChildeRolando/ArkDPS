'use strict';
const {world}=require('./native_pc_autochess_context_fixture.js');
const f=world({rolesPerPlayer:10,token:true,fixtureOptions:{enemyHpState:true}});
f.clock(30,4294967296n);const mark=f.mark();
f.context.auditMeta={string_class:f.level.add(0x18).readPointer().readPointer().toString(),classes:{BattleController:f.controllerClass.toString(),LevelData:f.level.readPointer().toString()}};
f.vm.runInContext('nativeCaptureMetadata=auditMeta',f.context);
const graph=f.vm.runInContext('nativeCaptureScopeGraph(nativeAutoCache,nativeAutoStage,clock,1)',f.context);
process.stdout.write(JSON.stringify({fixture:'actual world rolesPerPlayer10+token; nativeCaptureScopeGraph, not handwritten 64 fields',memberships:mark.native_autochess_context.memberships.length,graph}));
