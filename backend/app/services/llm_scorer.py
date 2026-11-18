"""Optional Gemini LLM semantic highlight selection, titles, and hook texts."""

import copy
import json
import urllib.error
import urllib.request
from typing import Any

from backend.app.services.pipeline import slice_words_for_window


GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


def _call_gemini_json(prompt: str, api_key: str) -> Any:
    """Call Gemini Flash expecting a JSON array/object response."""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    url = f"{GEMINI_ENDPOINT}?key={api_key.strip()}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    text_out = (
        raw.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    return json.loads(text_out)


def select_highlights_with_llm(
    transcript: dict[str, Any],
    num_clips: int,
    target_duration: float,
    api_key: str | None = None,
) -> list[dict[str, Any]] | None:
    """Select non-overlapping viral/funny clip windows directly from transcript segments via Gemini."""
    if not api_key or not api_key.strip():
        return None
    segments = transcript.get("segments") or []
    duration = float(transcript.get("duration") or 0.0)
    if not segments or duration <= 5.0:
        return None

    compact_segments = [
        {
            "start": round(float(s["start"]), 2),
            "end": round(float(s["end"]), 2),
            "text": str(s.get("text", "")).strip(),
        }
        for s in segments[:400]
        if str(s.get("text", "")).strip()
    ]
    if not compact_segments:
        return None

    prompt = (
        "Kamu adalah editor klip viral TikTok & Reels (bahasa Indonesia/Inggris).\n"
        f"Durasi total video: {duration:.1f} detik. Bahasa: {transcript.get('language', 'auto')}.\n"
        f"Pilih tepat {num_clips} momen paling lucu, menegangkan, atau punya hook/punchline kuat "
        f"dengan durasi sekitar {target_duration:.0f} detik (minimal 15s, maksimal 60s) yang TIDAK saling tumpang tindih.\n"
        "Kembalikan HANYA JSON array of objects dengan key: start_time (float), end_time (float), "
        "score (float 0..1), title (maks 8 kata), hook_text (maks 6 kata kapital).\n"
        f"Segmen transkrip:\n{json.dumps(compact_segments, ensure_ascii=False)}"
    )

    try:
        parsed = _call_gemini_json(prompt, api_key)
        if not isinstance(parsed, list):
            return None

        accepted: list[dict[str, Any]] = []
        words = transcript.get("words") or []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            s = max(0.0, float(item.get("start_time", 0.0)))
            e = min(duration, float(item.get("end_time", 0.0)))
            if e - s < 5.0:
                continue
            if any(not (e <= prev["start_time"] or s >= prev["end_time"]) for prev in accepted):
                continue
            hook_words = str(item.get("hook_text") or "").strip().split()
            accepted.append({
                "start_time": round(s, 3),
                "end_time": round(e, 3),
                "score": round(min(1.0, max(0.0, float(item.get("score", 0.85)))), 4),
                "title": str(item.get("title") or "").strip(),
                "hook_text": " ".join(hook_words[:6]),
            })
            if len(accepted) >= num_clips:
                break

        if not accepted:
            return None

        accepted.sort(key=lambda c: c["start_time"])
        for idx, clip in enumerate(accepted, start=1):
            clip["clip_index"] = idx
            clip["words"] = slice_words_for_window(words, clip["start_time"], clip["end_time"])
            if not clip["title"]:
                snippet = " ".join(w["word"] for w in clip["words"][:6]).strip()
                clip["title"] = f"Clip #{idx}" + (f" — {snippet}..." if snippet else "")
        return accepted
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        return None


def enhance_highlights_with_llm(
    transcript: dict[str, Any],
    base_clips: list[dict[str, Any]],
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Refine clip titles and generate <=6 word hook_text using Gemini if api_key is set.

    Falls back gracefully to base_clips on empty API key or any network/parse error.
    """
    if not api_key or not api_key.strip() or not base_clips:
        return base_clips

    clips_summary = []
    for clip in base_clips:
        text_snippet = " ".join(w.get("word", "") for w in clip.get("words", [])).strip()
        clips_summary.append({
            "clip_index": clip["clip_index"],
            "start_time": clip["start_time"],
            "end_time": clip["end_time"],
            "transcript": text_snippet[:500],
        })

    prompt = (
        "Kamu adalah editor klip gaming/livestream TikTok & Reels (bahasa Indonesia/Inggris).\n"
        f"Bahasa video: {transcript.get('language', 'auto')}.\n"
        "Untuk setiap kandidat klip berikut, buatkan:\n"
        "1) title: judul singkat yang menarik (maks 8 kata)\n"
        "2) hook_text: teks hook singkat untuk banner atas video (MAKSIMAL 6 kata, kapital/punchy)\n"
        "Kembalikan HANYA JSON array of objects dengan key: clip_index, title, hook_text.\n"
        f"Data klip:\n{json.dumps(clips_summary, ensure_ascii=False)}"
    )

    try:
        parsed = _call_gemini_json(prompt, api_key)
        if not isinstance(parsed, list):
            return base_clips

        by_idx = {
            int(item["clip_index"]): item
            for item in parsed
            if isinstance(item, dict) and "clip_index" in item
        }
        enriched = copy.deepcopy(base_clips)
        for clip in enriched:
            update = by_idx.get(int(clip["clip_index"]))
            if not update:
                continue
            new_title = str(update.get("title") or "").strip()
            if new_title:
                clip["title"] = new_title
            hook_words = str(update.get("hook_text") or "").strip().split()
            if hook_words:
                clip["hook_text"] = " ".join(hook_words[:6])
        return enriched
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        return base_clips
