# PetDex-style Robot Companion

A tiny 32×32 pixel-art head animation package for websites.

## Included modes

- `sleeping` — closed eyes and gentle breathing motion
- `charging` — pulsing cyan eyes and inward-moving energy pixels
- `thinking` — moving pupils, a subtle head tilt, and a short blink

Every mode contains **8 consistent frames**.

## Quick preview

Open `index.html` through a local web server:

```bash
cd petdex_robot_companion
python3 -m http.server 8080
```

Then visit `http://localhost:8080`.

## Integration

Copy these files into the same public directory:

```text
styles.css
petdex.js
assets/
```

Add the sprite:

```html
<link rel="stylesheet" href="/petdex/styles.css">

<div id="coding-companion"></div>

<script type="module">
  import { PetDexCompanion } from "/petdex/petdex.js";

  const companion = new PetDexCompanion(
    document.querySelector("#coding-companion"),
    { mode: "thinking", scale: 3 }
  );

  companion.setMode("charging");
</script>
```

## Map application state to animation

```js
function updateCompanion(appState) {
  const animationMode = {
    waitingForModel: "thinking",
    processing: "charging",
    idleAtNight: "sleeping",
  }[appState] ?? "thinking";

  companion.setMode(animationMode);
}
```

## Assets

```text
assets/
├── robot-head.png
├── sleeping-sheet.png
├── charging-sheet.png
├── thinking-sheet.png
├── sleeping.png          # animated PNG
├── charging.png          # animated PNG
├── thinking.png          # animated PNG
├── *-preview.gif
└── frames/
    ├── sleeping/
    ├── charging/
    └── thinking/
```

The sprites use transparent backgrounds, hard pixel edges, a limited gray/cyan palette, and no anti-aliasing.

## CSS-only use

```html
<div class="petdex-companion" data-mode="thinking"></div>
```

Switch modes by changing `data-mode` to `sleeping`, `charging`, or `thinking`.

## Sizing

The source frames are 32×32. Set `--petdex-scale` to an integer to preserve sharp pixels:

```html
<div
  class="petdex-companion"
  data-mode="thinking"
  style="--petdex-scale: 5"
></div>
```