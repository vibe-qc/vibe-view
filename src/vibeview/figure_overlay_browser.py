"""Client overlay mounting; pixels come from the shared image compositor."""
FIGURE_OVERLAY_JS = r"""
(function(){
 if(window.vvFigureOverlay) return;
 function find(){var refs=window.trame&&window.trame.refs;if(!refs)return null;
   for(var k of Object.keys(refs)){try{var v=refs[k].$.provides.view;
     if(v&&v.openglRenderWindow&&v.renderer)return v;}catch(e){}}
   return null;}
 function connect(){var t=window.trame,v=find();if(!t||!t.state||!v)return false;
   var canvas=v.openglRenderWindow.getCanvas(),host=canvas.parentElement;
   if(getComputedStyle(host).position==='static')host.style.position='relative';
   var img=document.createElement('img');img.id='vv-figure-overlay';img.alt='Figure annotations';
   Object.assign(img.style,{position:'absolute',left:'0',top:'0',width:'100%',height:'100%',
     pointerEvents:'none',zIndex:'3'});host.appendChild(img);
   function update(value){img.src=value||'';img.style.display=value?'block':'none';}
   t.state.watch(['figure_overlay_data'],update);update(t.state.get('figure_overlay_data'));
   var last='';function resize(){var r=canvas.getBoundingClientRect();
     var w=Math.round(r.width),h=Math.round(r.height),key=w+','+h;
     if(w>0&&h>0&&key!==last){last=key;t.trigger('figure_viewport_size',[w,h]);}}
   var observer=new ResizeObserver(resize);observer.observe(canvas);resize();
   window.vvFigureOverlay={image:img,observer:observer};return true;}
 var attempts=0,timer=setInterval(function(){
   if(connect()||++attempts>600)clearInterval(timer);},250);
})();
"""
