"""Browser regression checks against the workflow's downloaded card renderer."""
from __future__ import annotations

import argparse
import ast
import json
import os
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from PIL import Image

from day3.run_notebook import PLAYER_CARD_DESIGN_CELL_INDEX, SCENE_DESIGN_CELL_INDEX, _patch_player_card_sample


# Deliberately fictional artwork/data; no external requests or production model execution.
_BADGE = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="48" fill="white"/></svg>'
_METADATA = {
    1: {'short': 'AAA', 'name': 'Test Club Alpha', 'badge': _BADGE},
    2: {'short': 'BBB', 'name': 'Test Club Beta', 'badge': _BADGE},
}


def check_cards(notebook_path: Path, browser_channel: str | None = None) -> None:
    notebook = json.loads(notebook_path.read_text(encoding='utf-8'))
    source = ''.join(notebook['cells'][PLAYER_CARD_DESIGN_CELL_INDEX]['source'])
    namespace = {'team_meta_by_id': _METADATA}
    # The background is decorative. Use a fixture so checks can run before
    # downloading model history and production assets from Drive.
    previous_assets = os.environ.get('DAY1_ASSET_DIR')
    with tempfile.TemporaryDirectory() as assets:
        Image.new('RGB', (8, 8), '#03133f').save(Path(assets) / '4.png')
        os.environ['DAY1_ASSET_DIR'] = assets
        try:
            exec(compile(_patch_player_card_sample(source), '<production-player-card>', 'exec'), namespace)
        finally:
            if previous_assets is None:
                os.environ.pop('DAY1_ASSET_DIR', None)
            else:
                os.environ['DAY1_ASSET_DIR'] = previous_assets
    scene_source = ''.join(notebook['cells'][SCENE_DESIGN_CELL_INDEX]['source'])
    scene_css = []
    expected = {'_D3_DEFCON_PRODUCTION_CSS', '_D3_DESK_SHARED_CSS', '_D3_SHORT_SHARED_CARD_CSS'}
    for node in ast.parse(scene_source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in expected for t in node.targets):
            scene_css.append(ast.literal_eval(node.value))
    if len(scene_css) != len(expected):
        raise RuntimeError('Expected all three production scene card styles for layout checks')

    premium_source = ''.join(notebook['cells'][10]['source'])
    premium_js = next(ast.literal_eval(node.value) for node in ast.walk(ast.parse(premium_source))
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'VX_PREMIUM_JS' for t in node.targets))
    start = premium_js.index('function fitSecondary(root){')
    end = premium_js.index('function removeExactRowDuplicates(root)', start)
    # Exercise the actual notebook's secondary fitter, which runs inside the
    # capture chain after scene cards have registered their font floors.
    secondary_fitter = '<script>(()=>{const cfg={key:"card-test"};' \
        'const fitCache=new WeakMap();const normalize=s=>String(s||"").trim();' \
        'const visible=e=>e.getClientRects().length>0;' \
        'const set=(e,k,v)=>e.style.setProperty(k,String(v),"important");' \
        + premium_js[start:end] \
        + 'window.vxPrepareCapture=async()=>{fitSecondary(document.getElementById("host"));return {passed:true};};})();</script>'

    fixtures = [{'gw': 7+i, 'opponent': 'AAA/BBB' if i == 2 else 'AAA',
                 'venue': 'H/A' if i == 2 else 'H', 'fdr_value': 2.5} for i in range(5)]
    # Long labels, large numbers, full club names, blank weeks and two opponents.
    variants = [
        {'first_name': 'Dominic', 'surname': 'Calvert-Lewin'},
        {'first_name': 'Kiernan', 'surname': 'Dewsbury-Hall', 'info_rows': [
            ['P', 'POSITION', 'MID', ''], ['C', 'CLUB', 'Brighton and Hove Albion', ''], ['£', 'PRICE', '£15.5m', '']]},
        {'first_name': 'Jan Paul', 'surname': 'van Hecke', 'fixtures': [None, *fixtures[:4]]},
        {'first_name': 'Player', 'surname': 'With A Very Long Surname'},
    ]
    payload = {
        'id': 1, 'position': 'FWD', 'club': 'Test Club Alpha', 'club_badge': _BADGE,
        'player_image': _BADGE, 'price': '£15.5m', 'ownership': '100.0%', 'fixtures': fixtures,
        'display_stats': [{'label': label, 'value': value} for label, value in [
            ('SEASON FPL DC POINTS', '199'), ('LAST GW DEFCON', '22'), ('NEXT GW xDEFCON', '14.9'),
            ('DEFCON PER 90', '27.6'), ('SEASON DEFCON', '999')]],
    }
    checks = 0
    with sync_playwright() as playwright:
        options = {'channel': browser_channel} if browser_channel else {}
        browser = playwright.chromium.launch(headless=True, **options)
        page = browser.new_page(viewport={'width': 3840, 'height': 2160})
        try:
            for width, height in ((1202, 1642), (1500, 1594), (1090, 1460), (800, 1100), (700, 1000)):
                for host in ('d3DefHeroHost', 'd3DeskHeroHost', 'd3ShortHeroHost', 'review'):
                    for variant in variants:
                        page.set_content('<html><head>' + namespace['PLAYER_CARD_CSS'] + ''.join(scene_css)
                            + '<style>:root{--vx-dense:Arial}.vx-player-card{font-family:Arial!important}</style></head><body class="vxPremium"><div id="host" class="' + host
                            + f'" style="width:{width}px!important;height:{height}px!important"></div>'
                            + secondary_fitter + namespace['PLAYER_CARD_JS'] + '</body></html>')
                        page.evaluate('data=>window.VXPlayerCard.mount(document.getElementById("host"),data)', payload | variant)
                        # Production scenes remove data-pc-fit, then a card may be resized
                        # or revealed later. Capture must still refit it with loaded fonts.
                        page.evaluate('''async()=>{
                          await document.fonts.ready;
                          document.querySelectorAll('[data-pc-fit]').forEach(el=>{
                            const size=parseFloat(getComputedStyle(el).fontSize);
                            el.dataset.vxFit='true';el.dataset.vxPreferred=String(size);el.dataset.vxMin=String(size);
                            el.removeAttribute('data-pc-fit');
                          });
                          await window.vxPrepareCapture();
                        }''')
                        result = page.evaluate('''()=>{
                          const card=document.querySelector('.vx-player-card');
                          const issues=[];
                          for(const selector of ['.pc-info-stack','.pc-info-icon','.pc-stat-label','.pc-stat-value','.pc-fixture-badges']){
                            for(const el of card.querySelectorAll(selector)){
                              if(!el.getClientRects().length)continue;
                              const box=el.getBoundingClientRect(), parent=el.parentElement.getBoundingClientRect();
                              if(box.left<parent.left-1 || box.right>parent.right+1 || box.top<parent.top-1 || box.bottom>parent.bottom+1)
                                issues.push(selector+' exceeds parent');
                            }
                          }
                          const text=card.querySelector('.pc-stat-label'),value=card.querySelector('.pc-stat-value');
                          return {issues,label:parseFloat(getComputedStyle(text).fontSize),value:parseFloat(getComputedStyle(value).fontSize),
                            badges:card.querySelectorAll('.pc-fixture-crest').length};
                        }''')
                        assert not result['issues'], (width, height, host, variant, result)
                        expected_badges = 5 if 'fixtures' in variant else 6
                        assert result['badges'] == expected_badges, result
                        if width == 1202:
                            assert result['label'] >= 38 and result['value'] >= 56, result
                        # Resize the same mounted card; no re-render or player-specific offsets.
                        page.evaluate('''async()=>{
                          const host=document.getElementById('host');
                          host.style.setProperty('width','1090px','important');
                          host.style.setProperty('height','1460px','important');
                          await window.vxPrepareCapture();
                        }''')
                        checks += 1
        finally:
            browser.close()
    print(f'✅ Shared card browser QA: {checks} layouts, long labels/names, blank/double fixtures and live resizing')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('notebook', type=Path)
    parser.add_argument('--browser-channel', default=None)
    args = parser.parse_args()
    check_cards(args.notebook, args.browser_channel)
