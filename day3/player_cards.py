"""Shared, slot-responsive card presentation for the production notebook."""
from __future__ import annotations

import json
import re


# Stronger than the notebook's old scene overrides: every host uses one layout.
_CARD = '.vx-player-card' * 5
READABLE_CARD_CSS = r'''
<style id="vx-readable-player-cards">
CARD .pc-card-body{
  display:grid!important;grid-template-columns:minmax(0,1fr)!important;
  grid-template-rows:minmax(min-content,1fr) auto auto auto!important;
  gap:clamp(8px,1.1cqw,16px)!important;
}
CARD .pc-photo-info{order:1!important;display:block!important;min-height:min-content!important}
CARD .pc-portrait-box{left:44%!important;width:56%!important;display:block!important}
CARD .pc-portrait{max-width:100%!important;width:auto!important;height:96%!important;max-height:100%!important;aspect-ratio:auto!important}
CARD .pc-info-stack{
  position:relative!important;left:0!important;top:0!important;width:43%!important;height:auto!important;
  display:grid!important;grid-template-rows:repeat(3,auto)!important;
  gap:clamp(6px,1cqh,14px)!important;max-height:none!important;
}
CARD .pc-info-item{min-height:0!important;grid-template-columns:clamp(26px,4cqw,48px) minmax(0,1fr)!important;padding:clamp(6px,1cqw,14px)!important;gap:8px!important}
CARD .pc-info-icon{width:100%!important;height:auto!important;aspect-ratio:1!important;font-size:clamp(24px,3cqw,38px)!important;display:grid!important;place-items:center!important}
CARD .pc-info-label{font-size:clamp(22px,min(2.8cqw,3cqh),34px)!important;white-space:normal!important;overflow-wrap:anywhere!important;letter-spacing:0!important}
CARD .pc-info-value{font-size:clamp(32px,min(4.6cqw,5cqh),56px)!important;line-height:1.2!important;margin:0!important;white-space:normal!important;overflow-wrap:anywhere!important;overflow:visible!important;text-overflow:clip!important}
CARD .pc-identity{order:2!important;padding:10px clamp(78px,12cqw,145px) 10px 12px!important;min-height:0!important}
CARD .pc-name-first{font-size:clamp(24px,3cqw,38px)!important;margin:0!important}
CARD .pc-name-last{font-size:clamp(40px,6.8cqw,84px)!important;margin:0!important;white-space:normal!important;overflow-wrap:anywhere!important;overflow:visible!important;text-overflow:clip!important}
CARD .pc-club-crest{width:clamp(64px,10cqw,120px)!important;height:clamp(64px,10cqw,120px)!important;right:12px!important}
CARD .pc-stat-rows{
  order:3!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;
  grid-template-rows:none!important;grid-auto-rows:auto!important;gap:clamp(6px,1cqw,14px)!important;
}
CARD .pc-stat-row{
  grid-template-columns:clamp(24px,3cqw,38px) minmax(0,1fr)!important;
  grid-template-rows:auto auto!important;min-height:0!important;
  column-gap:6px!important;row-gap:4px!important;padding:clamp(6px,1cqw,14px)!important;
}
CARD .pc-stat-icon{width:auto!important;height:auto!important;font-size:clamp(24px,3cqw,38px)!important}
CARD .pc-stat-label{
  font-family:var(--vx-dense,'Arial Narrow',Arial,sans-serif)!important;
  font-size:clamp(24px,min(3.4cqw,3.6cqh),42px)!important;line-height:1.2!important;
  white-space:normal!important;overflow-wrap:anywhere!important;overflow:visible!important;
  text-overflow:clip!important;letter-spacing:0!important;
}
CARD .pc-stat-value{
  font-size:clamp(36px,min(5cqw,5.4cqh),64px)!important;line-height:1.2!important;
  white-space:normal!important;overflow-wrap:anywhere!important;overflow:visible!important;text-overflow:clip!important;
}
CARD .pc-fixture-row{
  order:4!important;grid-template-columns:repeat(5,minmax(0,1fr))!important;
  grid-template-rows:none!important;gap:clamp(5px,.8cqw,12px)!important;
  padding-top:clamp(32px,3.4cqw,44px)!important;
}
CARD .pc-fixture-row::before{top:0!important;font-size:clamp(24px,2.8cqw,36px)!important;line-height:1.2!important}
CARD .pc-fixture-card{justify-content:flex-start!important;padding:clamp(6px,.8cqw,12px)!important;min-height:0!important;gap:4px!important}
CARD .pc-fixture-main{
  display:grid!important;grid-template-columns:minmax(0,1fr)!important;
  width:100%!important;min-height:0!important;padding:0!important;gap:4px!important;
}
CARD .pc-fixture-badges{display:flex;justify-content:center;align-items:center;gap:4px;min-width:0;width:100%;height:clamp(44px,min(6.5cqw,7cqh),80px)}
CARD .pc-fixture-crest{width:auto!important;height:100%!important;max-width:calc(100% / var(--pc-badge-count,1) - 4px)!important;object-fit:contain!important;flex:none!important}
CARD .pc-fixture-team{
  width:100%!important;font-size:clamp(24px,min(3.2cqw,3.4cqh),40px)!important;line-height:1.2!important;
  white-space:normal!important;overflow-wrap:anywhere!important;overflow:visible!important;text-overflow:clip!important;
}
CARD .pc-fixture-venue{width:100%!important;font-size:clamp(22px,min(2.6cqw,2.8cqh),34px)!important;line-height:1.2!important;margin:0!important}
CARD .pc-fixture-meta{display:grid!important;grid-template-columns:minmax(0,1fr)!important;width:100%!important;padding:4px 0 0!important;gap:4px!important;margin-top:auto!important}
CARD .pc-fixture-gw{width:100%!important;font-size:clamp(22px,min(2.7cqw,2.9cqh),36px)!important;line-height:1.2!important}
CARD .pc-fixture-fdr{width:100%!important;font-size:clamp(28px,min(3.6cqw,3.8cqh),46px)!important;line-height:1.2!important}
</style>
'''.replace('CARD', _CARD)


_FIXTURE_JS = r'''  function fixtureCard(fx, index){
    const missing = !fx || fx.gw === null || fx.gw === undefined;
    const opponent = missing ? '—' : upper(fx.opponent, '—');
    const venue = missing ? '' : text(fx.venue, '').replace(/[()]/g, '');
    // Canonical metadata also supplies badges for payloads (e.g. DEFCON)
    // that contain opponent names but omit their image fields.
    const names = opponent.split(/[+\/]/).map(s=>s.replace(/\s*\([HA]\)\s*$/i,'').trim());
    const explicit = Array.isArray(fx?.badges) ? fx.badges : [];
    const badges = names.map((name,i)=>text(explicit[i], '')
      || (i===0 ? text(fx?.badge, '') : '') || PC_TEAM_BADGES[name] || '');
    const images = badges.filter(Boolean).map(src=>
      `<img class="pc-fixture-crest" src="${esc(src)}" alt="" onerror="this.style.visibility='hidden'">`).join('');
    return `<div class="pc-fixture-card ${missing ? 'fdr-none' : fdrClass(fx)}" data-fixture-index="${index}">
      <div class="pc-fixture-main">
        <div class="pc-fixture-badges" style="--pc-badge-count:${Math.max(1,badges.filter(Boolean).length)}">${images}</div>
        <div class="pc-fixture-team" data-pc-fit>${esc(opponent)}</div>
        <div class="pc-fixture-venue" data-pc-fit>${venue ? `(${esc(venue.toUpperCase())})` : '—'}</div>
      </div>
      <div class="pc-fixture-meta">
        <div class="pc-fixture-gw" data-pc-fit>GW ${esc(missing ? '—' : text(fx.gw,'—'))}</div>
        <div class="pc-fixture-fdr" data-pc-fit>FDR ${esc(missing ? '—' : fdrDisplay(fx))}</div>
      </div>
    </div>`;
  }'''

# CSS defines the preferred size from the live slot; old data-pc-max values
# must not cap the larger table type. Wrapping grows rows rather than clipping.
_FIT_JS = r'''  function fitOne(el){
    if (!el || !el.getClientRects().length) return;
    el.style.removeProperty('font-size');
    const preferred = parseFloat(getComputedStyle(el).fontSize);
    if (!Number.isFinite(preferred)) return;
    let lo=preferred*.8, hi=preferred, best=lo;
    el.style.setProperty('font-size', `${hi}px`, 'important');
    if (elementFits(el)) return;
    for(let i=0;i<12;i++){
      const mid=(lo+hi)/2;
      el.style.setProperty('font-size', `${mid}px`, 'important');
      if(elementFits(el)){best=mid;lo=mid;}else{hi=mid;}
    }
    el.style.setProperty('font-size', `${best}px`, 'important');
  }'''

# Run before every scene's existing capture QA, including cards first revealed
# after mount and scenes that remove data-pc-fit for their own text audit.
_READY_JS = r'''
<script id="vx-readable-card-capture">
(() => {
  const prepare=window.vxPrepareCapture;
  window.vxPrepareCapture=async (...args)=>{
    await document.fonts.ready;
    const cards=[...document.querySelectorAll('.vx-player-card')].filter(card=>
      card.getClientRects().length && getComputedStyle(card).visibility!=='hidden');
    await Promise.all(cards.flatMap(card=>[...card.querySelectorAll('img')].map(img=>img.decode().catch(()=>{}))));
    for(const card of cards){
      card.querySelectorAll('.pc-info-label,.pc-info-value,.pc-name-first,.pc-name-last,.pc-stat-label,.pc-stat-value,.pc-fixture-team,.pc-fixture-venue,.pc-fixture-gw,.pc-fixture-fdr').forEach(el=>el.setAttribute('data-pc-fit',''));
      window.VXPlayerCard.fitText(card);
    }
    // Scene QA uses its own registered font floors; keep them in sync after
    // a slot resize so it cannot restore a stale mount-time size.
    for(const card of cards){
      card.querySelectorAll('[data-vx-fit]').forEach(el=>{
        const size=parseFloat(getComputedStyle(el).fontSize);
        el.dataset.vxPreferred=String(size);el.dataset.vxMin=String(size);
      });
    }
    const report=prepare ? await prepare(...args) : {passed:true};
    for(const card of cards){
      if(!card.getClientRects().length || getComputedStyle(card).visibility==='hidden') continue;
      const body=card.querySelector('.pc-card-body'), bounds=body.getBoundingClientRect();
      const lanes=[...body.children].filter(el=>el.getClientRects().length).sort((a,b)=>a.getBoundingClientRect().top-b.getBoundingClientRect().top);
      for(let i=0;i<lanes.length;i++){
        const box=lanes[i].getBoundingClientRect();
        if(box.top<bounds.top-1 || box.bottom>bounds.bottom+1 || box.left<bounds.left-1 || box.right>bounds.right+1
          || (i && lanes[i-1].getBoundingClientRect().bottom>box.top+1))
          throw Error(`Player card ${card.dataset.playerId}: lane ${lanes[i].className} overlaps or clips`);
      }
      for(const el of card.querySelectorAll('[data-pc-fit],.pc-info-stack,.pc-info-item,.pc-stat-row,.pc-fixture-main,.pc-fixture-meta')){
        if(!el.getClientRects().length) continue;
        if(el.scrollWidth>el.clientWidth+1 || el.scrollHeight>el.clientHeight+1)
          throw Error(`Player card ${card.dataset.playerId}: ${el.className} text clips`);
      }
    }
    for(const card of cards){
      for(const el of card.querySelectorAll('.pc-info-stack,.pc-info-icon,.pc-stat-icon,.pc-fixture-badges')){
        if(!el.getClientRects().length) continue;
        const box=el.getBoundingClientRect(), bounds=el.parentElement.getBoundingClientRect();
        if(box.top<bounds.top-1 || box.bottom>bounds.bottom+1 || box.left<bounds.left-1 || box.right>bounds.right+1)
          throw Error(`Player card ${card.dataset.playerId}: ${el.className} exceeds its parent`);
      }
    }
    return report;
  };
})();
</script>
'''


def readable_player_cards(css: str, js: str, team_metadata: dict) -> tuple[str, str]:
    """Apply once to CELL 15A; all scenes inherit live team badges and sizing."""
    if 'vx-readable-player-cards' in css:
        return css, js
    badges = {}
    for meta in team_metadata.values():
        badge = str(meta.get('badge') or '')
        if not badge:
            continue
        for key in ('short', 'name'):
            name = str(meta.get(key) or '').strip().upper()
            if name:
                badges[name] = badge
    registry = '  const PC_TEAM_BADGES = ' + json.dumps(badges, ensure_ascii=True).replace('<', '\\u003c') + ';\n'
    for name, replacement in (('fixtureCard', registry + _FIXTURE_JS), ('fitOne', _FIT_JS)):
        js, count = re.subn(r'  function ' + name + r'\([^\n]*\)\{.*?\n  \}', lambda _: replacement, js, count=1, flags=re.DOTALL)
        if count != 1:
            raise RuntimeError(f'Shared player-card {name}: expected one function, found {count}')
    return css + READABLE_CARD_CSS, js + _READY_JS
