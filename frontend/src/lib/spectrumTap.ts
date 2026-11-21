/**
 * Real-audio spectrum for an <audio> element, built so it CANNOT affect playback.
 *
 * Why captureStream and not createMediaElementSource
 * --------------------------------------------------
 * `AudioContext.createMediaElementSource(audio)` re-routes the element's output
 * through the Web Audio graph. If the media is cross-origin without CORS the
 * graph receives silence and the user hears NOTHING. Playback is the product,
 * so this module never does that.
 *
 * `audio.captureStream()` leaves the element's own output alone and hands us a
 * *copy* as a MediaStream. We read that copy with an AnalyserNode that is NOT
 * connected to `ctx.destination`, so nothing is ever played twice or silenced.
 * Worst case (tainted/cross-origin media, unsupported browser) the copy is
 * silent or absent; the caller then draws its fallback and playback is intact.
 *
 * Contract
 * --------
 *   const tap = createSpectrumTap(audio);   // null when the browser cannot do it
 *   tap?.read(out)                          // "live" | "silent" | "unavailable"
 *
 * "unavailable" means: draw the decorative fallback. It is returned when there
 * is no data path, and also when the stream has been flat for ~2 s of playback
 * (the signature of tainted media). A genuinely silent passage longer than that
 * flips to the fallback until the next `playing` event; that is an accepted trade.
 */

export type TapState = "live" | "silent" | "unavailable";

export interface SpectrumTapOptions {
  /** Power of two. 256 gives 128 bins, plenty for a 48-64 bar or wave display. */
  fftSize?: number;
  /** 0..1, higher = smoother. */
  smoothing?: number;
  /** Frames (reads) of pure zeros while playing before declaring "unavailable". */
  flatFramesBeforeGiveUp?: number;
}

export interface SpectrumTap {
  /**
   * Fill `out` (any length) with 0..255 magnitudes covering roughly 0-11 kHz,
   * where speech and music energy lives. Cheap: call once per animation frame.
   */
  read(out: Uint8Array): TapState;
  /** Resume the AudioContext. Safe to call repeatedly; call it from a user gesture. */
  resume(): void;
  /** Number of frequency bins the analyser exposes. */
  readonly bins: number;
  dispose(): void;
}

type AnyAudio = HTMLMediaElement & {
  captureStream?: () => MediaStream;
  mozCaptureStream?: () => MediaStream;
};

export function createSpectrumTap(
  audio: HTMLMediaElement,
  options: SpectrumTapOptions = {},
): SpectrumTap | null {
  if (typeof window === "undefined") return null;
  const Ctx: typeof AudioContext | undefined =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  const el = audio as AnyAudio;
  const capture = el.captureStream ?? el.mozCaptureStream;
  if (!Ctx || typeof capture !== "function") return null;

  const fftSize = options.fftSize ?? 256;
  const smoothing = options.smoothing ?? 0.78;
  const giveUpAfter = options.flatFramesBeforeGiveUp ?? 120;

  let ctx: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let scratch: Uint8Array<ArrayBuffer> = new Uint8Array(new ArrayBuffer(0));
  let flat = 0;
  let gaveUp = false;
  let disposed = false;

  function detach(): void {
    try {
      source?.disconnect();
    } catch {
      /* already disconnected */
    }
    source = null;
    analyser = null;
  }

  /** (Re)build the graph from a fresh capture. Called on every `playing`. */
  function attach(): void {
    if (disposed) return;
    detach();
    flat = 0;
    gaveUp = false;
    try {
      ctx ??= new Ctx!();
      const stream = (capture as () => MediaStream).call(audio);
      // MediaStreamAudioSourceNode binds to the first audio track at creation,
      // so a stream with no track yet (too early) is retried on the next event.
      if (!stream || stream.getAudioTracks().length === 0) return;
      const node = ctx.createAnalyser();
      node.fftSize = fftSize;
      node.smoothingTimeConstant = smoothing;
      source = ctx.createMediaStreamSource(stream);
      source.connect(node); // deliberately NOT connected to ctx.destination
      analyser = node;
      scratch = new Uint8Array(new ArrayBuffer(node.frequencyBinCount));
    } catch {
      detach();
    }
  }

  function resume(): Promise<void> {
    if (ctx && ctx.state === "suspended") return Promise.resolve(ctx.resume()).catch(() => undefined);
    return Promise.resolve();
  }

  function onPlaying(): void {
    attach();
    void resume();
  }

  audio.addEventListener("playing", onPlaying);
  // If the element is already playing when the tap is created, build now.
  if (!audio.paused && audio.readyState >= 2) attach();

  return {
    get bins() {
      return analyser?.frequencyBinCount ?? fftSize / 2;
    },

    read(out: Uint8Array): TapState {
      if (disposed || !analyser || gaveUp) {
        out.fill(0);
        return "unavailable";
      }
      analyser.getByteFrequencyData(scratch);

      // Use the lower half of the spectrum (~0-11 kHz at 44.1/48 kHz) and
      // max-pool it into out.length buckets.
      const usable = Math.max(1, Math.floor(scratch.length * 0.5));
      let energy = 0;
      for (let i = 0; i < out.length; i++) {
        const from = Math.floor((i * usable) / out.length);
        const to = Math.max(from + 1, Math.floor(((i + 1) * usable) / out.length));
        let peak = 0;
        for (let j = from; j < to; j++) {
          const v = scratch[j] ?? 0;
          if (v > peak) peak = v;
        }
        out[i] = peak;
        energy += peak;
      }

      if (energy > 0) {
        flat = 0;
        return "live";
      }
      if (!audio.paused && !audio.ended) {
        flat += 1;
        if (flat >= giveUpAfter) {
          gaveUp = true;
          return "unavailable";
        }
      }
      return "silent";
    },

    resume() {
      void resume();
    },

    dispose() {
      disposed = true;
      audio.removeEventListener("playing", onPlaying);
      detach();
      if (ctx && ctx.state !== "closed") void Promise.resolve(ctx.close()).catch(() => undefined);
      ctx = null;
    },
  };
}
