import os
import sys
import re
import json
import logging
import numpy as np
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# Сбрасываем унаследованные зеркала Hugging Face
os.environ.pop("HF_ENDPOINT", None)

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5433,
    "database": "company",
    "user": "company_user",
    "password": "company_password",
}

# Кэш моделей в памяти
_AUDIO_MODEL = None
_AUDIO_EXTRACTOR = None
_RUBERT_CLASSIFIER = None

def get_audio_model():
    global _AUDIO_MODEL, _AUDIO_EXTRACTOR
    if _AUDIO_MODEL is None or _AUDIO_EXTRACTOR is None:
        import torch
        from transformers import Wav2Vec2ForSequenceClassification, Wav2Vec2FeatureExtractor
        model_name = "superb/wav2vec2-base-superb-er"
        _AUDIO_EXTRACTOR = Wav2Vec2FeatureExtractor.from_pretrained(model_name)
        _AUDIO_MODEL = Wav2Vec2ForSequenceClassification.from_pretrained(model_name, use_safetensors=False)
    return _AUDIO_EXTRACTOR, _AUDIO_MODEL

def get_rubert_classifier():
    global _RUBERT_CLASSIFIER
    if _RUBERT_CLASSIFIER is None:
        from transformers import pipeline
        model_name = "blanchefort/rubert-base-cased-sentiment"
        _RUBERT_CLASSIFIER = pipeline("sentiment-analysis", model=model_name)
    return _RUBERT_CLASSIFIER


# ---------------------------------------------------------
# 1. Тон-войс 1: Физический акустический анализ (громкость, тишина, темп)
# ---------------------------------------------------------
def process_audio_features(call_id, file_path, cursor):
    try:
        import librosa
        
        y, sr = librosa.load(file_path, sr=None)
        duration = librosa.get_duration(y=y, sr=sr)
        
        rms = librosa.feature.rms(y=y)[0]
        mean_rms = float(np.mean(rms))
        max_rms = float(np.max(rms))
        
        loud_threshold = mean_rms * 2.5
        loud_frames = np.sum(rms > loud_threshold)
        loud_pct = float((loud_frames / len(rms)) * 100) if len(rms) > 0 else 0.0
        
        non_silent_intervals = librosa.effects.split(y, top_db=40)
        active_speech_sec = float(sum((end - start) for start, end in non_silent_intervals) / sr)
        silent_duration_sec = float(duration - active_speech_sec)
        silence_pct = float((silent_duration_sec / duration) * 100) if duration > 0 else 0.0
        
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
        speech_tempo = float(tempo[0]) if isinstance(tempo, (list, np.ndarray)) else float(tempo)
        
        query = """
            INSERT INTO call_audio_features (
                call_id, mean_rms, max_rms, loud_moments_pct, 
                silent_duration_sec, silence_pct, active_speech_sec, speech_tempo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (call_id) DO UPDATE SET
                mean_rms = EXCLUDED.mean_rms,
                max_rms = EXCLUDED.max_rms,
                loud_moments_pct = EXCLUDED.loud_moments_pct,
                silent_duration_sec = EXCLUDED.silent_duration_sec,
                silence_pct = EXCLUDED.silence_pct,
                active_speech_sec = EXCLUDED.active_speech_sec,
                speech_tempo = EXCLUDED.speech_tempo;
        """
        cursor.execute(query, (
            call_id, round(mean_rms, 4), round(max_rms, 4), round(loud_pct, 2),
            round(silent_duration_sec, 2), round(silence_pct, 2), round(active_speech_sec, 2), round(speech_tempo, 1)
        ))
        logging.info(f"[Тон 1] Акустический анализ сохранен для call_id={call_id}")
    except Exception as e:
        logging.error(f"[Тон 1] Ошибка при расчете акустических параметров: {e}", exc_info=True)


# ---------------------------------------------------------
# 2. Тон-войс 2 и 3: Эмоции по аудио и таймлайн (Wav2Vec2)
# ---------------------------------------------------------
def process_audio_emotions_and_segments(call_id, file_path, cursor):
    try:
        import torch
        import librosa
        
        feature_extractor, model = get_audio_model()
        speech, sr = librosa.load(file_path, sr=16000)
        
        id2label = model.config.id2label
        label_translation = {
            "neu": "Нейтральный",
            "ang": "Гнев",
            "sad": "Грусть",
            "hap": "Радость"
        }
        
        # 2. Посегментный анализ (10-секундные отрезки)
        cursor.execute("DELETE FROM call_audio_segments WHERE call_id = %s;", (call_id,))
        
        segment_duration = 10
        samples_per_segment = segment_duration * sr
        total_samples = len(speech)
        num_segments = int(np.ceil(total_samples / samples_per_segment))
        
        all_segment_scores = []
        
        for i in range(num_segments):
            start_sample = i * samples_per_segment
            end_sample = min((i + 1) * samples_per_segment, total_samples)
            
            if end_sample - start_sample < sr:
                continue
                
            segment = speech[start_sample:end_sample]
            seg_inputs = feature_extractor(segment, sampling_rate=16000, return_tensors="pt", padding=True)
            with torch.no_grad():
                seg_logits = model(**seg_inputs).logits
            seg_scores = torch.softmax(seg_logits, dim=-1).numpy()[0]
            all_segment_scores.append(seg_scores)
            
            seg_dominant_idx = int(np.argmax(seg_scores))
            seg_dominant_label = label_translation.get(id2label[seg_dominant_idx], id2label[seg_dominant_idx])
            seg_conf = float(seg_scores[seg_dominant_idx] * 100)
            
            start_sec = round(start_sample / sr, 1)
            end_sec = round(end_sample / sr, 1)
            
            cursor.execute("""
                INSERT INTO call_audio_segments (call_id, start_sec, end_sec, dominant_emotion, confidence)
                VALUES (%s, %s, %s, %s, %s);
            """, (call_id, start_sec, end_sec, seg_dominant_label, round(seg_conf, 2)))
            
        logging.info(f"[Тон 3] Посегментный таймлайн сохранен для call_id={call_id} ({len(all_segment_scores)} отрезков)")

        # 2.1 Общие эмоции как усреднение по всем сегментам
        if all_segment_scores:
            avg_scores = np.mean(all_segment_scores, axis=0)
            scores_dict = {}
            for i, score in enumerate(avg_scores):
                raw_label = id2label[i]
                ru_label = label_translation.get(raw_label, raw_label)
                scores_dict[ru_label] = float(score * 100)
                
            dominant_idx = int(np.argmax(avg_scores))
            dominant_emotion = label_translation.get(id2label[dominant_idx], id2label[dominant_idx])
            dominant_conf = float(avg_scores[dominant_idx] * 100)
            
            query_emotions = """
                INSERT INTO call_audio_emotions (
                    call_id, dominant_emotion, confidence, happy_score, neutral_score, sad_score, angry_score
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (call_id) DO UPDATE SET
                    dominant_emotion = EXCLUDED.dominant_emotion,
                    confidence = EXCLUDED.confidence,
                    happy_score = EXCLUDED.happy_score,
                    neutral_score = EXCLUDED.neutral_score,
                    sad_score = EXCLUDED.sad_score,
                    angry_score = EXCLUDED.angry_score;
            """
            cursor.execute(query_emotions, (
                call_id, dominant_emotion, round(dominant_conf, 2),
                round(scores_dict.get("Радость", 0.0), 2),
                round(scores_dict.get("Нейтральный", 0.0), 2),
                round(scores_dict.get("Грусть", 0.0), 2),
                round(scores_dict.get("Гнев", 0.0), 2)
            ))
            logging.info(f"[Тон 2] Эмоции по аудио сохранены для call_id={call_id}: {dominant_emotion} ({dominant_conf:.1f}%)")
    except Exception as e:
        logging.error(f"[Тон 2/3] Ошибка при анализе аудио-эмоций: {e}", exc_info=True)


# ---------------------------------------------------------
# 3. Тон-войс 4: Построчная тональность текста (RuBERT)
# ---------------------------------------------------------
def process_text_sentiment_lines(call_id, transcript_text, cursor):
    if not transcript_text:
        return
        
    try:
        classifier = get_rubert_classifier()
        cursor.execute("DELETE FROM call_text_sentiment_lines WHERE call_id = %s;", (call_id,))
        
        lines = [line.strip() for line in transcript_text.split("\n") if line.strip()]
        if len(lines) <= 1:
            # Если текст сохранен одним абзацем, разбиваем по знакам препинания
            sentences = [s.strip() for s in re.split(r'(?<=[.!?…])\s+', transcript_text) if len(s.strip()) > 3]
            if sentences:
                lines = sentences
                
        for line_idx, line in enumerate(lines, start=1):
            speaker = "Неизвестно"
            phrase = line
            
            # Распознаем говорящего (Менеджер / Клиент)
            if line.startswith("**Менеджер:**") or line.startswith("Менеджер:"):
                speaker = "Менеджер"
                phrase = re.sub(r"^\*{0,2}Менеджер:\*{0,2}\s*", "", line)
            elif line.startswith("**Клиент:**") or line.startswith("Клиент:"):
                speaker = "Клиент"
                phrase = re.sub(r"^\*{0,2}Клиент:\*{0,2}\s*", "", line)
                
            truncated_line = phrase[:500]
            result = classifier(truncated_line)[0]
            
            sentiment_map = {
                "POSITIVE": "ПОЗИТИВНЫЙ",
                "NEGATIVE": "НЕГАТИВНЫЙ",
                "NEUTRAL": "НЕЙТРАЛЬНЫЙ"
            }
            sentiment_label = sentiment_map.get(result['label'], result['label'])
            conf = float(result['score'] * 100)
            
            cursor.execute("""
                INSERT INTO call_text_sentiment_lines (call_id, line_number, speaker, phrase_text, sentiment_label, confidence)
                VALUES (%s, %s, %s, %s, %s, %s);
            """, (call_id, line_idx, speaker, phrase, sentiment_label, round(conf, 2)))
            
        logging.info(f"[Тон 4] Пофразовый анализ RuBERT сохранен для call_id={call_id} ({len(lines)} фраз)")
    except Exception as e:
        logging.error(f"[Тон 4] Ошибка при пофразовом анализе RuBERT: {e}", exc_info=True)


# ---------------------------------------------------------
# 4. Тон-войс 5: Комплексный смысловой LLM-анализ диалога
# ---------------------------------------------------------
def process_llm_sentiment(call_id, transcript_text, cursor):
    if not transcript_text:
        return

    gemini_key = os.environ.get("GEMINI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    
    prompt = f"""
    Проанализируй диалог между Менеджером и Клиентом и верни СТРОГО валидный JSON (без markdown блоков, без обратных кавычек ```json):
    {{
        "customer_sentiment_start": "нейтральный | позитивный | раздраженный",
        "customer_sentiment_end": "удовлетворенный | позитивный | нейтральный | недовольный",
        "satisfaction_score": 5,
        "manager_politeness": 5,
        "conflict_detected": false,
        "conflict_reason": "краткая причина или null если конфликта нет",
        "tone_summary": "краткое резюме настроения разговора в 1-2 предложениях"
    }}

    Диалог:
    {transcript_text[:4000]}
    """
    
    content = None
    
    # 1. Пробуем через Gemini API (Google)
    if gemini_key:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            content = response.text.strip()
        except Exception as e:
            logging.warning(f"[Тон 5] Не удалось выполнить через Gemini: {e}")
            
    # 2. Если не получилось, пробуем через OpenRouter
    if not content and openrouter_key:
        try:
            import requests
            headers = {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "qwen/qwen-2.5-72b-instruct", 
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2
            }
            res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logging.error(f"[Тон 5] Ошибка при вызове OpenRouter: {e}")
            
    if not content:
        logging.warning("[Тон 5] Не удалось получить ответ LLM для анализа настроения.")
        return
        
    try:
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
        else:
            data = json.loads(content)
            
        query = """
            INSERT INTO customer_sentiment (
                call_id, customer_sentiment_start, customer_sentiment_end,
                satisfaction_score, manager_politeness, conflict_detected,
                conflict_reason, tone_summary
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (call_id) DO UPDATE SET
                customer_sentiment_start = EXCLUDED.customer_sentiment_start,
                customer_sentiment_end = EXCLUDED.customer_sentiment_end,
                satisfaction_score = EXCLUDED.satisfaction_score,
                manager_politeness = EXCLUDED.manager_politeness,
                conflict_detected = EXCLUDED.conflict_detected,
                conflict_reason = EXCLUDED.conflict_reason,
                tone_summary = EXCLUDED.tone_summary;
        """
        cursor.execute(query, (
            call_id,
            data.get("customer_sentiment_start", "нейтральный"),
            data.get("customer_sentiment_end", "позитивный"),
            int(data.get("satisfaction_score", 5)),
            int(data.get("manager_politeness", 5)),
            bool(data.get("conflict_detected", False)),
            data.get("conflict_reason") if data.get("conflict_detected") else None,
            data.get("tone_summary", "Разговор прошел успешно.")
        ))
        logging.info(f"[Тон 5] Смысловой LLM-анализ сохранен для call_id={call_id}")
    except Exception as e:
        logging.error(f"[Тон 5] Ошибка при вызове LLM-анализа: {e}")


# ---------------------------------------------------------
# Главная функция запуска всех 5 уровней тон-войса
# ---------------------------------------------------------
def process_all_tone_voice(call_id, file_path, transcript_text):
    logging.info(f"=== Запуск полного анализа Tone of Voice для call_id={call_id} ===")
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    try:
        # 1. Акустика
        process_audio_features(call_id, file_path, cursor)
        conn.commit()
        
        # 2 и 3. Эмоции и посегментный таймлайн
        process_audio_emotions_and_segments(call_id, file_path, cursor)
        conn.commit()
        
        # 4. RuBERT построчно
        process_text_sentiment_lines(call_id, transcript_text, cursor)
        conn.commit()
        
        # 5. LLM-анализ
        process_llm_sentiment(call_id, transcript_text, cursor)
        conn.commit()
        
        logging.info(f"=== Все 5 уровней Tone of Voice успешно записаны в БД для call_id={call_id} ===")
    except Exception as e:
        conn.rollback()
        logging.error(f"Критическая ошибка при обработке Tone of Voice: {e}", exc_info=True)
        raise
    finally:
        cursor.close()
        conn.close()
