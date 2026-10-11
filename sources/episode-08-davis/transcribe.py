"""Transcribe a slice of a media file with faster-whisper.

Decoding goes through PyAV (``faster_whisper.decode_audio``), which is exactly
the code path that calls ``av.open(..., metadata_errors="ignore")`` and needs
PyAV >= 11.

Example:
    python transcribe.py --audio "C:\\Downloads\\下载.mp4" \
        --model ..\\models\\faster-whisper-large-v3 \
        --start 0 --duration 30 --out ..\\out\\clip-0-30
"""

import argparse
import json
import os
import sys
import time


def fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True, help="output prefix (no extension)")
    ap.add_argument("--start", type=float, default=0.0, help="clip start, seconds")
    ap.add_argument("--duration", type=float, default=30.0, help="clip length, seconds")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute-type", default="float16")
    ap.add_argument("--language", default=None)
    ap.add_argument("--beam-size", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--word-timestamps", action="store_true",
                    help="per-word/character timings in the JSON (negligible speed cost)")
    ap.add_argument("--vad-filter", action="store_true",
                    help="enable Silero VAD; no effect on clean narration, see out/param-matrix")
    args = ap.parse_args()

    import av
    import ctranslate2
    import faster_whisper
    from faster_whisper import WhisperModel, decode_audio

    print("python       :", sys.version.split()[0])
    print("faster-whisper:", faster_whisper.__version__)
    print("ctranslate2  :", ctranslate2.__version__)
    print("pyav         :", av.__version__)
    print("device       :", args.device, "| compute_type:", args.compute_type)
    print("cuda devices :", ctranslate2.get_cuda_device_count())

    t0 = time.time()
    audio = decode_audio(args.audio, sampling_rate=16000)
    decode_s = time.time() - t0
    total_s = len(audio) / 16000.0
    print(f"decoded      : {total_s:.2f} s of audio in {decode_s:.2f} s (PyAV)")

    start = max(0.0, args.start)
    stop = min(total_s, start + args.duration) if args.duration > 0 else total_s
    clip = audio[int(start * 16000): int(stop * 16000)]
    clip_s = len(clip) / 16000.0
    print(f"clip         : {start:.2f} s -> {stop:.2f} s ({clip_s:.2f} s, {len(clip)} samples)")

    t0 = time.time()
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    load_s = time.time() - t0
    print(f"model loaded : {load_s:.2f} s")

    t0 = time.time()
    segments, info = model.transcribe(
        clip,
        language=args.language,
        beam_size=args.beam_size,
        temperature=args.temperature,
        vad_filter=args.vad_filter,
        word_timestamps=args.word_timestamps,
        condition_on_previous_text=False,
    )
    segments = list(segments)
    infer_s = time.time() - t0

    print(f"language     : {info.language} (p={info.language_probability:.2f})")
    print(f"duration     : {info.duration:.2f} s (model view)")
    print(f"inference    : {infer_s:.2f} s -> RTF {infer_s / clip_s:.3f}x "
          f"({clip_s / infer_s:.1f}x realtime)")
    print("-" * 72)
    for s in segments:
        print(f"[{s.start:7.2f} -> {s.end:7.2f}] {s.text.strip()}")
    print("-" * 72)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    text = "".join(s.text for s in segments).strip()
    with open(args.out + ".txt", "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with open(args.out + ".srt", "w", encoding="utf-8") as fh:
        for i, s in enumerate(segments, 1):
            fh.write(f"{i}\n{fmt_ts(s.start)} --> {fmt_ts(s.end)}\n{s.text.strip()}\n\n")
    with open(args.out + ".json", "w", encoding="utf-8") as fh:
        json.dump(
            {
                "audio": args.audio,
                "model": args.model,
                "device": args.device,
                "compute_type": args.compute_type,
                "clip": {"start": start, "stop": stop, "seconds": clip_s},
                "source_seconds": total_s,
                "decode_seconds": decode_s,
                "model_load_seconds": load_s,
                "inference_seconds": infer_s,
                "rtf": infer_s / clip_s,
                "language": info.language,
                "language_probability": info.language_probability,
                "beam_size": args.beam_size,
                "temperature": args.temperature,
                "condition_on_previous_text": False,
                "vad_filter": args.vad_filter,
                "word_timestamps": args.word_timestamps,
                "text": text,
                "segments": [
                    {
                        "start": s.start,
                        "end": s.end,
                        "text": s.text.strip(),
                        **(
                            {"words": [
                                {"w": w.word, "start": w.start, "end": w.end}
                                for w in (s.words or [])
                            ]}
                            if args.word_timestamps
                            else {}
                        ),
                    }
                    for s in segments
                ],
            },
            fh,
            ensure_ascii=False,
            indent=2,
        )
    print("wrote        :", args.out + ".{txt,srt,json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
