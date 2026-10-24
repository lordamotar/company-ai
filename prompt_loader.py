# -*- coding: utf-8 -*-
"""
prompt_loader.py — загрузка промптов из папки prompts/.

Промпты хранятся в .md-файлах, чтобы их можно было читать и править без кода.
При загрузке:
  * отбрасывается служебный HTML-комментарий в начале файла (<!-- ... -->);
  * {{CATEGORIES}} заменяется справочником категорий из call_categories.py.
"""

import os
import re

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def load_prompt(name):
    """Возвращает текст промпта prompts/<name>.md. Если файла нет — ошибка (работать без промпта нельзя)."""
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"\A\s*<!--.*?-->\s*", "", text, count=1, flags=re.S)
    if "{{CATEGORIES}}" in text:
        from call_categories import category_prompt_block
        text = text.replace("{{CATEGORIES}}", category_prompt_block().strip("\n"))
    if "{{LEAD_SCORING}}" in text:
        # Оценка клиента и следующий шаг: пустая строка, пока ENABLE_LEAD_SCORING не true (см. lead_scoring.py)
        from lead_scoring import prompt_block
        text = text.replace("{{LEAD_SCORING}}", prompt_block())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"
