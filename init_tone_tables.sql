-- ==========================================================
-- Миграция: Добавление 5 уровней Tone of Voice в PostgreSQL
-- База: company, Пользователь: company_user
-- ==========================================================

-- Проверяем наличие базовой таблицы транскрипций (если еще не создана)
CREATE TABLE IF NOT EXISTS call_transcriptions (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(32),
    call_date DATE,
    call_time TIME,
    unique_id VARCHAR(64) UNIQUE NOT NULL,
    unix_timestamp TIMESTAMPTZ,
    text TEXT,
    source_file VARCHAR(255),
    status VARCHAR(32) DEFAULT 'new',
    manager_name VARCHAR(64) DEFAULT 'Оператор',
    city_name VARCHAR(64) DEFAULT 'Алматы',
    duration_sec NUMERIC(8,2),
    sale_status VARCHAR(64) DEFAULT 'Консультация',
    order_sum VARCHAR(64) DEFAULT '0 ₸',
    has_upsell BOOLEAN DEFAULT FALSE,
    upsell_sum VARCHAR(64) DEFAULT '0 ₸',
    upsell_text VARCHAR(255),
    service_booking VARCHAR(64) DEFAULT 'Нет',
    service_date VARCHAR(64),
    client_name VARCHAR(128),
    car_model VARCHAR(128),
    tire_brand VARCHAR(128),
    tire_size VARCHAR(64),
    tire_season VARCHAR(64),
    tire_count VARCHAR(32),
    branch_address VARCHAR(255),
    payment_method VARCHAR(128),
    lost_reason TEXT,
    call_summary TEXT,
    call_category VARCHAR(64) DEFAULT 'Прочее / Консультация',
    call_subcategory VARCHAR(64) DEFAULT 'Общие вопросы',
    call_tags JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------
-- ТОН-ВОЙС 1: Физический акустический анализ звука
-- (громкость, пики/крики, тишина, паузы, темп речи)
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_audio_features (
    id SERIAL PRIMARY KEY,
    call_id INT UNIQUE NOT NULL REFERENCES call_transcriptions(id) ON DELETE CASCADE,
    mean_rms NUMERIC(6,4),             -- Средняя громкость
    max_rms NUMERIC(6,4),              -- Пиковая громкость
    loud_moments_pct NUMERIC(5,2),     -- Доля громких моментов/криков (%)
    silent_duration_sec NUMERIC(8,2),  -- Секунды тишины (пауз)
    silence_pct NUMERIC(5,2),          -- Процент тишины от длины звонка (%)
    active_speech_sec NUMERIC(8,2),    -- Секунды активной речи
    speech_tempo NUMERIC(6,1),         -- Темп речи (ударов в минуту)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------
-- ТОН-ВОЙС 2: Общие эмоции по аудио целиком (Wav2Vec2)
-- (определение эмоций по тембру, интонациям и дрожанию голоса)
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_audio_emotions (
    id SERIAL PRIMARY KEY,
    call_id INT UNIQUE NOT NULL REFERENCES call_transcriptions(id) ON DELETE CASCADE,
    dominant_emotion VARCHAR(32),      -- Преобладающая эмоция (Happy, Neutral, Sad, Angry)
    confidence NUMERIC(5,2),           -- Уверенность нейросети (%)
    happy_score NUMERIC(5,2),          -- Радость / Дружелюбие (%)
    neutral_score NUMERIC(5,2),        -- Нейтральный тон (%)
    sad_score NUMERIC(5,2),            -- Грусть (%)
    angry_score NUMERIC(5,2),          -- Раздражение / Гнев (%)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------
-- ТОН-ВОЙС 3: Посегментный таймлайн эмоций (Wav2Vec2)
-- (динамика изменения эмоций с шагом 10 секунд)
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_audio_segments (
    id SERIAL PRIMARY KEY,
    call_id INT NOT NULL REFERENCES call_transcriptions(id) ON DELETE CASCADE,
    start_sec NUMERIC(6,1),            -- Начало интервала (сек)
    end_sec NUMERIC(6,1),              -- Конец интервала (сек)
    dominant_emotion VARCHAR(32),      -- Эмоция на данном отрезке
    confidence NUMERIC(5,2),           -- Уверенность (%)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------
-- ТОН-ВОЙС 4: Построчная тональность фраз (RuBERT)
-- (оценка эмоционального окраса каждой отдельной фразы)
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS call_text_sentiment_lines (
    id SERIAL PRIMARY KEY,
    call_id INT NOT NULL REFERENCES call_transcriptions(id) ON DELETE CASCADE,
    line_number INT,                   -- Номер строки в диалоге
    speaker VARCHAR(32),               -- Менеджер / Клиент
    phrase_text TEXT,                  -- Текст реплики
    sentiment_label VARCHAR(32),       -- ПОЗИТИВНЫЙ, НЕГАТИВНЫЙ, НЕЙТРАЛЬНЫЙ
    confidence NUMERIC(5,2),           -- Уверенность модели (%)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------
-- ТОН-ВОЙС 5: Смысловой LLM-анализ настроения (Gemini / Qwen 2.5)
-- (оценки для РОПа и карточки Битрикс24)
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS customer_sentiment (
    id SERIAL PRIMARY KEY,
    call_id INT UNIQUE NOT NULL REFERENCES call_transcriptions(id) ON DELETE CASCADE,
    customer_sentiment_start VARCHAR(32), -- Настроение клиента в начале звонка
    customer_sentiment_end VARCHAR(32),   -- Настроение клиента в конце звонка
    satisfaction_score INT,              -- Оценка удовлетворенности клиента (1-5)
    manager_politeness INT,              -- Вежливость менеджера (1-5)
    conflict_detected BOOLEAN DEFAULT FALSE, -- Зафиксирован ли конфликт/претензия
    conflict_reason TEXT,                -- Причина недовольства клиента
    tone_summary TEXT,                   -- Краткий текстовый вывод ИИ
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индексы для быстрого поиска и построения отчетов
CREATE INDEX IF NOT EXISTS idx_audio_segments_call_id ON call_audio_segments(call_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_lines_call_id ON call_text_sentiment_lines(call_id);
CREATE INDEX IF NOT EXISTS idx_customer_sentiment_call_id ON customer_sentiment(call_id);

-- Обновление существующей таблицы call_transcriptions новыми полями классификации и очереди
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS call_category VARCHAR(64) DEFAULT 'Прочее / Консультация';
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS call_subcategory VARCHAR(64) DEFAULT 'Общие вопросы';
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS call_tags JSONB DEFAULT '[]';
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS retry_count INT DEFAULT 0;
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ;
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS checklist JSONB DEFAULT '[]';
