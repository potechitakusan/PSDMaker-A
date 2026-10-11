"""Read-only scene for all 50 lighting choices; shares PSD lighting equations."""
from pathlib import Path
import numpy as np
from scipy import ndimage as ndi
from PIL import Image
from .storage import digest
from .light_presets import light_preset
from .normal_lighting import load_normal_lighting,normal_dot,material_normal_layers


class NormalPreviewScene:
    def __init__(self,job):
        from .coloring import load_job,paint_preview
        from .artist import LIGHTING
        job,_,palette,plan,arrays=load_job(job)
        lighting=plan.get('lighting')
        if not lighting or lighting.get('method')!='normals':raise ValueError('No normal lighting in this job')
        if digest(job/'flat_preview.png')!=lighting['flat_hash']:raise ValueError('Coloring changed; prepare normals again')
        labels,flat,lut=paint_preview(job,plan,arrays,palette,True,write=False)
        self.job=job;self.lighting=lighting.copy();self.ink=arrays['ink'].copy()
        self.normals=load_normal_lighting(lighting,self.ink.shape)
        self.filled=self.normals.copy();self.flat=flat
        self.clean=~ndi.binary_dilation(self.ink,iterations=2)
        self.materials=[]
        for i,p in enumerate(plan['parts'],1):
            if p['semantic_id']=='background':continue
            mask=labels==i;inner=mask & self.clean
            if not inner.any():inner=mask
            nearest=ndi.distance_transform_edt(~inner,return_distances=False,return_indices=True)
            self.filled[mask]=self.normals[tuple(ix[mask] for ix in nearest)]
        for key,entry in palette['palettes'].items():
            ids=[i for i,p in enumerate(plan['parts'],1) if p.get('palette_id')==key]
            # Jobs using no palette_id are invalid in coloring; load_job checks it.
            mask=np.isin(labels,ids)
            if mask.any():self.materials.append((mask,mask & self.clean,np.asarray(entry['rgb'],np.float32)/255,entry.get('tones')))
        temperature=lighting.get('temperature','neutral')
        if temperature not in LIGHTING:raise ValueError('Unknown lighting temperature')
        self.neutral=[np.asarray(v,np.float32) for v in LIGHTING[temperature]]

    def render(self,preset=1):
        choice=light_preset(preset)
        result=self.flat.copy()
        if choice['enabled']:
            dot=normal_dot(self.normals,choice['light'])
            for mask,sample_mask,base,tones in self.materials:
                shadow,sa,highlight,ha,diag=material_normal_layers(
                    self.filled[mask],choice['light'],base,tones,dot[sample_mask],self.lighting,self.neutral)
                rgb=base*(1-sa[:,None]+shadow*sa[:,None])
                result[mask]=rgb+(1-rgb)*highlight*ha[:,None]
        result[self.ink]=0
        return Image.fromarray(np.uint8(np.round(np.clip(result,0,1)*255)))
