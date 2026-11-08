# README preview

`readme-preview.png` shows the input recording beside its actual HTML export:

- Left: `examples/demo/source.mp4`, paused around 00:07 in Chromium.
- Right: a browser capture of `#f000004` and `#s000003` from `examples/demo/index.html`.

The export retains its default browser formatting and original transcript text.
Labels and layout are added around the captures for this README illustration.
The generator checks that the recording and exported HTML remain unchanged.

To recreate it from the repository root:

```sh
python -m pip install -e '.[pdf]'
python -m playwright install chromium
python scripts/make_readme_preview.py
```

The final image is saved here; intermediate files go to `work/readme-preview/`.
The example uses the existing controlled demo with synthesized narration.
