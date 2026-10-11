"""Run only in the isolated inference venv. Official safetensors, no remote code."""
import argparse
import json
import os
from pathlib import Path
import time


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--image',required=True)
    p.add_argument('--output',required=True)
    args=p.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    os.environ['HF_HOME']=str(Path('.tmp/huggingface').resolve())
    os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING']='1'
    import torch
    import diffusers
    from huggingface_hub import model_info, hf_hub_download
    from PIL import Image
    import numpy as np
    model='prs-eth/marigold-normals-v1-1'
    info=model_info(model)
    revision=info.sha
    # Pin this run to the exact official repository revision.
    card=hf_hub_download(model,'README.md',revision=revision)
    (output/'MODEL_CARD.md').write_bytes(Path(card).read_bytes())
    if info.card_data.get('license')!='openrail++':
        raise ValueError('Unexpected model license; inspect before proceeding')
    # Model repository has no LICENSE file; retain its own license declaration
    # and link, rather than pretending a different repository is this license.
    (output/'MODEL_LICENSE.txt').write_text(
        'CreativeML Open RAIL++-M; declared openrail++ in pinned MODEL_CARD.md.\n'
        'https://huggingface.co/stabilityai/stable-diffusion-2/blob/main/LICENSE-MODEL\n'
        'The upstream license link requires access; no weights are redistributed.\n',encoding='utf8')
    torch.cuda.reset_peak_memory_stats()
    started=time.perf_counter()
    pipe=diffusers.MarigoldNormalsPipeline.from_pretrained(model,revision=revision,
        variant='fp16',torch_dtype=torch.float16,use_safetensors=True).to('cuda')
    load_seconds=time.perf_counter()-started
    image=Image.open(args.image).convert('RGB')
    timings=[]
    for trial in range(2):
        torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
        before=torch.cuda.mem_get_info()
        start=time.perf_counter()
        with torch.inference_mode():
            result=pipe(image,num_inference_steps=4,ensemble_size=1,processing_resolution=768,
                match_input_resolution=True,generator=torch.Generator('cuda').manual_seed(42))
        torch.cuda.synchronize()
        timings.append(dict(seconds=time.perf_counter()-start,
            peak_allocated_bytes=torch.cuda.max_memory_allocated(),
            peak_reserved_bytes=torch.cuda.max_memory_reserved(),free_before_bytes=before[0],
            free_after_bytes=torch.cuda.mem_get_info()[0]))
    normal=result.prediction[0]
    np.save(output/'normals.npy',normal)
    pipe.image_processor.visualize_normals(result.prediction)[0].save(output/'normals.png')
    record=dict(model=model,revision=revision,source='https://huggingface.co/'+model,
        license='CreativeML Open RAIL++-M',gpu=torch.cuda.get_device_name(0),
        torch=torch.__version__,diffusers=diffusers.__version__,image=str(Path(args.image).resolve()),
        size=list(image.size),prediction_shape=list(normal.shape),steps=4,ensemble=1,seed=42,
        processing_resolution=768,model_load_and_download_seconds=load_seconds,trials=timings,
        convention='X right, Y up, Z toward viewer; signed unit vectors',
        vram_note='PyTorch process peak; total device free memory also sampled, not whole-system peak')
    (output/'inference.json').write_text(json.dumps(record,indent=2),encoding='utf8')
    print(json.dumps(record,indent=2),flush=True)


if __name__=='__main__':main()
