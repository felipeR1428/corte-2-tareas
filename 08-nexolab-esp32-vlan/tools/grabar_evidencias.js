/* Prueba del navegador y grabación de las acciones reales sobre los servicios.
   Variables: NEXO_CHROMIUM (opcional), PLAYWRIGHT_BROWSERS_PATH (opcional).
   Instalar Playwright y su navegador, o indicar un ejecutable compatible.
*/
const fs=require('fs'),path=require('path'),assert=require('assert'),cp=require('child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,'evidencias');
const temp=path.join(root,'resultados','videos-temporales');fs.mkdirSync(temp,{recursive:true});
const result={origen:'Prueba ejecutada en navegador y procesos Python, con Mosquitto y ESP32 emuladas',
 docker_ejecutado:false,esp32_fisicas:false,vlan_8021q_probada:false,version:'3.0',fecha_utc:new Date().toISOString(),
 pruebas:[],videos:[],errores_js:[]};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let browser;
async function waitJson(page,route,predicate,timeout=18000){const end=Date.now()+timeout;let d;while(Date.now()<end){try{d=await page.evaluate(async route=>{const r=await fetch(route);if(!r.ok)throw Error(r.status);return r.json();},route);if(predicate(d))return d;}catch{}await sleep(250);}throw Error('Condición no cumplida en '+route+': '+JSON.stringify(d).slice(0,300));}
function save(name,data){fs.writeFileSync(path.join(out,'datos',name+'.json'),JSON.stringify(data,null,2));}
function pass(name,details){result.pruebas.push({nombre:name,resultado:'aprobada',detalles:details});console.log('APROBADA '+name);}
async function openVideo(name){const ctx=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1,recordVideo:{dir:temp,size:{width:1440,height:1000}}});const page=await ctx.newPage();page.on('pageerror',e=>result.errores_js.push(e.message));await page.goto('http://127.0.0.1:18180',{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>document.querySelector('#connection').textContent==='Monitor conectado');await sleep(2200);return{ctx,page,name};}
async function caption(page,text){await page.evaluate(text=>{let x=document.querySelector('#evidence-caption');if(!x){x=document.createElement('div');x.id='evidence-caption';Object.assign(x.style,{position:'fixed',bottom:'16px',left:'252px',right:'22px',padding:'14px 20px',background:'#102c30f0',color:'white',font:'14px Segoe UI,sans-serif',borderRadius:'8px',zIndex:'20',boxShadow:'0 5px 20px #0003'});document.body.append(x);}x.textContent='PRUEBA DE SOFTWARE  /  '+text;},text);}
async function snap(page,name){await page.evaluate(()=>{const x=document.querySelector('#evidence-caption');if(x)x.style.display='none';});await page.screenshot({path:path.join(out,'capturas',name+'.png'),fullPage:true});await page.evaluate(()=>{const x=document.querySelector('#evidence-caption');if(x)x.style.display='';});}
async function closeVideo(v){await v.ctx.close();const file=await v.page.video().path();const target=path.join(out,'videos',v.name+'.mp4');cp.execFileSync('ffmpeg',['-y','-loglevel','error','-i',file,'-c:v','libx264','-preset','fast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',target]);const meta=JSON.parse(cp.execFileSync('ffprobe',['-v','quiet','-show_format','-show_streams','-of','json',target],{encoding:'utf8'}));result.videos.push({archivo:path.relative(root,target),duracion_s:Number(meta.format.duration),ancho:meta.streams[0].width,alto:meta.streams[0].height});}
async function main(){
 browser=await chromium.launch({...(process.env.NEXO_CHROMIUM?{executablePath:process.env.NEXO_CHROMIUM}:{}),headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--no-zygote']});
 let v=await openVideo('01-panel-y-carrera'),p=v.page;
 const initial=await waitJson(p,'/api/resumen.json',d=>d.mqtt_conectado&&d.otras_metricas?.esclava?.edad_s<5);
 save('estado_inicial',initial);assert.equal(initial.nexo.modo,'software');
 await p.waitForFunction(()=>Array.from(document.querySelectorAll('[data-page="inicio"] img[data-feed]')).every(i=>i.complete&&i.naturalWidth>0));
 await caption(p,'Vista general: servicios reales, renders PyBullet y confirmación MQTT de la esclava emulada.');await sleep(4500);await snap(p,'01-vista-general');
 await p.click('[data-view="carreras"]');await caption(p,'Tres jugadores reciben UDP de los mandos emulados; otros tres vehículos conducen de forma autónoma.');await sleep(6000);await snap(p,'02-carrera');
 const before=await waitJson(p,'/api/zona/pista/estado',d=>d.carros?.length===6);
 assert(before.pista?.nombre==='Circuito Nexo GP'&&before.pista.perimetro>70);
 save('circuito_gp_inicio',before);pass('Circuito técnico cargado',{nombre:before.pista.nombre,perimetro_m:before.pista.perimetro,ancho_m:before.pista.ancho});
 await p.locator('.detail-card').scrollIntoViewIfNeeded();await caption(p,'Carrocería Nexo GT: alerón, cabina, franjas y luces. Cámara 3D de seguimiento en vivo.');
 await p.waitForFunction(()=>{const i=document.querySelector('img[data-resource="detalle"]');return i.complete&&i.naturalWidth>0;});
 await sleep(8000);await p.locator('.detail-card').screenshot({path:path.join(out,'capturas','02c-carro-competicion.png')});
 await p.evaluate(()=>window.scrollTo({top:0,behavior:'smooth'}));await caption(p,'Recorrido por la horquilla y las eses. Las posiciones proceden de la simulación física.');await sleep(12000);
 const moving=await waitJson(p,'/api/zona/pista/estado',d=>d.carros?.every(c=>c.distancia-before.carros.find(b=>b.id===c.id).distancia>5));
 save('circuito_gp_movimiento',moving);pass('Los seis carros avanzan por el circuito',{avances_m:moving.carros.map(c=>({id:c.id,metros:Number((c.distancia-before.carros.find(b=>b.id===c.id).distancia).toFixed(2))}))});
 await snap(p,'02d-circuito-en-marcha');

 await p.locator('[data-gamer="2"][data-mode="pausa"]').click();await caption(p,'Se pausa el mando 2: el servidor aplica el freno de seguridad al perder la señal.');
 const stopped=await waitJson(p,'/api/zona/pista/estado',d=>d.carros?.find(c=>c.id==='player-2')?.failsafe===true);
 await sleep(2500);await snap(p,'02b-freno-seguridad');save('carrera_failsafe',stopped);pass('Freno por falta de control UDP',{jugador:'player-2',failsafe:true});
 await p.locator('[data-gamer="2"][data-mode="auto"]').click();const resumed=await waitJson(p,'/api/zona/pista/estado',d=>d.carros?.find(c=>c.id==='player-2')?.failsafe===false);save('carrera_recuperada',resumed);
 
 pass('Reanudación del mando y seis vehículos',{vehiculos:resumed.carros.length,control_restaurado:true});await caption(p,'El mando vuelve a transmitir y el jugador 2 recupera el movimiento.');await sleep(3500);await closeVideo(v);
 v=await openVideo('02-robots-control-manual');p=v.page;await p.click('[data-view="robotica"]');
 const poses={spot:[10,-8,5],pepper:[20,55,-25],nao:[30,60,20]};let i=3;
 for(const id of ['spot','pepper','nao']){
   await p.click('[data-robot="'+id+'"]');await caption(p,'Robot '+id.toUpperCase()+': modificar las entradas del mando emulado y observar la respuesta física.');await sleep(2500);
   await p.evaluate(values=>values.forEach((value,index)=>{const el=document.querySelector('#joint-'+index);el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));}),poses[id]);
   await p.click('#apply-robot');
   const received=await waitJson(p,'/api/zona/'+id+'/estado',d=>d.comando&&d.comando.every((x,k)=>Math.abs(x-poses[id][k])<.11));
   await sleep(3000);const physical=await waitJson(p,'/api/zona/'+id+'/estado',d=>d.estado==='siguiendo'&&d.error_deg<5,22000);
   assert(physical.recibidos>0);save('robot_'+id+'_manual',physical);pass('Control manual real por UDP: '+id,{comando:physical.comando,medido:physical.medido,error_deg:physical.error_deg});
   await snap(p,String(i++).padStart(2,'0')+'-'+id);await sleep(1500);
   if(id==='nao'){
     await p.click('#pause-robot');await caption(p,'Al suspender los JOINTS, el robot entra en reposo por falta de señal.');
     const idle=await waitJson(p,'/api/zona/nao/estado',d=>d.estado==='reposo');save('robot_nao_failsafe',idle);await sleep(2000);await snap(p,'05b-nao-reposo');pass('Reposo del robot por pérdida de mando',{robot:'nao',estado:idle.estado});
   }
   await p.click('#auto-robot');await sleep(1500);
 }
 await closeVideo(v);
 v=await openVideo('03-monitoreo-falla-recuperacion');p=v.page;await p.click('[data-view="red"]');await caption(p,'RTT y jitter medidos mediante eco UDP local. Esta ejecución no prueba el aislamiento de las VLAN.');await sleep(4000);await snap(p,'06-red-y-metricas');
 const start=Date.now();console.log('NEXO_EVENT '+JSON.stringify({action:'stop',service:'sim-pepper'}));await caption(p,'Se detiene el proceso de Pepper. El monitor detecta la caída y publica el cambio por MQTT.');
 const fallen=await waitJson(p,'/api/resumen.json',d=>d.servicios['sim-pepper'].estado==='CAIDO'&&d.otras_metricas?.esclava?.datos?.leds?.['sim-pepper']==='CAIDO');
 save('estado_caida',fallen);const detection=(Date.now()-start)/1000;
 assert(['track-server','player-1','player-2','player-3','sim-spot','sim-nao'].every(n=>fallen.servicios[n].estado!=='CAIDO'));
 pass('Caída de Pepper y señal MQTT',{deteccion_observada_s:detection,led:'CAIDO',otros_servicios:'siguen enviando latidos'});
 await sleep(1500);await snap(p,'07-pepper-caido');await p.click('[data-view="inicio"]');await sleep(1700);await snap(p,'07b-led-apagado');
 console.log('NEXO_EVENT '+JSON.stringify({action:'start',service:'sim-pepper'}));await caption(p,'Pepper se reinicia. La recepción de latidos restablece su estado y el indicador de la esclava.');
 const recovered=await waitJson(p,'/api/resumen.json',d=>d.servicios['sim-pepper'].estado==='OK'&&d.otras_metricas?.esclava?.datos?.leds?.['sim-pepper']==='OK',25000);
 save('estado_recuperado',recovered);pass('Recuperación de Pepper',{servicio:'OK',led:'OK',eventos:recovered.eventos.filter(x=>x.servicio==='sim-pepper')});
 await sleep(2500);await p.click('[data-view="red"]');await sleep(1500);await snap(p,'08-recuperacion');
 const download=await Promise.all([p.waitForEvent('download'),p.click('a[href="/api/metricas.csv"]')]);await download[0].saveAs(path.join(out,'datos','metricas_exportadas.csv'));assert(fs.statSync(path.join(out,'datos','metricas_exportadas.csv')).size>100);pass('Exportación CSV desde el navegador',{archivo:'metricas_exportadas.csv'});
 await closeVideo(v);
 v=await openVideo('04-conexiones-esp32');p=v.page;await p.click('[data-view="montaje"]');
 for(const [id,order] of [['gamer','09'],['robot','10'],['esclava','11']]){await p.click('[data-mount="'+id+'"]');await caption(p,'Montaje '+id+': ilustración generada con IA. Para cablear utiliza la tabla GPIO, no la posición aparente del cable.');await p.waitForFunction(()=>document.querySelector('#mount-image').complete&&document.querySelector('#mount-image').naturalWidth>0);await sleep(4500);await snap(p,order+'-montaje-'+id);}
 await closeVideo(v);
 const ctx=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1});p=await ctx.newPage();await p.goto('http://127.0.0.1:18180');await sleep(3000);assert(await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));await p.screenshot({path:path.join(out,'capturas','12-vista-movil.png'),fullPage:true});pass('Interfaz móvil sin desbordamiento horizontal',{ancho:390});await ctx.close();
 assert.deepEqual(result.errores_js,[]);pass('Navegador sin errores JavaScript',{errores:0});
 const last=await fetch('http://127.0.0.1:18180/api/resumen.json').then(r=>r.json());save('estado_final',last);
 const laps=await fetch('http://127.0.0.1:18180/api/zona/pista/estado').then(r=>r.json());save('circuito_gp_final',laps);assert(laps.carros.every(c=>c.vuelta>=1));pass('Vueltas completas en el nuevo circuito',{vueltas:laps.carros.map(c=>({id:c.id,vueltas:c.vuelta,reapariciones:c.reapariciones}))});
 save('pruebas_integracion',result);await browser.close();console.log('EVIDENCIAS_COMPLETAS');
}
main().catch(async e=>{result.error=e.stack;save('pruebas_integracion',result);console.error(e);if(browser)await browser.close();process.exit(1)});
