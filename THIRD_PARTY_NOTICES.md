# Third-party notices

PsdMaker itself is released under the Unlicense (see [LICENSE](LICENSE)). The files listed below contain portions
derived from third-party software. Those portions remain under their original license, whose notice is reproduced here.

## ComfyUI-MangaScreen

- Source: https://github.com/ashukae/ComfyUI-MangaScreen (commit `d0eb8dcc6c45274e3eee118ff0dabe05b56039b9`, version 0.2.1)
- License: MIT
- Used in:
  - `anime_layer_agent/manga_tone.py` — line/solid separation (`ink_layer`), descreened value map (`value_map`),
    flat tone plan (`tone_plan`), gradation (`region_spread`), white lines in beta (`white_cut`), the 45° AM screen
    (`screen_thresholds`, adapted from `print_screen`), Manga Levels (`manga_levels`) and the default parameters.
  - `anime_layer_agent/comfy_client.py` — the Qwen-Image 2.1 Turbo graph (8-step sigmas, node wiring) and the
    Manga Line Extraction step, following the `QWEN-Manga` workflow.
  - `anime_layer_agent/manga.py` — the redraw edit instruction (`REDRAW_PROMPT`).
  - `tests/test_manga.py` — `reference_print_screen`, kept verbatim as a test oracle.
- Modifications: torch and ComfyUI node wrappers removed; the screen is rendered from a per-pixel page threshold so
  all PSD layers share one screen; tone levels are measured per semantic part; the output is split into layers.

```
MIT License

Copyright (c) 2026 ashukae

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Models used through ComfyUI (Qwen-Image 2.1, Manga Line Extraction) are not bundled or redistributed.
Their own licenses apply to whoever downloads and runs them.
