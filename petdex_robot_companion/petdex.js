export class PetDexCompanion {
  static modes = new Set(["sleeping", "charging", "thinking"]);

  constructor(element, options = {}) {
    if (!(element instanceof HTMLElement)) {
      throw new TypeError("PetDexCompanion requires a valid HTMLElement.");
    }

    this.element = element;
    this.element.classList.add("petdex-companion");
    this.setScale(options.scale ?? 4);
    this.setMode(options.mode ?? "thinking");
  }

  setMode(mode) {
    if (!PetDexCompanion.modes.has(mode)) {
      throw new RangeError(
        `Unknown mode "${mode}". Use sleeping, charging, or thinking.`
      );
    }

    this.element.dataset.mode = mode;
    this.element.setAttribute("aria-label", `Robot companion: ${mode}`);
    return this;
  }

  setScale(scale) {
    const numericScale = Number(scale);

    if (!Number.isInteger(numericScale) || numericScale < 1 || numericScale > 12) {
      throw new RangeError("Scale must be an integer from 1 to 12.");
    }

    this.element.style.setProperty("--petdex-scale", numericScale);
    return this;
  }

  pause() {
    this.element.dataset.paused = "true";
    return this;
  }

  play() {
    this.element.dataset.paused = "false";
    return this;
  }
}