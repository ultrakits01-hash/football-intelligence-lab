const menu=document.querySelector('.menu'),nav=document.querySelector('.nav nav');menu?.addEventListener('click',()=>{nav.style.display=nav.style.display==='flex'?'none':'flex';if(nav.style.display==='flex'){Object.assign(nav.style,{position:'absolute',top:'76px',left:'0',right:'0',padding:'22px',background:'#090e15',flexDirection:'column',borderBottom:'1px solid #1c2a39'})}});const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.animate([{opacity:0,transform:'translateY(22px)'},{opacity:1,transform:'none'}],{duration:650,easing:'cubic-bezier(.2,.8,.2,1)',fill:'both'});io.unobserve(e.target)}}),{threshold:.08});document.querySelectorAll('.feature,.shot-frame,.pipeline>div,.league-list div').forEach(x=>io.observe(x));

// Reliable in-page navigation (works even when opened via Python http.server).
document.querySelectorAll('[data-scroll]').forEach(a=>a.addEventListener('click',e=>{
  const id=a.dataset.scroll, target=document.getElementById(id);
  if(!target)return;
  e.preventDefault();
  target.scrollIntoView({behavior:'smooth',block:'start'});
  history.replaceState(null,'','#'+id);
}));
