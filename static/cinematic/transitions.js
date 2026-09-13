/** Authored, interruptible visual cuts. The presentation clock never advances a chapter. */
export function createTransitions({isReduced=()=>false,isAudience=()=>true}={}) {
  let animations=[], echoes=[], sequence=0, active=false, family='cut';
  const $=selector=>document.querySelector(selector);
  const animate=(node,keyframes,options)=>{if(!node||node.hidden)return;const animation=node.animate(keyframes,{duration:950,easing:'cubic-bezier(.16,1,.3,1)',fill:'both',...options});animations.push(animation);return animation};
  const cancel=()=>{sequence++;animations.forEach(a=>a.cancel());animations=[];echoes.forEach(e=>e.remove());echoes=[];active=false;document.body.classList.remove('scene-transitioning');};
  function capture(){
    // Retargeting a moving scene must not resurrect its not-yet-visible incoming title.
    if(active)return {kind:document.body.dataset.kind,interrupted:true,lines:[]};
    const title=$('#chapter-title');
    return {kind:document.body.dataset.kind,lines:[...title.querySelectorAll('.title-line')].map(line=>{const r=line.getBoundingClientRect(),s=getComputedStyle(line);let opacity=1;for(let n=line;n&&n!==document.body;n=n.parentElement)opacity*=Number(getComputedStyle(n).opacity);return {node:line.cloneNode(true),opacity,text:line.textContent,left:r.left,top:r.top,width:r.width,height:r.height,font:s.font,fontSize:s.fontSize,fontFamily:s.fontFamily,fontWeight:s.fontWeight,letterSpacing:s.letterSpacing,lineHeight:s.lineHeight,color:s.color,textAlign:s.textAlign,direction:s.direction}}).filter(line=>line.opacity>.05)};
  }
  function play(snapshot,{from='',to='',backward=false}={}){
    cancel();if(isReduced()||!isAudience())return;
    const token=++sequence, sign=backward?-1:1;
    active=true;document.body.classList.add('scene-transitioning');
    const reveal=to==='reveal';
    const continuous=(from==='the-question'&&to==='the-signal')||(from==='the-signal'&&to==='the-question');
    const aperture=['door-state','sensor-comparison','built-software','console-handoff','start-here'].includes(to)&&!continuous;
    family=reveal?'gravitational-reveal':continuous?'field-continuity':aperture?'threshold-aperture':to==='a-door'?'comedy-cut':'depth-travel';
    document.body.dataset.transition=family;
    if(snapshot&&!reveal){
      snapshot.lines.forEach((line,index)=>{const ghost=line.node.cloneNode(true);ghost.className='transition-echo';Object.assign(ghost.style,{left:`${line.left}px`,top:`${line.top}px`,width:`${line.width}px`,height:`${line.height}px`,fontSize:line.fontSize,fontFamily:line.fontFamily,fontWeight:line.fontWeight,letterSpacing:line.letterSpacing,lineHeight:line.lineHeight,color:line.color,textAlign:line.textAlign,direction:line.direction});$('#transition-echoes').append(ghost);echoes.push(ghost);
        animate(ghost,[{opacity:Math.min(.7,line.opacity),translate:'0 0',filter:'blur(0px)',scale:'1'},{opacity:0,translate:`${-sign*40}px -20px`,filter:'blur(9px)',scale:'.97'}],{duration:380,delay:index*35,easing:'cubic-bezier(.4,0,1,1)'});
      });
    }
    const enterDelay=reveal?1450:to==='a-door'?190:continuous?160:aperture?410:300;
    if(!reveal){
      [...$('#chapter-title').querySelectorAll('.title-line')].forEach((line,index)=>animate(line,[{opacity:0,translate:`${sign*24}px 45px`,clipPath:'inset(100% 0 0 0)',filter:'blur(5px)'},{opacity:1,translate:'0 0',clipPath:'inset(-15% -10% -15% -10%)',filter:'blur(0px)'}],{duration:900,delay:enterDelay+index*110}));
    }
    animate($('#eyebrow'),[{opacity:0,translate:'0 9px',letterSpacing:'.32em'},{opacity:1,translate:'0 0',letterSpacing:'.18em'}],{duration:850,delay:enterDelay-50});
    // Provenance and capability labels stay readable throughout the visual transition.
    animate($('#chapter-description'),[{opacity:0,translate:'0 13px'},{opacity:1,translate:'0 0'}],{duration:750,delay:enterDelay+220});
    if(aperture){
      const left=$('#transition-panel-left'),right=$('#transition-panel-right'),slit=$('#transition-slit');
      animate(left,[{opacity:0,transform:'translateX(-104%)'},{opacity:.88,transform:'translateX(0)',offset:.32},{opacity:.95,transform:'translateX(0)',offset:.46},{opacity:0,transform:'translateX(-104%)'}],{duration:1100,easing:'cubic-bezier(.65,0,.35,1)'});
      animate(right,[{opacity:0,transform:'translateX(104%)'},{opacity:.88,transform:'translateX(0)',offset:.32},{opacity:.95,transform:'translateX(0)',offset:.46},{opacity:0,transform:'translateX(104%)'}],{duration:1100,easing:'cubic-bezier(.65,0,.35,1)'});
      animate(slit,[{opacity:0,scale:'1 .08',filter:'blur(9px)'},{opacity:.7,scale:'1 1',filter:'blur(0px)',offset:.36},{opacity:.3,scale:'90 1.1',filter:'blur(20px)',offset:.65},{opacity:0,scale:'150 1.2',filter:'blur(30px)'}],{duration:1100,easing:'cubic-bezier(.65,0,.35,1)'});
    }else if(continuous){
      animate($('#transition-field'),[{opacity:0,scale:'.08'},{opacity:.25,scale:'.38',offset:.23},{opacity:0,scale:'1.6'}],{duration:1400,easing:'cubic-bezier(.15,.55,.15,1)'});
    }else if(reveal){
      animate($('#transition-field'),[{opacity:0,scale:'1.2'},{opacity:.14,scale:'.1',offset:.55},{opacity:0,scale:'1.8'}],{duration:1800,easing:'cubic-bezier(.5,0,.2,1)'});
    }
    if(to==='built-software')animate($('#product-stage'),[{opacity:0,translate:'90px 45px',scale:'.78',filter:'blur(12px)'},{opacity:1,translate:'0 0',scale:'1',filter:'blur(0)'}],{duration:1300,delay:260});
    if(to==='console-handoff')animate($('#demo-stage'),[{opacity:0,translate:'60px 0',scale:'.93',filter:'blur(8px)'},{opacity:1,translate:'0 0',scale:'1',filter:'blur(0)'}],{duration:1000,delay:380});
    if(to==='sensor-comparison'){
      animate($('#comparison-stage'),[{opacity:0,translate:'45px 0',filter:'blur(5px)'},{opacity:1,translate:'0 0',filter:'blur(0)'}],{duration:900,delay:380});
      [...$('#comparison-rows').children].forEach((row,i)=>animate(row,[{opacity:0,translate:'25px 0'},{opacity:1,translate:'0 0'}],{duration:750,delay:460+i*100}));
    }
    Promise.allSettled(animations.map(a=>a.finished)).then(()=>{if(token!==sequence)return;animations.forEach(a=>a.cancel());animations=[];echoes.forEach(e=>e.remove());echoes=[];active=false;document.body.classList.remove('scene-transitioning')});
  }
  return {capture,play,cancel,diagnostics:()=>({active,family,animations:animations.length,echoes:echoes.length})};
}
