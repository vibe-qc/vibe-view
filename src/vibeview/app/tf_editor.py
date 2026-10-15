"""The transfer-function curve editor: a client-side widget over the window.

Part of the ``vibeview.app`` package; see its ``__init__`` for ``create_app``.

The widget draws, in an SVG inside ``#vv-tf-editor``, the colour nodes of
the active volume's transfer function as a gradient and its opacity nodes
as a polyline with a handle per node. A handle drags with the pointer
(position along the window, opacity up and down), a click on the gradient
adds a node there, a double-click removes one, and the keyboard works too:
each handle is focusable with the slider role, the arrow keys move it,
Delete removes it and Enter inserts a node after it. Every change commits
the whole node list to the server through the ``update_volume_nodes``
trigger, which rebuilds the function and pushes the property; the voxels
never re-send. The server's state then redraws the widget, keeping focus
on the node that had it, so the keyboard and the sliders stay in step. The
widget redraws only when the function, the window or its host changed:
the page's DOM-change hook fires for the redraw's own mutation too, and
redrawing on that would loop every frame and drop focus and drags.

The editor is the node-by-node follow-up of the direct volume rendering
design (docs/volume_rendering_design.md, decision 2); the six ramp sliders
remain beside it as the coarse, slider-shaped path.
"""

from __future__ import annotations

from vibeview.app.scene import _ON_DOM_CHANGE_JS

#: The element the widget renders into; ``controls.py`` places it in the
#: volume card and ``tests/test_volume_curve_editor.py`` looks for it.
TF_EDITOR_ID = "vv-tf-editor"

#: The server trigger the widget commits to (``bindings.py`` registers it).
TF_EDITOR_TRIGGER = "update_volume_nodes"

_TF_EDITOR_JS = (
    "typeof document !== 'undefined' && (function(){"
    "if(window._vvTfEditor)return;window._vvTfEditor=1;"
    + _ON_DOM_CHANGE_JS
    + "var W=260,H=110,PAD=10,drag=null,last=null;"
    "function st(k){try{var v=window.trame.state.get(k);return v===undefined?null:v;}"
    "catch(e){return null;}}"
    "function commit(nodes){window.trame.trigger('" + TF_EDITOR_TRIGGER + "',[nodes]);}"
    "function X(x,lo,hi){return PAD+(x-lo)/((hi-lo)||1)*(W-2*PAD);}"
    "function Y(a){return H-PAD-a*(H-2*PAD);}"
    "function points(nodes,lo,hi){return nodes.slice().sort(function(a,b){return a[0]-b[0];})"
    ".map(function(n){return X(n[0],lo,hi)+','+Y(n[1]);}).join(' ');}"
    # Redraw only when the inputs changed or the host is empty: the DOM-change
    # hook fires for the redraw's own mutation, which would otherwise loop.
    "function render(){var host=document.getElementById('" + TF_EDITOR_ID + "');"
    "if(!host)return;var tf=st('vol_tf'),win=st('vol_window'),mode=st('vol_mode');"
    "if(!tf||!win||mode!=='volume'){if(host.firstChild)host.innerHTML='';last=null;return;}"
    "if(drag)return;var key=JSON.stringify([tf,win]);"
    "if(key===last&&host.firstChild)return;last=key;"
    "var lo=Number(win[0]),hi=Number(win[1]),span=(hi-lo)||1;"
    "var nodes=(tf.opacity||[]).map(function(n){return [Number(n[0]),Number(n[1])];})"
    ".sort(function(a,b){return a[0]-b[0];});"
    "var focused=document.activeElement&&document.activeElement.classList"
    "&&document.activeElement.classList.contains('vv-tf-node')"
    "?Number(document.activeElement.getAttribute('data-i')):-1;"
    "var stops=(tf.color||[]).map(function(c){var t=(Number(c[0])-lo)/span*100;"
    "return '<stop offset=\"'+t+'%\" stop-color=\"rgb('+Math.round(c[1]*255)+','"
    "+Math.round(c[2]*255)+','+Math.round(c[3]*255)+')\"/>';}).join('');"
    "var circles=nodes.map(function(n,i){return '<circle class=\"vv-tf-node\" data-i=\"'+i"
    "+'\" cx=\"'+X(n[0],lo,hi)+'\" cy=\"'+Y(n[1])+'\" r=\"5\" tabindex=\"0\" role=\"slider\"'"
    "+' aria-label=\"Opacity node '+(i+1)+' of '+nodes.length+'\" aria-valuemin=\"0\"'"
    "+' aria-valuemax=\"1\" aria-valuenow=\"'+n[1].toFixed(2)+'\" aria-valuetext=\"'"
    "+n[1].toFixed(2)+' at '+n[0].toPrecision(3)+'\" fill=\"#fff\" stroke=\"#4361ee\""
    " stroke-width=\"2\" style=\"cursor:grab;outline-offset:2px\"/>';}).join('');"
    "host.innerHTML='<svg class=\"vv-tf-svg\" viewBox=\"0 0 '+W+' '+H+'\" width=\"100%\""
    " height=\"'+H+'\" style=\"touch-action:none;display:block\">'"
    "+'<defs><linearGradient id=\"vv-tf-grad\" x1=\"0\" x2=\"1\" y1=\"0\" y2=\"0\">'+stops"
    "+'</linearGradient></defs>'"
    "+'<rect class=\"vv-tf-bg\" x=\"'+PAD+'\" y=\"'+PAD+'\" width=\"'+(W-2*PAD)+'\" height=\"'"
    "+(H-2*PAD)+'\" fill=\"url(#vv-tf-grad)\" opacity=\"0.6\" rx=\"3\" style=\"cursor:copy\"/>'"
    "+'<polyline points=\"'+points(nodes,lo,hi)+'\" fill=\"none\" stroke=\"#fff\""
    " stroke-width=\"2\"/>'+circles+'</svg>';"
    "attach(host,nodes,lo,hi);"
    "if(focused>=0){var again=host.querySelector('.vv-tf-node[data-i=\"'"
    "+Math.min(focused,nodes.length-1)+'\"]');if(again)again.focus();}}"
    "function attach(host,nodes,lo,hi){var svg=host.querySelector('svg');if(!svg)return;"
    "function toData(ev){var r=svg.getBoundingClientRect();"
    "var px=(ev.clientX-r.left)*W/r.width,py=(ev.clientY-r.top)*H/r.height;"
    "var x=lo+(px-PAD)/(W-2*PAD)*(hi-lo),a=(H-PAD-py)/(H-2*PAD);"
    "return [Math.min(hi,Math.max(lo,x)),Math.min(1,Math.max(0,a))];}"
    "function copy(){return nodes.map(function(n){return n.slice();});}"
    "svg.querySelectorAll('.vv-tf-node').forEach(function(c){"
    "var i=Number(c.getAttribute('data-i'));"
    # preventDefault on pointerdown also suppresses the browser's focus, so
    # the handle focuses itself; a click that never moved commits nothing.
    "c.addEventListener('pointerdown',function(ev){ev.preventDefault();ev.stopPropagation();"
    "c.focus();drag={i:i,nodes:copy(),moved:false};c.setPointerCapture(ev.pointerId);});"
    "c.addEventListener('pointermove',function(ev){if(!drag||drag.i!==i)return;"
    "var p=toData(ev);drag.nodes[i]=p;drag.moved=true;c.setAttribute('cx',X(p[0],lo,hi));"
    "c.setAttribute('cy',Y(p[1]));var pl=svg.querySelector('polyline');"
    "if(pl)pl.setAttribute('points',points(drag.nodes,lo,hi));});"
    "c.addEventListener('pointerup',function(ev){if(!drag)return;var d=drag;drag=null;"
    "if(d.moved)commit(d.nodes);});"
    "c.addEventListener('dblclick',function(ev){ev.preventDefault();if(nodes.length<=2)return;"
    "commit(nodes.filter(function(_,j){return j!==i;}));});"
    # Read the current nodes at key time: a fast repeat must step from the
    # last commit, not from the nodes captured when the handle was drawn.
    "c.addEventListener('keydown',function(ev){var cur=st('vol_tf');"
    "var n=(cur&&cur.opacity&&cur.opacity.length===nodes.length?cur.opacity:nodes)"
    ".map(function(q){return [Number(q[0]),Number(q[1])];});var step=(hi-lo)/50;"
    "if(ev.key==='ArrowUp')n[i][1]=Math.min(1,n[i][1]+0.05);"
    "else if(ev.key==='ArrowDown')n[i][1]=Math.max(0,n[i][1]-0.05);"
    "else if(ev.key==='ArrowRight')n[i][0]=Math.min(hi,n[i][0]+step);"
    "else if(ev.key==='ArrowLeft')n[i][0]=Math.max(lo,n[i][0]-step);"
    "else if((ev.key==='Delete'||ev.key==='Backspace')&&n.length>2)n.splice(i,1);"
    "else if(ev.key==='Enter'||ev.key==='+'){var j=i+1<n.length?i+1:i;"
    "n.splice(i+1,0,[(n[i][0]+n[j][0])/2,(n[i][1]+n[j][1])/2]);}"
    "else return;ev.preventDefault();commit(n);});});"
    "svg.addEventListener('pointerdown',function(ev){"
    "if(ev.target.classList&&ev.target.classList.contains('vv-tf-node'))return;"
    "commit(nodes.concat([toData(ev)]));});}"
    "window._vvOnDomChange(render);"
    "(function watch(tries){var t=window.trame;"
    "if(t&&t.state&&t.state.watch){t.state.watch(['vol_tf','vol_window','vol_mode'],render);"
    "return;}if(tries>0)setTimeout(function(){watch(tries-1);},100);})(100);})()"
)


def tf_editor_script() -> str:
    """The page script that owns the curve editor."""
    return _TF_EDITOR_JS
