/**
 * The reel's sound wave: one smooth, mirrored ribbon, drawn flat in the accent
 * colour. No glow, no gradient (the design system forbids both); depth comes
 * from a quiet fill under a crisp outline and a hairline centre axis.
 *
 * Pure drawing: give it a 2D context, a size and 0..1 magnitudes. It owns no
 * timers and reads no React state, so the caller decides when to redraw.
 */
export interface WaveStyle {
  /** CSS colour for outline + fill, resolved from --color-accent by the caller. */
  accent: string;
  /** CSS colour for the centre axis, resolved from --color-fg-subtle. */
  axis: string;
  /** Outline thickness in CSS px. */
  stroke?: number;
  /** 0..1 height of the loudest peak relative to half the canvas. */
  headroom?: number;
}

/** Fast attack, slow release, per bar. Mutates `state` and returns it. */
export function smoothBars(target: ArrayLike<number>, state: Float32Array, release = 0.88): Float32Array {
  for (let i = 0; i < state.length; i++) {
    const t = (target[i] ?? 0) / 255;
    const cur = state[i] ?? 0;
    state[i] = t > cur ? cur + (t - cur) * 0.55 : cur * release + t * (1 - release);
  }
  return state;
}

/** Deterministic 0..1 values for a clip with no recording of its energy: a "cover" for the wave. */
export function seededBars(seed: string, count: number): Float32Array {
  let h = 1779033703 ^ seed.length;
  for (let i = 0; i < seed.length; i++) {
    h = Math.imul(h ^ seed.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  const rnd = () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
  const f1 = 2 + rnd() * 3, f2 = 5 + rnd() * 6, p1 = rnd() * 6.28, p2 = rnd() * 6.28;
  const out = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const x = i / (count - 1);
    const envelope = 0.35 + 0.65 * Math.sin(Math.PI * Math.min(1, x * 1.05)) ** 0.8; // quiet at both ends
    const body = 0.5 + 0.28 * Math.sin(x * f1 * 6.28 + p1) + 0.17 * Math.sin(x * f2 * 6.28 + p2);
    out[i] = Math.max(0.06, Math.min(1, envelope * body * (0.8 + 0.4 * rnd())));
  }
  return out;
}

/** Draw `values` (0..1, left = low frequency) as a mirrored ribbon filling width x height. */
export function drawWave(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  values: ArrayLike<number>,
  style: WaveStyle,
): void {
  const n = values.length;
  ctx.clearRect(0, 0, width, height);
  if (n < 2) return;
  const mid = height / 2;
  const amp = (height / 2) * (style.headroom ?? 0.82);
  const step = width / (n - 1);

  // Hairline centre axis
  ctx.strokeStyle = style.axis;
  ctx.globalAlpha = 0.5;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, Math.round(mid) + 0.5);
  ctx.lineTo(width, Math.round(mid) + 0.5);
  ctx.stroke();
  ctx.globalAlpha = 1;

  const pts: Array<[number, number]> = [];
  for (let i = 0; i < n; i++) pts.push([i * step, Math.max(1.5, (values[i] ?? 0) * amp)]);

  const trace = (sign: 1 | -1, reverse = false) => {
    const seq = reverse ? [...pts].reverse() : pts;
    const first = seq[0]!;
    if (!reverse) ctx.moveTo(first[0], mid + sign * first[1]);
    else ctx.lineTo(first[0], mid + sign * first[1]);
    for (let i = 1; i < seq.length - 1; i++) {
      const [x0, y0] = seq[i]!;
      const [x1, y1] = seq[i + 1]!;
      ctx.quadraticCurveTo(x0, mid + sign * y0, (x0 + x1) / 2, mid + sign * ((y0 + y1) / 2));
    }
    const last = seq[seq.length - 1]!;
    ctx.lineTo(last[0], mid + sign * last[1]);
  };

  // Fill between the upper and lower edge
  ctx.beginPath();
  trace(-1);
  trace(1, true);
  ctx.closePath();
  ctx.fillStyle = style.accent;
  ctx.globalAlpha = 0.16;
  ctx.fill();
  ctx.globalAlpha = 1;

  // Crisp outline, top and bottom
  ctx.strokeStyle = style.accent;
  ctx.lineWidth = style.stroke ?? 2;
  ctx.lineJoin = "round";
  for (const sign of [-1, 1] as const) {
    ctx.beginPath();
    trace(sign);
    ctx.stroke();
  }
}
