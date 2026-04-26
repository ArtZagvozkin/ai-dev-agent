import logging

import requests
from fastapi import HTTPException


logger = logging.getLogger(__name__)


class MattermostClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, json: dict | None = None) -> dict:
        logger.debug("Mattermost POST request: path=%s", path)

        url = f"{self.base_url}/api/v4{path}"

        try:
            response = requests.post(
                url,
                headers=self._headers(),
                json=json,
                timeout=(10, 60),
            )
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Mattermost request failed: {e}")

        if response.status_code == 401:
            raise HTTPException(
                status_code=401,
                detail="Mattermost authentication failed. Check MATTERMOST_BOT_TOKEN",
            )

        if response.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail="Mattermost access denied. Check that bot can post to this channel",
            )

        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Mattermost resource not found: {path}. Response: {response.text}",
            )

        if not response.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Mattermost API error: {response.status_code} {response.text}",
            )

        return response.json()

    def create_post(self, channel_id: str, message: str) -> dict:
        return self._post(
            "/posts",
            json={
                "channel_id": channel_id,
                "message": message,
            },
        )
