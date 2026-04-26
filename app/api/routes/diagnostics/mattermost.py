from fastapi import APIRouter, Depends

from app.api.dependencies import get_mattermost_client
from app.infrastructure.mattermost.client import MattermostClient
from app.schemas.api import MattermostPostResponse, MattermostTestMessageRequest


router = APIRouter(
    prefix="/diagnostics/mattermost",
    tags=["diagnostics: mattermost"],
)


@router.post("/message", response_model=MattermostPostResponse)
def send_test_message(
    data: MattermostTestMessageRequest,
    mattermost: MattermostClient = Depends(get_mattermost_client),
):
    post = mattermost.create_post(
        channel_id=data.channel_id,
        message=data.message,
    )

    return {
        "id": post.get("id", ""),
        "channel_id": post.get("channel_id", data.channel_id),
        "message": post.get("message", data.message),
        "user_id": post.get("user_id"),
        "create_at": post.get("create_at"),
    }
