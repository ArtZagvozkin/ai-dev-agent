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

    def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        timeout: tuple[int, int] = (10, 60),
    ) -> dict:
        logger.debug("Mattermost %s request: path=%s", method.upper(), path)

        url = f"{self.base_url}/api/v4{path}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._headers(),
                json=json,
                timeout=timeout,
            )
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Mattermost request failed: {e}")

        if response.status_code == 401:
            raise HTTPException(
                status_code=401,
                detail="Mattermost authentication failed. Check Mattermost bot token",
            )

        if response.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail="Mattermost access denied. Check bot permissions",
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

        if not response.text:
            return {}

        return response.json()

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def _post(self, path: str, json: dict | None = None) -> dict:
        return self._request("POST", path, json=json)

    def get_me(self) -> dict:
        return self._get("/users/me")

    def create_post(
        self,
        channel_id: str,
        message: str,
        root_id: str | None = None,
    ) -> dict:
        payload = {
            "channel_id": channel_id,
            "message": message,
        }

        if root_id:
            payload["root_id"] = root_id

        return self._post(
            "/posts",
            json=payload,
        )
