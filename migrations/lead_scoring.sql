-- Оценка клиента и следующий шаг (lead_scoring.py). Применять ТОЛЬКО при включении функции:
--   python apply_migrations.py migrations/lead_scoring.sql
-- Миграция идемпотентна и существующие данные не меняет.
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS lead_grade VARCHAR(16);
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS lead_grade_reason TEXT;
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS non_target_reason VARCHAR(64);
ALTER TABLE call_transcriptions ADD COLUMN IF NOT EXISTS next_step JSONB;
CREATE INDEX IF NOT EXISTS idx_call_transcriptions_lead_grade ON call_transcriptions(lead_grade);
