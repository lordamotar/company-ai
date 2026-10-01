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
    return text.strip() + "\n"
