"""Render editing experiments from the saved PSD and require a recorded review."""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw
from psd_tools import PSDImage
from skimage.color import rgb2hsv, hsv2rgb

from . import storage
from .storage import read_json, write_json, save_image, digest, atomic_write, progress
from .composition import render_generated_psd

CHECKS = {
    'input_alignment':'入力と線画の一致・補正前後・残る再描画差',
    'material_masks':'強い仮色での飛び地・取り残し・別素材への混入',
    'recolor':'Base色替え時の影の濁り・元色残り・変更範囲',
    'motifs_lighting':'模様/固有色と照明色・色補正の分離',
    'linework':'線単独と線OFF、点状汚れ/塗り跡/欠け',
    'background':'背景単独と濃い仮背景、四辺と隙間の断片',
    'palette':'同素材のBase色統一と異素材を誤統合していないこと',
    'hierarchy_budget':'パーツ階層・役割の隣接・描画枚数/フォルダ数',
    'readback':'保存PSDの読み戻し、原画差と編集目標差',
    'delivery':'実在パス・入力不変・未達/実アプリ未確認の明記',
}


def checked_manifest(job):
    manifest = read_json(job/'layers.json')
    if manifest.get('pipeline') not in ('compact','coloring'):
        raise ValueError('Post-review requires a compact or coloring job')
    if manifest.get('pipeline') == 'coloring':
        from .coloring import load_job
        _,_,_,plan,_=load_job(job)
        lighting=plan.get('lighting')
        if lighting and (digest(lighting['guide_path'])!=lighting['guide_hash'] or digest(job/'lighting_aligned.png')!=lighting['aligned_hash']):
            raise ValueError('Lighting guide changed; rebuild before post-review')
    if digest(manifest['psd_path']) != manifest['psd_hash'] or digest(job/'semantic_plan.json') != manifest['semantic_plan_hash']:
        raise ValueError('PSD or semantic plan changed; rebuild before post-review')
    return manifest


def recolored_source(layer, hue_shift):
    rgba = np.array(layer.topil().convert('RGBA'))
    hsv = rgb2hsv(rgba[...,:3].astype(np.float32)/255)
    hsv[...,0] = (hsv[...,0]+hue_shift)%1
    # Gray/black materials need a visible diagnostic color too; this is QA only.
    hsv[...,1] = np.maximum(hsv[...,1],.6)
    hsv[...,2] = np.maximum(hsv[...,2],.65)
    rgba[...,:3] = np.round(hsv2rgb(hsv)*255).astype(np.uint8)
    return Image.fromarray(rgba)


def sheet(panels, path, columns=3):
    canvas = Image.new('RGB',(columns*360,((len(panels)+columns-1)//columns)*580),'#e8eaed')
    draw = ImageDraw.Draw(canvas)
    for index,(title,picture) in enumerate(panels):
        picture=picture.copy();picture.thumbnail((350,538))
        x,y=index%columns*360,index//columns*580
        canvas.paste(picture,(x+(360-picture.width)//2,y+30))
        draw.text((x+8,y+8),title,fill='black')
    save_image(canvas,path)


def post_review(job):
    job=Path(job).resolve()
    manifest=checked_manifest(job)
    plan=read_json(job/'semantic_plan.json')
    psd=PSDImage.open(manifest['psd_path'])
    items={item['name']:item for item in manifest['layers']}
    pixels=[layer for layer in psd.descendants() if not layer.is_group()]
    if len(items)!=len(pixels):
        raise ValueError('Post-review requires unique layer names matching the saved PSD')
    folder=job/'post_review'
    folder.mkdir(exist_ok=True)
    files=[]
    def render(name, hide=(), only=None, recolor=(), background='white'):
        with ExitStack() as stack:
            for layer in pixels:
                role=items[layer.name]['group']
                layer.visible = role not in hide and (only is None or role in only)
                if layer.name in recolor:
                    stack.enter_context(patch.object(layer,'topil',return_value=recolored_source(layer,1/3)))
            image=render_generated_psd(psd)
        backing=Image.new('RGBA',image.size,background)
        picture=Image.alpha_composite(backing,image).convert('RGB')
        path=folder/f'{name}.png'
        save_image(picture,path);files.append(path)
        return picture
    reference=Image.open(job/'reference.png').convert('RGB')
    current=render('current')
    panels=[('Source palette flats' if manifest.get('pipeline')=='coloring' else 'Reference',reference),('Saved PSD',current),
            ('Base + ink',render('base',only=('Base','Background','Lineart'))),
            ('Ink alone',render('ink',only=('Lineart',))),
            ('Background alone',render('background',only=('Background',),background='#455264')),
            ('Character on dark',render('dark_background',hide=('Background',),background='#1c2636')),
            ('Ink OFF',render('no_ink',hide=('Lineart',))),
            ('Shadows OFF',render('no_shadow',hide=('Shadows',))),
            ('Highlights OFF',render('no_highlight',hide=('Highlights',)))]
    sheet(panels,folder/'overview.png');files.append(folder/'overview.png')
    palettes={}
    for part in plan['parts']:
        if part['semantic_id']=='background':continue
        palettes.setdefault(part.get('palette_id',part['semantic_id']),[]).append(part)
    recolor_panels=[]
    for index,(palette,parts) in enumerate(palettes.items()):
        names=[p.get('display_name',p['semantic_id'])+'_Base' for p in parts]
        picture=render(f'recolor_{index:02d}',recolor=names)
        recolor_panels.append((palette,picture))
    for start in range(0,len(recolor_panels),6):
        path=folder/f'recolor_sheet_{start//6+1:02d}.png'
        sheet(recolor_panels[start:start+6],path);files.append(path)
    labels=np.load(job/'semantic_labels.npy')
    lut=np.zeros((len(plan['parts'])+1,3),np.float32)
    for index in range(1,len(lut)):
        lut[index]=hsv2rgb(np.array([[[index*.61803398875%1,.8,.95]]]))[0,0]
    mask_picture=Image.fromarray(np.uint8(np.round(lut[labels]*255)))
    save_image(mask_picture,folder/'material_masks.png');files.append(folder/'material_masks.png')
    # Include the actual pre-build inspection artifacts in the review context.
    # Optional for fixtures and old jobs, required visually by the workflow.
    files += [path for path in (job/'alignment_comparison.png', job/'background_only.png') if path.exists()]
    files += sorted(job.glob('semantic_review_*.png'))
    assert digest(manifest['psd_path'])==manifest['psd_hash']
    context={key:manifest[key] for key in ('psd_hash','semantic_plan_hash')}
    context['evidence_hashes']={path.relative_to(job).as_posix():digest(path) for path in files}
    write_json(job/'post_review_context.json',context)
    template={'context':context,'checks':{key:{'status':'pending','notes':'','evidence':[]} for key in CHECKS}}
    # Keep prior assessments for audit; a new build needs a fresh review context.
    assessment=job/'post_review_assessment.json'
    if assessment.exists():
        atomic_write(job/'history'/f'post_review_{digest(assessment)[:12]}.json',assessment.read_bytes())
    write_json(assessment,template)
    md=['# Astra事後チェック（未確認）','',f'- PSD: `{manifest["psd_path"]}`',
        '- 手順: docs/POST_REVIEW_CHECKLIST.md。画像を実際に開き、各項目へ根拠と結果を記録する。',
        '- 数値合格、画像の生成、CLI成功だけでチェックを付けない。','']
    md += [f'- [ ] {key}: {label}' for key,label in CHECKS.items()]
    md += ['',f'記入先: `{assessment}`',f'比較画像: `{folder}`','']
    atomic_write(storage.DOCS/'jobs'/f'{job.name}-post-review.md','\n'.join(md))
    progress(job,'post_review_required','色替え・単独表示を生成。Astraの事後確認待ち',
             next_action='POST_REVIEW_CHECKLIST.mdに従い画像を開きassessment記入後finish-review')
    return {'job':str(job),'assessment':str(assessment),'overview':str(folder/'overview.png'),
            'recolor_sheets':len(range(0,len(recolor_panels),6)),'reviewed':False}


def finish_review(job, assessment=None):
    job=Path(job).resolve()
    manifest=checked_manifest(job)
    context=read_json(job/'post_review_context.json')
    record=read_json(assessment or job/'post_review_assessment.json')
    if record.get('context')!=context or any(context[key]!=manifest[key] for key in ('psd_hash','semantic_plan_hash')):
        raise ValueError('Review context is stale; run post-review again')
    for path,expected in context['evidence_hashes'].items():
        if digest(job/path)!=expected:
            raise ValueError('Review evidence changed; run post-review again')
    checks=record.get('checks',{})
    if set(checks)!=set(CHECKS):
        raise ValueError('Every checklist item is required')
    for key,check in checks.items():
        if check.get('status') not in ('pass','limitation','fail') or not str(check.get('notes','')).strip():
            raise ValueError(f'Unreviewed checklist item: {key}')
        evidence=check.get('evidence',[])
        if not evidence or any(path not in context['evidence_hashes'] for path in evidence):
            raise ValueError(f'Valid visual evidence required: {key}')
    analysis=read_json(job/'analysis.json')
    if analysis.get('psd_hash')!=manifest['psd_hash']:
        raise ValueError('Evaluation does not match saved PSD')
    failed=[key for key,v in checks.items() if v['status']=='fail']
    limits=[key for key,v in checks.items() if v['status']=='limitation']
    result={'reviewed':True,'passed':bool(analysis['passed'] and not failed),'failed_checks':failed,
            'limitations':limits,'psd_hash':manifest['psd_hash'],'assessment':record}
    write_json(job/'post_review_result.json',result)
    phase='complete_with_limitations' if result['passed'] and limits else 'complete' if result['passed'] else 'needs_repair'
    md=['# Astra事後チェック結果','',f'判定: {phase}',f'PSD: `{manifest["psd_path"]}`','']
    for key,check in checks.items():
        md += [f'- [{"x" if check["status"] != "fail" else " "}] {CHECKS[key]} — {check["status"]}',
               f'  - {check["notes"]}',f'  - 根拠: {", ".join(check["evidence"])}']
    atomic_write(storage.DOCS/'jobs'/f'{job.name}-post-review.md','\n'.join(md)+'\n')
    destination=Path(manifest['psd_path']).parent
    if destination!=job:
        atomic_write(destination/'post_review_result.json',(job/'post_review_result.json').read_bytes())
        atomic_write(destination/'post_review.md','\n'.join(md)+'\n')
    recorder=progress
    if manifest.get('pipeline')=='coloring':
        from .coloring import event
        recorder=event
    recorder(job,phase,'Astra事後レビューを記録',next_action='未達があれば局所修正して再構築・事後チェック',
             post_review_passed=result['passed'],post_review_limitations=limits)
    return {'job':str(job),'passed':result['passed'],'limitations':limits,'failed_checks':failed}
