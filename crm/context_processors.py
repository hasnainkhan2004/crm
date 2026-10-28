from .services import get_task_attention_count


def task_counts(request):
    return {
        'overdue_task_count': get_task_attention_count(request.user),
    }
