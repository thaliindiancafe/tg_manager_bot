"""Wrap user-visible text so the agent sees telegram_user_id in private chats."""


def with_private_telegram_context(
    telegram_user_id: int,
    user_text: str,
    *,
    reply_task_id: str | None = None,
) -> str:
    if reply_task_id:
        mid = (
            f" Пользователь ответил **реплаем** (или указал UUID) на поручение; "
            f"task_id={reply_task_id}. Сначала assert_task_for_telegram_user с "
            f"telegram_user_id={telegram_user_id}. "
            "Если в notes есть чеклист — отчёт через **submit_task_proof** (не complete_task); "
            "при all_ok статус станет review. Закрыть задачу сотруднику нельзя — "
            "руководитель вызывает **approve_task** или **reject_task_proof**. "
            "Без чеклиста после отчёта — submit_task_proof или complete_task по смыслу."
        )
    else:
        mid = (
            " Если отчёт **без реплая** — get_open_tasks_for_telegram_user; при 2+ задачах "
            "бот мог показать кнопки выбора. UUID в тексте тоже привязывает task_id. "
            "Чеклист: submit_task_proof, не complete_task до review."
        )
    prefix = f"[Контекст: личный чат; telegram_user_id={telegram_user_id}.{mid}]\n\n"
    return prefix + (user_text or "")
