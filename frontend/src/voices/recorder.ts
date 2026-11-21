import { MAX_VOICE_MS, PEAK_COUNT } from "./types";

export type VoiceRecorderErrorCode = "unsupported" | "denied" | "no-device" | "busy" | "failed";

export class VoiceRecorderError extends Error {
  constructor(public readonly code: VoiceRecorderErrorCode, message: string) {
    super(message);
    this.name = "VoiceRecorderError";
  }
}

export interface RecorderResult {
  blob: Blob;
  mimeType: string;
  durationMs: number;
  /** PEAK_COUNT values in 0..1 measured from the microphone. */
  peaks: number[];
}

export interface VoiceRecorderOptions {
  maxMs?: number;
  /** Called ~20 times a second while recording. level is 0..1 (smoothed RMS). */
  onLevel?: (level: number, elapsedMs: number) => void;
  /** Called when the time limit stops the recording. The caller still owns the result. */
  onAutoStop?: (result: RecorderResult) => void;
}

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

export function pickMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  return MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m)) ?? "";
}

/** Max-pool raw samples into `count` bars and scale so the loudest bar is 1. */
export function toPeaks(samples: number[], count: number = PEAK_COUNT): number[] {
  const out = new Array<number>(count).fill(0);
  if (samples.length === 0) return out.map(() => 0.08);
  for (let i = 0; i < count; i++) {
    const from = Math.floor((i * samples.length) / count);
    const to = Math.max(from + 1, Math.floor(((i + 1) * samples.length) / count));
    let peak = 0;
    for (let j = from; j < to && j < samples.length; j++) peak = Math.max(peak, samples[j] ?? 0);
    out[i] = peak;
  }
  const top = Math.max(...out, 0.0001);
  return out.map((v) => Math.max(0.08, v / top));
}

export class VoiceRecorder {
  static isSupported(): boolean {
    return (
      typeof navigator !== "undefined" &&
      !!navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function" &&
      typeof MediaRecorder !== "undefined"
    );
  }

  private readonly maxMs: number;
  private readonly opts: VoiceRecorderOptions;
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private ctx: AudioContext | null = null;
  private chunks: Blob[] = [];
  private samples: number[] = [];
  private startedAt = 0;
  private meterTimer: ReturnType<typeof setInterval> | null = null;
  private limitTimer: ReturnType<typeof setTimeout> | null = null;
  private level = 0;
  private finishing: Promise<RecorderResult> | null = null;

  constructor(opts: VoiceRecorderOptions = {}) {
    this.opts = opts;
    this.maxMs = opts.maxMs ?? MAX_VOICE_MS;
  }

  get recording(): boolean {
    return this.recorder?.state === "recording";
  }

  async start(): Promise<void> {
    if (!VoiceRecorder.isSupported()) {
      throw new VoiceRecorderError("unsupported", "This browser can't record audio. Try a current Chrome, Edge, Firefox or Safari over HTTPS.");
    }
    if (this.recorder) throw new VoiceRecorderError("busy", "A recording is already in progress.");

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (error) {
      const name = (error as { name?: string } | null)?.name;
      if (name === "NotAllowedError" || name === "SecurityError") {
        throw new VoiceRecorderError("denied", "Microphone access is blocked. Allow it in your browser's site settings, then try again.");
      }
      if (name === "NotFoundError" || name === "OverconstrainedError") {
        throw new VoiceRecorderError("no-device", "No microphone was found.");
      }
      throw new VoiceRecorderError("failed", "The microphone could not be started.");
    }

    const mimeType = pickMimeType();
    try {
      this.recorder = mimeType ? new MediaRecorder(this.stream, { mimeType }) : new MediaRecorder(this.stream);
    } catch {
      this.release();
      throw new VoiceRecorderError("unsupported", "This browser can't record in a supported audio format.");
    }

    this.chunks = [];
    this.samples = [];
    this.recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
    };

    // Level meter: an analyser on the mic stream. It is NOT connected to the
    // speakers, so there is no feedback loop.
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctx) {
      this.ctx = new Ctx();
      const analyser = this.ctx.createAnalyser();
      analyser.fftSize = 1024;
      this.ctx.createMediaStreamSource(this.stream).connect(analyser);
      const buf = new Uint8Array(new ArrayBuffer(analyser.fftSize));
      this.meterTimer = setInterval(() => {
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = ((buf[i] ?? 128) - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        // Fast attack, slower release, and a gain that makes quiet speech visible.
        const target = Math.min(1, rms * 4);
        this.level = target > this.level ? target : this.level * 0.85 + target * 0.15;
        this.samples.push(this.level);
        this.opts.onLevel?.(this.level, performance.now() - this.startedAt);
      }, 50);
    }

    this.startedAt = performance.now();
    this.recorder.start(250);
    this.limitTimer = setTimeout(() => {
      void this.stop().then((result) => this.opts.onAutoStop?.(result));
    }, this.maxMs);
  }

  /** Idempotent: calling stop twice returns the same result. */
  stop(): Promise<RecorderResult> {
    if (this.finishing) return this.finishing;
    const recorder = this.recorder;
    if (!recorder) return Promise.reject(new VoiceRecorderError("failed", "There is no recording to stop."));

    this.finishing = new Promise<RecorderResult>((resolve, reject) => {
      recorder.onstop = () => {
        const durationMs = Math.round(performance.now() - this.startedAt);
        const mimeType = recorder.mimeType || pickMimeType() || "audio/webm";
        const blob = new Blob(this.chunks, { type: mimeType });
        const peaks = toPeaks(this.samples);
        this.release();
        if (blob.size === 0) reject(new VoiceRecorderError("failed", "Nothing was recorded."));
        else resolve({ blob, mimeType, durationMs: Math.min(durationMs, this.maxMs), peaks });
      };
      recorder.onerror = () => {
        this.release();
        reject(new VoiceRecorderError("failed", "The recording failed."));
      };
      if (recorder.state !== "inactive") recorder.stop();
    });
    return this.finishing;
  }

  /** Discard everything and release the microphone. */
  cancel(): void {
    if (this.recorder) {
      this.recorder.ondataavailable = null;
      this.recorder.onstop = null;
      if (this.recorder.state !== "inactive") this.recorder.stop();
    }
    this.release();
    this.finishing = null;
  }

  private release(): void {
    if (this.meterTimer) clearInterval(this.meterTimer);
    if (this.limitTimer) clearTimeout(this.limitTimer);
    this.meterTimer = null;
    this.limitTimer = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    if (this.ctx && this.ctx.state !== "closed") void Promise.resolve(this.ctx.close()).catch(() => undefined);
    this.ctx = null;
    this.recorder = null;
  }
}
