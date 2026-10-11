"""Second test: reuse exact fixture inputs, normals inferred from existing shading."""
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from anime_layer_agent.coloring import paint_flats, build_colored
from anime_layer_agent.normal_lighting import prepare_normal_lighting
from anime_layer_agent.storage import read_json,write_json


def main():
    for side,x in [('left',-.65),('right',.65)]:
        job=Path('work')/f'appearance_normals_{side}'
        if job.exists():raise ValueError('Comparison job already exists')
        job.mkdir(parents=True)
        for name in ('coloring_job.json','coloring_arrays.npz','source_palette.json','semantic_plan.json',
                     'lineart_binary.png','lineart_rgba.png','editing_lineart_white.png'):
            shutil.copy2(Path('work/normals_left')/name,job/name)
        plan=read_json(job/'semantic_plan.json');plan['lighting']=None
        write_json(job/'semantic_plan.json',plan)
        paint_flats(job)
        prepare_normal_lighting(job,'work/normal_input/appearance_normals/normals.npy',[x,.65,.55])
        plan=read_json(job/'semantic_plan.json')
        plan['lighting'].update(reviewed=True,notes='Official normal image opened: hair curvature and nose recovered from shaded input; grooves at ink and original shading may bias geometry. Exact fixed grid; no cast shadows.')
        write_json(job/'semantic_plan.json',plan)
        result=build_colored(job,Path('output')/job.name/'output.psd')
        print(result['job'],result['metrics'],result['build_elapsed_seconds'],flush=True)


if __name__=='__main__':main()
