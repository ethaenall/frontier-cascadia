import { chapters } from './chapters.js';
import { createTransitions } from './transitions.js';
const $ = (selector) => document.querySelector(selector);
const audience = !new URLSearchParams(location.search).has('presenter');
const store = {get(key, fallback) {try{return sessionStorage.getItem(key) ?? fallback}catch{return fallback}},set(key,value){try{sessionStorage.setItem(key,String(value))}catch{}}};
const params = new URLSearchParams(location.search);
const sessionID = params.get('session') || store.get('threshold-session',crypto.randomUUID());
store.set('threshold-session',sessionID);
let chapterIndex = Math.max(0,Math.min(chapters.length-1,Number(store.get('threshold-chapter','0'))||0));
let renderer = null, anims = [], revealID = 0, audioContext = null, soundOn = false, channel = null;
let frameMode = 'Loading graphics', quality = store.get('threshold-quality','auto');
const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
let motionOverride = store.get('threshold-motion','system');
let reducedMotion = motionOverride==='system'?motionQuery.matches:motionOverride==='reduce';
let consoleURL = '';try{consoleURL=validateConsole(store.get('threshold-console',''))}catch{store.set('threshold-console','')}
let timerStart = performance.now();
const activeAudio = new Set();
const transitions=createTransitions({isReduced:()=>reducedMotion,isAudience:()=>audience});
const currentChapter = () => chapters[chapterIndex];
const VISUALS = {'emancipator':0,'reveal':1,'a-door':2,'door-state':3,'sensor-comparison':10,'the-question':4,'the-signal':5,'built-software':6,'console-handoff':7,'fall-research':8,'start-here':9};
const visualIndex = () => VISUALS[currentChapter().id] ?? 0;
const savedChapterID=store.get('threshold-chapter-id','');
if(savedChapterID&&chapters.some(c=>c.id===savedChapterID))chapterIndex=chapters.findIndex(c=>c.id===savedChapterID);
function receiveCue(cue){if(currentChapter().id!=='the-question'||cue.type!=='illustrated-entry')return;const active=Boolean(cue.active);document.body.classList.toggle('illustrated-alert',active);$('#detection-state').textContent=active?'SIMULATED DETECTION':'SIMULATED WALK-THROUGH';}
try{channel=new BroadcastChannel(`threshold-talk-${sessionID}`)}catch{}
const send = (message) => channel?.postMessage(message);
function make(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node}
function letters(title){
  title.forEach((line,i)=>{const row=make('span','title-line');Array.from(line).forEach(char=>row.append(make('span','glyph',char===' '? '\u00a0':char)));$('#chapter-title').append(row)});
}
function fitTitle(){const title=$('#chapter-title');title.style.fontSize='';const available=$('#copy').clientWidth;const widest=Math.max(...[...title.children].map(line=>line.scrollWidth),1);if(widest>available)title.style.fontSize=`${parseFloat(getComputedStyle(title).fontSize)*available/widest*.985}px`;}
function clearAnimations(){transitions.cancel();revealID++;anims.forEach(a=>a.cancel());anims=[];$('#letter-flight').replaceChildren();$('#chapter-title').style.opacity='';document.body.classList.remove('revealing')}
function flyLetters(sources){
  if(reducedMotion||!sources.length)return;
  const token=++revealID, targets=[...$('#chapter-title').querySelectorAll('.glyph')].map(n=>({text:n.textContent,rect:n.getBoundingClientRect()}));
  const pool=sources.slice(0,80), taken=new Set();
  document.body.classList.add('revealing');$('#chapter-title').style.opacity='0';
  targets.forEach((target,i)=>{
    let index=pool.findIndex((source,j)=>!taken.has(j)&&source.text.toUpperCase()===target.text.toUpperCase());
    if(index<0)index=pool.findIndex((_,j)=>!taken.has(j));
    if(index<0)return;taken.add(index);
    const source=pool[index];const span=make('span','flying-letter',target.text);
    span.style.left=`${source.rect.left}px`;span.style.top=`${source.rect.top}px`;span.style.fontSize=`${source.rect.height*.82}px`;
    $('#letter-flight').append(span);
    const x=target.rect.left-source.rect.left,y=target.rect.top-source.rect.top;
    const scale=target.rect.height/Math.max(1,source.rect.height);
    anims.push(span.animate([{transform:'translate(0,0) scale(1)',opacity:1,filter:'blur(0)'},{transform:`translate(${x*.5+Math.sin(i)*85}px,${y*.35-80-i*8}px) rotate(${i%2?18:-18}deg) scale(.7)`,opacity:.55,filter:'blur(1px)',offset:.4},{transform:`translate(${x}px,${y}px) scale(${scale})`,opacity:1,filter:'blur(0)'}],{duration:1550,delay:i*35,easing:'cubic-bezier(.2,.7,.1,1)',fill:'forwards'}));
  });
  pool.forEach((source,i)=>{if(taken.has(i))return;const span=make('span','flying-letter excess',source.text);span.style.left=`${source.rect.left}px`;span.style.top=`${source.rect.top}px`;span.style.fontSize=`${source.rect.height*.8}px`;$('#letter-flight').append(span);anims.push(span.animate([{transform:'translate(0,0)',opacity:.7},{transform:`translate(${innerWidth*.7-source.rect.left}px,${innerHeight*.52-source.rect.top}px) rotate(${(i%7)*40}deg) scale(.02)`,opacity:0}],{duration:900+(i%5)*95,easing:'cubic-bezier(.6,0,.2,1)',fill:'forwards'}))});
  Promise.allSettled(anims.map(a=>a.finished)).then(()=>{if(token!==revealID)return;$('#chapter-title').style.opacity='';$('#letter-flight').replaceChildren();document.body.classList.remove('revealing');anims=[]});
}
function render({transition=true,sources=[],snapshot=null,previousID='',previousVisual=0}={}){
 const ch=currentChapter();clearAnimations();document.title=ch.kind==='intro'?'Frontier Cascadia — the invention':'Threshold — Frontier Cascadia';
 document.body.dataset.chapter=String(chapterIndex);document.body.dataset.visual=String(visualIndex());document.body.dataset.kind=ch.kind;document.body.classList.remove('illustrated-alert');store.set('threshold-chapter-id',ch.id);
 $('#eyebrow').textContent=ch.eyebrow;$('#chapter-title').replaceChildren();$('#chapter-title').setAttribute('aria-label',ch.title.join(' '));letters(ch.title);fitTitle();
 $('#chapter-description').textContent=ch.description;$('#chapter-description').hidden=!ch.description;
 $('#claim-badge').hidden=!ch.badge;if(ch.badge){$('#claim-badge').textContent=ch.badge.text;$('#claim-badge').dataset.tone=ch.badge.tone}
 $('#visual-caption').textContent=ch.caption;$('#scene-number').textContent=String(chapterIndex+1).padStart(2,'0');
 $('#scene-total').textContent=String(chapters.length).padStart(2,'0');
 $('#chapter-name').textContent=ch.kind==='intro'?'The invention':ch.title.join(' ');
 $('#next-cue').textContent=chapterIndex===0?'Reveal':chapterIndex===chapters.length-1?'End':'Continue';
 $('#previous').disabled=chapterIndex===0;$('#next').disabled=chapterIndex===chapters.length-1;
 $('#brand').classList.toggle('concealed',chapterIndex===0);
 $('#product-stage').hidden=ch.kind!=='product';$('#demo-stage').hidden=ch.kind!=='demo';
 $('#signal-annotation').hidden=ch.id!=='the-signal';
 $('#door-annotation').hidden=ch.id!=='door-state';
 $('#door-state').textContent='OPEN / CLOSED';
 $('#door-detail').textContent='A magnetic contact switch';
 $('#future-annotation').hidden=ch.kind!=='future';
 $('#comparison-stage').hidden=ch.kind!=='comparison';
 $('#comparison-rows').replaceChildren(...(ch.comparison||[]).map(row=>{const tr=make('tr');const label=make('th','',row.label);label.scope='row';tr.append(label,make('td','',row.contact),make('td','',row.threshold));return tr}));
 $('#detection-cue').hidden=ch.id!=='the-question';$('#detection-state').textContent='SIMULATED WALK-THROUGH';
 $('#slide-progress').style.width=`${(chapterIndex+1)/chapters.length*100}%`;
 $('#notes-heading').textContent=ch.title.join(' ');$('#notes-list').replaceChildren(...ch.notes.map(note=>make('p','',note)));
 $('#notes-time').textContent=ch.time;$('#notes-next').textContent=chapters[chapterIndex+1]?.title.join(' ')||'End of presentation';
 $('#status-live').textContent=`Scene ${chapterIndex+1} of ${chapters.length}: ${ch.title.join(' ')}`;
 $$('.overview-item').forEach((button,i)=>button.setAttribute('aria-current',String(i===chapterIndex)));
 if(renderer)renderer.setChapter(visualIndex(),{immediate:!transition||reducedMotion,from:previousVisual});
 if(transition&&!reducedMotion)transitions.play(snapshot,{from:previousID,to:ch.id,backward:chapters.findIndex(c=>c.id===previousID)>chapterIndex});
 if(chapterIndex===1&&transition)flyLetters(sources);
 if(!audience)renderPresenter();
}
function $$(selector){return [...document.querySelectorAll(selector)]}
function navigate(next,{broadcast=true,transition=true}={}){
 next=Math.max(0,Math.min(chapters.length-1,Number(next)||0));
 if(next===chapterIndex)return;
 const snapshot=transitions.capture(),previousID=currentChapter().id,previousVisual=visualIndex();
 const sources=[...$('#chapter-title').querySelectorAll('.glyph')].filter(n=>n.textContent.trim()).map(n=>({text:n.textContent,rect:n.getBoundingClientRect()}));
 chapterIndex=next;store.set('threshold-chapter',next);render({transition,sources,snapshot,previousID,previousVisual});
 if(broadcast)send({type:audience?'state':'navigate',chapter:next});
 if(audience&&soundOn)playTransition();
}
function toggleDialog(id){const dialog=$(id);if(dialog.open){dialog.close();return}$$('dialog[open]').forEach(d=>d.close());dialog.showModal()}
function updateRuntime(){ $('#renderer-state').textContent=frameMode;$('#quality-select').value=quality;$('#motion-select').value=motionOverride;$('#sound-toggle').setAttribute('aria-pressed',String(soundOn));$('#sound-toggle').title=soundOn?'Mute sound (M)':'Enable subtle transition sound (M)';$('#sound-label').textContent=soundOn?'Sound on':'Sound off';document.body.classList.toggle('reduced-motion',reducedMotion);$('#motion-state').textContent=reducedMotion?'Reduced motion':'Full motion';}
async function setSound(value){soundOn=value;try{if(value){audioContext ||= new (window.AudioContext||window.webkitAudioContext)();await audioContext.resume()}else{activeAudio.forEach(node=>{try{node.stop()}catch{}});activeAudio.clear();if(audioContext?.state==='running')await audioContext.suspend()}}catch{soundOn=false}updateRuntime()}
async function playTransition(){if(!soundOn||!audioContext||document.hidden)return;try{if(audioContext.state==='suspended')await audioContext.resume()}catch{return}if(!soundOn||audioContext.state!=='running'||document.hidden)return;const t=audioContext.currentTime;[70,141].forEach((hz,i)=>{if(activeAudio.size>=4)return;const osc=audioContext.createOscillator(),gain=audioContext.createGain();osc.type='sine';osc.frequency.setValueAtTime(hz,t);osc.frequency.exponentialRampToValueAtTime(hz*.55,t+.7);gain.gain.setValueAtTime(0,t);gain.gain.linearRampToValueAtTime(i?.025:.04,t+.06);gain.gain.exponentialRampToValueAtTime(.0001,t+.8);osc.connect(gain).connect(audioContext.destination);activeAudio.add(osc);osc.onended=()=>{activeAudio.delete(osc);osc.disconnect();gain.disconnect()};osc.start(t);osc.stop(t+.85)})}
async function fullscreen(){try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch{$('#toast').textContent='Use your browser’s full-screen command.';$('#toast').hidden=false}}
function validateConsole(raw){if(!raw.trim())return '';const url=new URL(raw);if(url.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||url.username||url.password||url.search||url.hash)throw new Error('Use a local http://127.0.0.1:PORT/ address, without credentials, query text, or fragments.');return url.href}
function updateConsole(){const link=$('#open-console');if(consoleURL){link.href=consoleURL;link.hidden=false;$('#configure-console').textContent='Change console';$('#console-detail').textContent='Opens your selected local app. Pair there privately before presenting.'}else{link.removeAttribute('href');link.hidden=true;$('#configure-console').textContent='Set console address';$('#console-detail').textContent='No console selected. This presentation does not start or control the sensor app.'}$('#console-url').value=consoleURL}
function openPresenter(){const url=new URL(location.href);url.searchParams.set('presenter','1');url.searchParams.set('session',sessionID);const popup=window.open(url.href,`threshold-presenter-${sessionID}`,'popup,width=980,height=760');if(!popup){$('#toast').textContent='Allow this local page to open the presenter window, or use N for rehearsal notes.';$('#toast').hidden=false}}
function renderPresenter(){const ch=currentChapter();$('#presenter-prev').disabled=chapterIndex===0;$('#presenter-next-button').disabled=chapterIndex===chapters.length-1;$('#presenter-title').textContent=ch.title.join(' ');$('#presenter-eyebrow').textContent=`SCENE ${chapterIndex+1} / ${chapters.length} · ${ch.time}`;$('#presenter-script').replaceChildren(...ch.notes.map(n=>make('p','',n)));$('#presenter-next').textContent=chapters[chapterIndex+1]?.title.join(' ')||'End of presentation'}
function updateTimer(){const secs=Math.floor((performance.now()-timerStart)/1000);$('#presenter-timer').textContent=`${Math.floor(secs/60).toString().padStart(2,'0')}:${(secs%60).toString().padStart(2,'0')}`}
$('#previous').addEventListener('click',()=>navigate(chapterIndex-1));$('#next').addEventListener('click',()=>navigate(chapterIndex+1));
$('#fullscreen-toggle').addEventListener('click',fullscreen);$('#sound-toggle').addEventListener('click',()=>setSound(!soundOn));
$('#settings-toggle').addEventListener('click',()=>toggleDialog('#settings-dialog'));$('#overview-toggle').addEventListener('click',()=>toggleDialog('#overview-dialog'));$('#notes-toggle').addEventListener('click',()=>toggleDialog('#notes-dialog'));$('#presenter-toggle').addEventListener('click',openPresenter);
$$('[data-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$$('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close()}}));
$('#configure-console').addEventListener('click',()=>{toggleDialog('#settings-dialog');$('#console-url').focus()});
$('#console-form').addEventListener('submit',event=>{event.preventDefault();try{consoleURL=validateConsole($('#console-url').value);store.set('threshold-console',consoleURL);$('#console-error').textContent='';updateConsole();$('#settings-dialog').close()}catch(error){$('#console-error').textContent=error.message}});
$('#recorded-demo').addEventListener('click',()=>toggleDialog('#recording-dialog'));
$('#quality-select').addEventListener('change',event=>{quality=event.target.value;store.set('threshold-quality',quality);renderer?.setQuality(quality);updateRuntime()});
function updateMotion(){reducedMotion=motionOverride==='system'?motionQuery.matches:motionOverride==='reduce';clearAnimations();renderer?.setReducedMotion(reducedMotion);updateRuntime()}
$('#motion-select').addEventListener('change',event=>{motionOverride=event.target.value;store.set('threshold-motion',motionOverride);updateMotion()});motionQuery.addEventListener('change',updateMotion);
$('#restart').addEventListener('click',()=>{navigate(0);timerStart=performance.now();$('#settings-dialog').close()});
$('#presenter-prev').addEventListener('click',()=>navigate(chapterIndex-1));$('#presenter-next-button').addEventListener('click',()=>navigate(chapterIndex+1));$('#reset-timer').addEventListener('click',()=>timerStart=performance.now());
chapters.forEach((ch,i)=>{const button=make('button','overview-item');button.type='button';button.append(make('span','overview-index',String(i+1).padStart(2,'0')),make('span','',ch.kind==='intro'?'The invention':ch.title.join(' ')));button.addEventListener('click',()=>{$('#overview-dialog').close();navigate(i)});$('#overview-grid').append(button)});
addEventListener('keydown',event=>{if(event.metaKey||event.ctrlKey||event.altKey||event.repeat)return;if(['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.target.isContentEditable)return;if($('dialog[open]')){if(event.key==='Escape')return;return}if((event.key===' '||event.key==='Enter')&&event.target.closest('button,a'))return;
 switch(event.key.toLowerCase()){case ' ':case 'arrowright':case 'pagedown':event.preventDefault();navigate(chapterIndex+1);break;case 'arrowleft':case 'pageup':event.preventDefault();navigate(chapterIndex-1);break;case 'home':event.preventDefault();navigate(0);break;case 'end':event.preventDefault();navigate(chapters.length-1);break;case 'f':fullscreen();break;case 'n':toggleDialog('#notes-dialog');break;case 'o':toggleDialog('#overview-dialog');break;case 's':toggleDialog('#settings-dialog');break;case 'p':if(audience)openPresenter();break;case 'm':setSound(!soundOn);break;case 'q':quality=quality==='auto'?'high':quality==='high'?'low':'auto';store.set('threshold-quality',quality);renderer?.setQuality(quality);updateRuntime();break;}});
let touchStart=null;$('#stage').addEventListener('pointerdown',e=>{if(e.pointerType==='touch'&&!e.target.closest('button,a'))touchStart={x:e.clientX,y:e.clientY}});$('#stage').addEventListener('pointerup',e=>{if(!touchStart)return;const dx=e.clientX-touchStart.x,dy=e.clientY-touchStart.y;touchStart=null;if(Math.abs(dx)>70&&Math.abs(dx)>Math.abs(dy)*1.6)navigate(chapterIndex+(dx<0?1:-1))});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&audioContext?.state==='running')audioContext.suspend()});
addEventListener('resize',fitTitle);
if(channel)channel.onmessage=({data})=>{if(!data||typeof data!=='object')return;if(data.type==='request'&&audience)send({type:'state',chapter:chapterIndex});if(data.type==='navigate'&&audience&&Number.isInteger(data.chapter)){navigate(data.chapter,{broadcast:false});send({type:'state',chapter:chapterIndex})}if(data.type==='state'&&!audience&&Number.isInteger(data.chapter))navigate(data.chapter,{broadcast:false,transition:false})};
render({transition:false});updateRuntime();updateConsole();
if(!audience){document.body.classList.add('presenter-mode');renderPresenter();send({type:'request'});setInterval(updateTimer,1000);updateTimer();frameMode='Presenter controls · no GPU';updateRuntime()}else{
  import('./scene.js').then(async({createScene})=>{renderer=await createScene($('#scene-canvas'),{quality,reducedMotion,onCue:receiveCue,onStatus:status=>{frameMode=status.backend;document.body.dataset.renderer=status.backend;$('#visual-fallback').hidden=status.backend!=='Static';updateRuntime()}});renderer.setChapter(visualIndex(),{immediate:true});document.body.dataset.ready='true';}).catch(()=>{frameMode='Static fallback';document.body.dataset.renderer='Static';document.body.dataset.ready='true';$('#visual-fallback').hidden=false;updateRuntime()});
}
window.thresholdPresentation={goTo:index=>navigate(index),getState:()=>({chapter:chapterIndex,id:currentChapter().id,kind:currentChapter().kind,renderer:frameMode,reducedMotion,quality,audience,soundOn,activeAudio:activeAudio.size,consoleConfigured:Boolean(consoleURL),transition:transitions.diagnostics(),diagnostics:renderer?.diagnostics?.()??null}),getChapters:()=>chapters.map(({id,title,kind,badge})=>({id,title,kind,badge}))};
addEventListener('pagehide',()=>{clearAnimations();renderer?.dispose();channel?.close();activeAudio.forEach(n=>{try{n.stop()}catch{}});audioContext?.close()},{once:true});
