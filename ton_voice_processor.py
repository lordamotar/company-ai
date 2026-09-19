import os
import sys
import time
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

        # Обновляем длительность в таблице транскрипций
        cursor.execute("UPDATE call_transcriptions SET duration_sec = %s WHERE id = %s;", (round(float(duration), 2), call_id))

        logging.info(f"[Тон 1] Акустический анализ и длительность ({duration:.1f} сек) сохранены для call_id={call_id}")
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
            clean_words = re.sub(r'[^\w\s]', '', phrase.lower()).strip().split()
            clean_str = " ".join(clean_words)

            # 1. Диалоговые междометия и короткие подтверждения (всегда нейтральны)
            NEUTRAL_INTERJECTIONS = {
                "угу", "ага", "да", "нет", "ок", "окей", "okay", "хорошо", "понятно", "ясно",
                "ладно", "слушаю", "алло", "так", "так-с", "так точно", "конечно", "верно",
                "минуту", "секунду", "пару минут", "липучка", "шипы", "размер", "цена", "алматы",
                "астана", "шымкент", "караганда", "зима", "лето", "шипованный", "с 10", "с десяти"
            }

            # Маркеры реального негатива/претензии/ругани
            NEGATIVE_KEYWORDS = [
                "ужас", "кошмар", "отврат", "брак", "обман", "мошенник", "претензи",
                "жалоб", "верните деньги", "хам", "груб", "суд", "некомпетент", "издева", "надоел",
                "безобрази", "отвратительн", "почему так долго", "вы с ума сошли", "идиотизм"
            ]
            has_explicit_neg = any(kw in clean_str for kw in NEGATIVE_KEYWORDS)

            # Вопросительные фразы поиска информации или уточнения связи/параметров
            is_info_query = (
                "?" in phrase or any(w in clean_str for w in [
                    "сколько", "цена", "стоимость", "когда", "какой", "какая", "какие", "где",
                    "наличие", "подскажите", "работает", "работаете", "слышно", "плохо слышно",
                    "нагрузк", "индекс", "баллон", "размер"
                ])
            ) and not has_explicit_neg

            is_short_interjection = (clean_str in NEUTRAL_INTERJECTIONS) or (len(clean_words) <= 3 and not has_explicit_neg)

            if is_short_interjection or is_info_query:
                sentiment_label = "НЕЙТРАЛЬНЫЙ"
                conf = 95.0
            else:
                result = classifier(truncated_line)[0]
                raw_label = result['label']
                raw_score = float(result['score'])

                # Без явных слов претензии/конфликта реплика не может быть признана негативной
                if raw_label == "NEGATIVE":
                    if has_explicit_neg:
                        sentiment_label = "НЕГАТИВНЫЙ"
                        conf = round(raw_score * 100, 2)
                    else:
                        sentiment_label = "НЕЙТРАЛЬНЫЙ"
                        conf = 85.0
                elif raw_label == "POSITIVE":
                    sentiment_label = "ПОЗИТИВНЫЙ"
                    conf = round(raw_score * 100, 2)
                else:
                    sentiment_label = "НЕЙТРАЛЬНЫЙ"
                    conf = round(raw_score * 100, 2)
            
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
# ---------------------------------------------------------
# Справочники для резервного извлечения данных (Fallback)
# ---------------------------------------------------------
FALLBACK_BRANDS = [
    "Continental", "Nokian", "Nordman", "Pirelli", "Michelin", "Bridgestone",
    "Cordiant", "Viatti", "Triangle", "Sailun", "Yokohama", "Goodyear",
    "Hankook", "Kormoran", "Maxxis", "Linglong", "Кама", "Dunlop", "Kumho",
    "Nexen", "Toyo", "Barum", "Matador", "Gislaved", "BFGoodrich", "Goodrich",
    "Skad", "Trebl", "K&K", "Replay"
]

FALLBACK_CAR_MODELS = [
    "Camry", "RAV4", "Corolla", "Prado", "Land Cruiser", "Highlander", "Avensis",
    "Accent", "Elantra", "Sonata", "Tucson", "Santa Fe", "Creta",
    "Rio", "Cerato", "K5", "Optima", "Sportage", "Sorento", "Seltos",
    "Cobalt", "Nexia", "Lacetti", "Gentra", "Malibu", "Tracker",
    "Polo", "Golf", "Passat", "Tiguan", "Touareg",
    "Duster", "Logan", "Sandero", "Kaptur",
    "Vesta", "Granta", "Largus", "Priora", "Niva",
    "Mazda 3", "Mazda 6", "CX-5", "Subaru", "Forester", "Outback"
]

FALLBACK_BRANCHES = [
    "Карагайлы", "Жандосова", "Рыскулова", "Райымбека", "Суюнбая",
    "Толе би", "Сейфуллина", "Саина", "Майлина", "Шаляпина",
    "Семей", "Павлодар", "Усть-Каменогорск", "Караганда", "Астана", "Шымкент", "Актобе"
]

def extract_entities_fallback(text):
    """Резервный алгоритм извлечения параметров шин, авто, клиента и сделки по регулярным выражениям."""
    if not text:
        return {}
        
    text_lower = text.lower()
    res = {}

    # 1. Бренд
    for b in FALLBACK_BRANDS:
        if b.lower() in text_lower:
            res["tire_brand"] = b
            break

    # 2. Типоразмер
    m_size = re.search(r'(\d{3}[\s/]\d{2}[\s/R]*\d{2})', text, re.I)
    if m_size:
        res["tire_size"] = m_size.group(1).replace(" ", "/").upper()
    else:
        m_r = re.search(r'R\s*(\d{2})', text, re.I)
        if m_r:
            res["tire_size"] = f"R{m_r.group(1)}"

    # 3. Марка авто
    for car in FALLBACK_CAR_MODELS:
        if car.lower() in text_lower:
            res["car_model"] = car
            break

    # 4. Сезонность
    if "шип" in text_lower or "шипован" in text_lower:
        res["tire_season"] = "Зимние шипованные"
    elif "липуч" in text_lower or "фрикцион" in text_lower:
        res["tire_season"] = "Зимние фрикционные (липучка)"
    elif "летн" in text_lower or "лето" in text_lower:
        res["tire_season"] = "Летние"
    elif "всесезон" in text_lower:
        res["tire_season"] = "Всесезонные"
    elif "зимн" in text_lower or "зима" in text_lower:
        res["tire_season"] = "Зимние"

    # 5. Количество
    m_cnt = re.search(r'(\d+)\s*(?:шт|штук|баллон|колес)', text_lower)
    if m_cnt:
        res["tire_count"] = f"{m_cnt.group(1)} шт."
    elif "комплект" in text_lower:
        res["tire_count"] = "4 шт. (комплект)"

    # 6. Филиал
    for br in FALLBACK_BRANCHES:
        if br.lower() in text_lower:
            res["branch_address"] = br
            break

    # 7. Оплата
    if "kaspi red" in text_lower or "каспи ред" in text_lower:
        res["payment_method"] = "Kaspi Red"
    elif "kaspi qr" in text_lower or "каспи qr" in text_lower or "каспи кюар" in text_lower:
        res["payment_method"] = "Kaspi QR"
    elif "рассрочк" in text_lower:
        res["payment_method"] = "Kaspi Рассрочка"
    elif "безнал" in text_lower or "счет на оплату" in text_lower:
        res["payment_method"] = "Безналичный расчет"
    elif "в филиале" in text_lower or "на кассе" in text_lower or "наличн" in text_lower:
        res["payment_method"] = "Оплата в филиале"

    # 8. Имя клиента
    m_client = re.search(r'(?:на\s+имя|клиент\s+зовут|меня\s+зовут)\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)', text, re.I)
    if m_client:
        c_name = m_client.group(1).strip()
        if c_name.lower() not in ["компания", "эйкос", "оператор"]:
            res["client_name"] = c_name

    # 9. Услуги и сервис
    if "шиномонтаж" in text_lower or "переобувк" in text_lower or "монтаж" in text_lower:
        res["service_booking"] = "Шиномонтаж"
    elif "хранен" in text_lower:
        res["service_booking"] = "Сезонное хранение"
    elif "балансировк" in text_lower:
        res["service_booking"] = "Балансировка"

    return res


# ---------------------------------------------------------
# 4. Тон-войс 5: Комплексный смысловой LLM-анализ диалога
# ---------------------------------------------------------
def process_llm_sentiment(call_id, transcript_text, cursor):
    if not transcript_text:
        return

    gemini_key = os.environ.get("GEMINI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    
    prompt = f"""
    Ты — ведущий AI-аналитик отдела контроля качества сети шинных центров Aikos (Эйкос).
    Внимательно проанализируй диалог между Менеджером и Клиентом и верни СТРОГО валидный JSON (без markdown блоков, без обратных кавычек ```json):
    {{
        "customer_sentiment_start": "нейтральный | позитивный | раздраженный",
        "customer_sentiment_end": "удовлетворенный | позитивный | нейтральный | недовольный",
        "satisfaction_score": 5,
        "manager_politeness": 5,
        "conflict_detected": false,
        "conflict_reason": "краткая причина или null если конфликта нет",
        "tone_summary": "краткое резюме настроения разговора в 1-2 предложениях",
        "manager_name": "имя менеджера если названо (например Жандос, Руслан) или null",
        "city_name": "город филиала если назван (например Алматы, Астана, Шымкент) или null",
        "client_name": "фамилия и имя клиента если названы (например 'Канапьянов Елдос', 'Марат') или null",
        "car_model": "марка и модель авто клиента если упоминались (например 'Toyota Camry', 'Hyundai Tucson', 'Cobalt') или null",
        "tire_brand": "бренд шин или дисков (например 'Cordiant', 'BFGoodrich', 'Nokian', 'Viatti', 'Triangle', 'Skad') или null",
        "tire_size": "типоразмер (например '215/70 R16', '205/65 R15', '185/65 R14') или null",
        "tire_season": "сезонность: 'Зимние шипованные' | 'Зимние фрикционные (липучка)' | 'Летние' | 'Всесезонные' или null",
        "tire_count": "количество шин/товаров (например '4 шт.', '2 шт.', 'комплект') или null",
        "branch_address": "конкретный филиал или склад самовывоза (например 'Карагайлы', 'Жандосова', 'Рыскулова', 'Семей') или null",
        "payment_method": "способ оплаты ('Kaspi QR' | 'Kaspi Red' | 'Рассрочка Kaspi' | 'Оплата в филиале' | 'Безналичный расчет') или null",
        "sale_status": "Продажа состоялась | Бронь оформлена | Консультация | Отказ от покупки",
        "order_sum": "Сумма сделки/брони в тенге с пробелом и символом ₸, например '160 000 ₸', '87 000 ₸'. Если продажи или брони не было, ставь '0 ₸'",
        "has_upsell": false,
        "upsell_sum": "Сумма допродажи с ₸, например '6 000 ₸', '98 200 ₸' или '0 ₸'",
        "upsell_text": "Что допродано (например: 'Комплексный шиномонтаж', 'Сезонное хранение шин', 'Диски Skad') или пустая строка",
        "service_booking": "Нет | Шиномонтаж | Сезонное хранение | Балансировка | Ремонт шин | Другое",
        "service_date": "Согласованная дата/время записи на сервис или самовывоз (например: '2020-10-16', 'Завтра', 'Согласовано') или пустая строка",
        "lost_reason": "Если сделки/брони не было — краткая причина отказа ('Сравнивает цены / думает', 'Нет в наличии на складе', 'Слишком дорого', 'Далеко ехать') или null",
        "call_summary": "Краткая суть разговора в 1-2 предложениях (цель обращения клиента и достигнутый результат)"
    }}

    ПРАВИЛА ОПРЕДЕЛЕНИЯ КОММЕРЧЕСКОГО РЕЗУЛЬТАТА И ФИКСАЦИИ:
    1. "Продажа состоялась": клиент подтвердил покупку, согласовал оплату, доставку или выписку счета.
    2. "Бронь оформлена": менеджер зафиксировал бронь/резерв шин или дисков, либо договорился о бронировании через WhatsApp/визите в филиал для оплаты и самовывоза (например: «поставлю на резерв», «забронировал», «отправлю на вацап, поставлю бронь, в филиале оплатите заберете», «приезжайте забирайте, бронь стоит»). Если клиент согласился на резерв или подтвердил связь в WhatsApp для выбора и фиксации заказа — это СТРОГО "Бронь оформлена".
    3. "Отказ от покупки": клиент явно отказался от покупки (дорого, нет в наличии и не согласен ждать, передумал).
    4. "Консультация": клиент только узнавал наличие, цены или адреса без договоренности о покупке или бронировании (order_sum = "0 ₸").
    5. РАСЧЕТ СУММЫ (order_sum): если названа цена за 1 шину (например, 38 750) и клиент берет комплект из 4 шт, рассчитай общую сумму: 38 750 * 4 = 155 000 ₸. Если сумма названа прямо — укажи её.
    6. ДОПРОДАЖА (has_upsell): true, если кроме основных шин клиент согласился/забронировал диски, услуги шиномонтажа со скидкой, сезонного хранения, датчики давления или расширенную гарантию. Укажи upsell_sum и upsell_text.
    7. ЗАПИСЬ НА СЕРВИС (service_booking): если клиент записался или договорился на шиномонтаж, хранение или другую услугу, укажи название услуги и дату (service_date). Если записи нет — service_booking = "Нет", service_date = "".
    8. conflict_detected = true ТОЛЬКО при реальной ссоре, ругани, хамстве, оскорблениях или официальных претензиях.
    9. Обычные рабочие моменты (товара нет в нужном филиале, подбор аналогов) НЕ являются конфликтом (conflict_detected = false).
    10. Если клиент вежливо завершил разговор и поблагодарил, satisfaction_score должен быть не ниже 4.

    Диалог:
    {transcript_text[:4000]}
    """
    
    content = None
    
    # 1. Пробуем через актуальный Gemini API (Google) с поддержкой пула ключей
    from worker import get_gemini_api_keys, get_current_gemini_key, rotate_gemini_key
    keys = get_gemini_api_keys()
    
    if keys:
        from google import genai
        max_attempts = max(1, len(keys))
        for attempt in range(max_attempts):
            curr_key = get_current_gemini_key()
            client = genai.Client(api_key=curr_key)
            is_quota_error = False
            
            for model_name in ["gemini-3.6-flash", "gemini-2.5-pro", "gemini-2.5-flash-preview"]:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt
                    )
                    if response and response.text:
                        content = response.text.strip()
                        break
                except Exception as e:
                    err_msg = str(e)
                    logging.warning(f"[Тон 5] Ошибка при вызове {model_name}: {err_msg[:120]}")
                    if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                        is_quota_error = True
                        break
                    time.sleep(1)

            if content:
                break
                
            if is_quota_error and len(keys) > 1 and attempt < max_attempts - 1:
                logging.info("[Тон 5] Смена ключа Gemini из-за исчерпания квоты (429)...")
                rotate_gemini_key()
                time.sleep(1)
            else:
                break
            
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
            
    # 3. Парсим ответ либо берем резервный fallback
    data = {}
    if content:
        try:
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
            else:
                data = json.loads(content)
        except Exception as err:
            logging.warning(f"[Тон 5] Не удалось распарсить JSON от LLM: {err}")

    # Дополняем недостающие поля через fallback эвристики
    fallback = extract_entities_fallback(transcript_text)
    for k, v in fallback.items():
        if not data.get(k):
            data[k] = v

    try:
        # 1. Сохранение Tone of Voice и CSAT в customer_sentiment
        query_sentiment = """
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
        cursor.execute(query_sentiment, (
            call_id,
            data.get("customer_sentiment_start", "нейтральный"),
            data.get("customer_sentiment_end", "позитивный"),
            int(data.get("satisfaction_score", 5)),
            int(data.get("manager_politeness", 5)),
            bool(data.get("conflict_detected", False)),
            data.get("conflict_reason") if data.get("conflict_detected") else None,
            data.get("tone_summary", data.get("call_summary", "Разговор прошел успешно."))
        ))

        # 2. Автоматическая фиксация сделки, допродажи, сервиса и всех параметров в call_transcriptions
        sale_status = data.get("sale_status", "Консультация")
        order_sum = str(data.get("order_sum", "0 ₸"))
        has_upsell = bool(data.get("has_upsell", False))
        upsell_sum = str(data.get("upsell_sum", "0 ₸"))
        upsell_text = str(data.get("upsell_text", ""))
        service_booking = str(data.get("service_booking", "Нет"))
        service_date = str(data.get("service_date", ""))
        mgr_name = data.get("manager_name")
        city_name = data.get("city_name")

        client_name = data.get("client_name")
        car_model = data.get("car_model")
        tire_brand = data.get("tire_brand")
        tire_size = data.get("tire_size")
        tire_season = data.get("tire_season")
        tire_count = data.get("tire_count")
        branch_address = data.get("branch_address")
        payment_method = data.get("payment_method")
        lost_reason = data.get("lost_reason")
        call_summary = data.get("call_summary") or data.get("tone_summary")

        query_commercial = """
            UPDATE call_transcriptions
            SET sale_status = %s,
                order_sum = %s,
                has_upsell = %s,
                upsell_sum = %s,
                upsell_text = %s,
                service_booking = %s,
                service_date = %s,
                client_name = COALESCE(%s, client_name),
                car_model = COALESCE(%s, car_model),
                tire_brand = COALESCE(%s, tire_brand),
                tire_size = COALESCE(%s, tire_size),
                tire_season = COALESCE(%s, tire_season),
                tire_count = COALESCE(%s, tire_count),
                branch_address = COALESCE(%s, branch_address),
                payment_method = COALESCE(%s, payment_method),
                lost_reason = COALESCE(%s, lost_reason),
                call_summary = COALESCE(%s, call_summary)
            WHERE id = %s;
        """
        cursor.execute(query_commercial, (
            sale_status,
            order_sum,
            has_upsell,
            upsell_sum,
            upsell_text,
            service_booking,
            service_date,
            client_name,
            car_model,
            tire_brand,
            tire_size,
            tire_season,
            tire_count,
            branch_address,
            payment_method,
            lost_reason,
            call_summary,
            call_id
        ))

        if mgr_name and mgr_name.strip():
            cursor.execute("""
                UPDATE call_transcriptions 
                SET manager_name = %s 
                WHERE id = %s AND (manager_name IS NULL OR manager_name = 'Оператор');
            """, (mgr_name.strip().capitalize(), call_id))

        if city_name and city_name.strip():
            cursor.execute("""
                UPDATE call_transcriptions 
                SET city_name = %s 
                WHERE id = %s AND (city_name IS NULL OR city_name = 'Алматы' OR city_name = '');
            """, (city_name.strip(), call_id))

        logging.info(
            f"[Тон 5 & Фиксация] call_id={call_id}: статус='{sale_status}', сумма={order_sum}, "
            f"допродажа={has_upsell} ({upsell_sum}, {upsell_text}), сервис='{service_booking}' ({service_date}), "
            f"бренд={tire_brand}, размер={tire_size}, клиент={client_name}, авто={car_model}"
        )
    except Exception as e:
        logging.error(f"[Тон 5] Ошибка при парсинге и сохранении LLM-анализа: {e}", exc_info=True)


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

        # 6. Автоматически обновляем html/calls_data.json для моментального отображения на веб-странице
        try:
            from export_db_to_json import export_calls_to_json
            export_calls_to_json()
        except Exception as err:
            logging.warning(f"Не удалось обновить html/calls_data.json: {err}")
    except Exception as e:
        conn.rollback()
        logging.error(f"Критическая ошибка при обработке Tone of Voice: {e}", exc_info=True)
        raise
    finally:
        cursor.close()
        conn.close()
