import React, { useState, useRef } from "react";
import { Upload as UploadIcon, Music, CheckCircle2, AlertCircle, Sparkles, FileAudio } from "lucide-react";
import { clipsAPI } from "../api/client";

interface UploadPageProps {
  onUploadSuccess: () => void;
}

export const UploadPage: React.FC<UploadPageProps> = ({ onUploadSuccess }) => {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState<string>("");
  const [category, setCategory] = useState<string>("comedy");
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [durationSec, setDurationSec] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successInfo, setSuccessInfo] = useState<{ clip_id: string; message: string } | null>(null);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (!selected) return;
    validateAndSetFile(selected);
  };

  const validateAndSetFile = (f: File) => {
    setErrorMessage(null);

    // 100 MB Limit check
    if (f.size > 100 * 1024 * 1024) {
      setErrorMessage("File exceeds the 100 MB limit.");
      return;
    }

    // Audio duration probe check
    const audio = new Audio();
    const objectUrl = URL.createObjectURL(f);
    audio.src = objectUrl;
    audio.onloadedmetadata = () => {
      const dur = Math.round(audio.duration);
      setDurationSec(dur);
      if (dur > 300) {
        setErrorMessage(`Audio duration (${dur}s) exceeds the maximum allowed 300 seconds (5 minutes).`);
      }
      URL.revokeObjectURL(objectUrl);
    };

    setFile(f);
    if (!title) {
      const cleanName = f.name.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " ");
      setTitle(cleanName);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const generateSampleClip = () => {
    const sampleRate = 22050;
    const dur = 15;
    const numSamples = sampleRate * dur;
    const buffer = new ArrayBuffer(44 + numSamples * 2);
    const view = new DataView(buffer);

    const writeString = (offset: number, str: string) => {
      for (let i = 0; i < str.length; i++) {
        view.setUint8(offset + i, str.charCodeAt(i));
      }
    };

    writeString(0, "RIFF");
    view.setUint32(4, 36 + numSamples * 2, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, numSamples * 2, true);

    for (let i = 0; i < numSamples; i++) {
      const t = i / sampleRate;
      const freq = 180 + 40 * Math.sin(2 * Math.PI * 0.5 * t);
      const val = Math.sin(2 * Math.PI * freq * t) * 0.4;
      const intVal = Math.max(-32768, Math.min(32767, Math.floor(val * 32767)));
      view.setInt16(44 + i * 2, intVal, true);
    }

    const blob = new Blob([buffer], { type: "audio/wav" });
    const sampleFile = new File([blob], "echoflow_voice_snippet.wav", { type: "audio/wav" });
    validateAndSetFile(sampleFile);
    setTitle("My EchoFlow Voice Roast");
    setCategory("comedy");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !title.trim()) return;

    if (durationSec && durationSec > 300) {
      setErrorMessage("Audio exceeds 300 seconds limit.");
      return;
    }

    setIsUploading(true);
    setErrorMessage(null);

    const formData = new FormData();
    formData.append("original_file", file);
    formData.append("title", title.trim());
    formData.append("category", category);

    try {
      const res = await clipsAPI.uploadClip(formData);
      setSuccessInfo(res);
      setTimeout(() => {
        onUploadSuccess();
      }, 2500);
    } catch (err: any) {
      setErrorMessage(
        err?.data?.original_file?.[0] ||
        err?.data?.title?.[0] ||
        err?.message ||
        "Upload failed. Please check file format."
      );
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="w-full max-w-2xl mx-auto px-4 md:px-8 py-6 pb-28 space-y-6">
      <div className="border-b border-white/10 pb-4">
        <h1 className="text-3xl md:text-4xl font-black uppercase tracking-tighter text-[#F5F5F5] flex items-center gap-3">
          <UploadIcon className="w-7 h-7 text-[#FF6321]" />
          Creator Studio
        </h1>
        <p className="text-xs font-mono uppercase text-white/40 mt-1">
          Ingest raw audio into the asynchronous Celery & HLS transcode pipeline
        </p>
      </div>

      {successInfo ? (
        <div className="p-8 rounded-3xl bg-[#111111] border border-white/15 text-center space-y-4 animate-in zoom-in-95 duration-200">
          <div className="w-16 h-16 rounded-2xl bg-[#FF6321]/20 text-[#FF6321] border border-[#FF6321]/30 flex items-center justify-center mx-auto">
            <CheckCircle2 className="w-8 h-8" />
          </div>
          <div className="space-y-1">
            <h2 className="text-xl font-black uppercase tracking-tight text-white">Ingestion Accepted (202)</h2>
            <p className="text-xs text-white/60">{successInfo.message}</p>
            <p className="text-[11px] text-[#FF6321] font-mono">CLIP_ID: {successInfo.clip_id}</p>
          </div>
          <div className="p-4 rounded-xl bg-black/60 border border-white/10 text-xs font-mono uppercase text-white/40 max-w-md mx-auto leading-relaxed">
            Worker task dispatched: Faster-Whisper transcribing, Librosa chroma vector extraction & 3-tier HLS packaging active...
          </div>
          <p className="text-xs font-mono uppercase text-[#FF6321] font-bold animate-pulse">
            Directing to live feed...
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-5">
          {errorMessage && (
            <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-mono flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{errorMessage}</span>
            </div>
          )}

          {/* Drag and Drop Zone */}
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`relative p-8 rounded-2xl border-2 border-dashed transition-all cursor-pointer flex flex-col items-center justify-center text-center space-y-3 ${
              file
                ? "bg-[#FF6321]/5 border-[#FF6321]"
                : "bg-[#111111] hover:bg-[#161616] border-white/15 hover:border-white/30"
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".mp3,.wav,.ogg,.flac,.m4a,.aac,.webm,.opus,audio/*"
              onChange={handleFileChange}
              className="hidden"
            />

            <div className="w-14 h-14 rounded-xl bg-white/10 flex items-center justify-center text-[#FF6321]">
              {file ? <FileAudio className="w-7 h-7 text-[#FF6321]" /> : <Music className="w-7 h-7" />}
            </div>

            {file ? (
              <div>
                <p className="text-sm font-black uppercase tracking-tight text-white">{file.name}</p>
                <p className="text-xs font-mono uppercase text-white/40 mt-1">
                  {(file.size / (1024 * 1024)).toFixed(2)} MB {durationSec ? `• ${durationSec}S DURATION` : ""}
                </p>
                <span className="inline-block text-[10px] font-mono uppercase text-[#FF6321] font-bold mt-2 underline">
                  Click to replace audio asset
                </span>
              </div>
            ) : (
              <div>
                <p className="text-sm font-black uppercase tracking-tight text-white">
                  Drop Audio File Here Or Click To Select
                </p>
                <p className="text-xs font-mono uppercase text-white/40 mt-1">
                  MP3, WAV, OGG, M4A, FLAC • MAX 100 MB • MAX 300S (5 MIN)
                </p>
              </div>
            )}
          </div>

          {/* Quick Demo Sample Button */}
          {!file && (
            <div className="flex justify-center">
              <button
                type="button"
                onClick={generateSampleClip}
                className="text-xs font-mono uppercase font-bold text-[#FF6321] hover:text-[#ff783d] flex items-center gap-1.5 p-2 rounded hover:bg-white/5 transition-colors"
              >
                <Sparkles className="w-3.5 h-3.5" />
                <span>Synthesize 15s Demo Voice Clip For Testing</span>
              </button>
            </div>
          )}

          {/* Title Input */}
          <div>
            <label className="text-xs font-black uppercase tracking-wider text-white/60 block mb-1.5 font-mono">
              Reel Title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g., THE FUTURE OF QUIET COMPUTING"
              maxLength={255}
              required
              className="w-full bg-[#111111] border border-white/15 rounded-xl px-4 py-3 text-sm font-bold text-white placeholder-white/20 focus:outline-none focus:border-[#FF6321] transition-colors"
            />
          </div>

          {/* Category Picker */}
          <div>
            <label className="text-xs font-black uppercase tracking-wider text-white/60 block mb-1.5 font-mono">
              Vector Category
            </label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full bg-[#111111] border border-white/15 rounded-xl px-4 py-3 text-sm font-bold uppercase text-white focus:outline-none focus:border-[#FF6321] transition-colors"
            >
              <option value="comedy">Comedy & Roasts</option>
              <option value="science">Science Bites</option>
              <option value="motivation">Daily Motivation</option>
              <option value="music">Lo-Fi & Beat Loops</option>
              <option value="quotes">Philosophy & Quotes</option>
              <option value="instrumental">Focus Waves</option>
            </select>
          </div>

          {/* Worker Pipeline Note */}
          <div className="p-4 rounded-xl bg-[#111111] border border-white/10 text-[10px] font-mono uppercase text-white/40 leading-relaxed">
            <span className="font-bold text-[#FF6321]">PIPELINE SPEC: </span>
            FFmpeg 3-tier ABR HLS transcoding • Whisper-v3 Large automatic speech recognition • Librosa 128-dim MFCC acoustic vectorization.
          </div>

          {/* Submit Button */}
          <button
            type="submit"
            disabled={!file || !title.trim() || isUploading}
            className="w-full py-4 rounded-xl bg-[#FF6321] text-black font-black text-sm uppercase tracking-widest shadow-[0_0_25px_rgba(255,99,33,0.3)] hover:bg-[#ff753b] active:scale-[0.99] transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {isUploading ? "Dispatching to Celery Queue..." : "Upload & Launch Reel"}
          </button>
        </form>
      )}
    </div>
  );
};
