"""GUI integration: select all 50 lights without mutating a job or its PSD."""
from pathlib import Path
import sys,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tkinter as tk
import numpy as np
from PIL import Image,ImageGrab
from anime_layer_agent.viewer import PreviewWindow
from anime_layer_agent.storage import read_json,write_json,digest


def main():
    job=Path('work/normal_tones_calibrated').resolve()
    psd=read_json(job/'layers.json')['psd_path']
    before=(digest(psd),digest(job/'semantic_plan.json'))
    folder=Path('output/palette_light_feature_validation');folder.mkdir(exist_ok=True,parents=True)
    root=tk.Tk();root.withdraw();window=PreviewWindow(root,psd,job)
    started=time.perf_counter();state=dict(index=0,waiting=False);result=dict(passed=False,selected=[])
    ids=[1,50] if '--capture-only' in sys.argv else list(range(1,51))
    def finish(error=None):
        result.update(error=error,elapsed_seconds=time.perf_counter()-started,
            files_unchanged=before==(digest(psd),digest(job/'semantic_plan.json')))
        result['passed']=error is None and len(result['selected'])==len(ids) and result['files_unchanged']
        write_json(folder/('gui_capture_result.json' if '--capture-only' in sys.argv else 'gui_result.json'),result);window.close()
    def capture():
        try:
            box=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())
            ImageGrab.grab(bbox=box).save(folder/'light_picker_gui.png')
        except OSError as exc:result['screenshot_error']=str(exc)
        finish()
    def step():
        try:
            if time.perf_counter()-started>80:finish('Light preview timeout');return
            if not window.light_job or 'PSD' not in window.images:
                root.after(100,step);return
            if not state['waiting']:
                if state['index']==len(ids):finish();return
                window.light_picker.current(ids[state['index']]-1);window.choose_light()
                state['waiting']=True
            elif '照明' in window.images:
                preset=ids[state['index']];result['selected'].append(preset)
                if preset in (1,10,28,49,50):
                    window.images['照明'].save(folder/f'gui_light_{preset:02d}.png')
                if preset==50:
                    assert np.array_equal(np.asarray(window.images['照明'].convert('RGB')),
                        np.asarray(Image.open(job/'flat_preview.png').convert('RGB')))
                    root.deiconify();root.lift();root.attributes('-topmost',True)
                    root.update_idletasks();window.draw()
                    root.after(350,capture);return
                state['index']+=1;state['waiting']=False
            elif window.light_label.cget('text').startswith('照明を表示できません'):
                finish(window.light_label.cget('text'));return
        except Exception as exc:finish(str(exc));return
        root.after(100,step)
    root.after(100,step);root.mainloop()
    print(result);return 0 if result['passed'] else 1


if __name__=='__main__':sys.exit(main())
