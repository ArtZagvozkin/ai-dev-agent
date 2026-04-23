from app.application.skills.code_review.schemas import TaskInfo


class ContextBuilder:
    def prompt_build(
        self,
        agent_context: str,
        task_data: TaskInfo,
        merge_request_data: dict,
    ) -> str:
        return (
            f"PROJECT CONTEXT:\n"
            f"{agent_context}\n\n"
            f"TASK:\n"
            f"ID: {task_data.id}\n"
            f"Type: {task_data.type}\n"
            f"Title: {task_data.title}\n"
            f"Description:\n{task_data.description}\n\n"
            f"MERGE REQUEST:\n"
            f"Title: {merge_request_data['title']}\n"
            f"Description:\n{merge_request_data['description']}\n\n"
            f"DIFF:\n"
            f"{merge_request_data['diff']}\n"
        )
