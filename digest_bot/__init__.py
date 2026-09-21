"""AI/IT digest bot for a Telegram channel.

Пайплайн: fetch -> dedup -> LLM-черновик -> одобрение в админ-чате ->
публикация по слотам -> сбор метрик -> еженедельный отчёт.

Все внешние вызовы (Telegram Bot API, Anthropic API) изолированы в
telegram_api.py и llm.py, поэтому остальную логику можно тестировать
без сети — см. tests/.
"""

__version__ = "0.1.0"
