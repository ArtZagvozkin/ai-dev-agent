import logging

import requests
from fastapi import HTTPException


logger = logging.getLogger(__name__)

class JiraClient:
    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.api_token = api_token

    def _get(self, path: str, params: dict | None = None) -> dict:
        logger.debug("Jira GET request: path=%s, params=%s", path, params)

        url = f"{self.base_url}/rest/api/3{path}"

        try:
            response = requests.get(
                url,
                auth=(self.email, self.api_token),
                headers={"Accept": "application/json"},
                params=params,
                timeout=(10, 60),
            )
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Jira request failed: {e}")

        if response.status_code == 401:
            raise HTTPException(
                status_code=401,
                detail="Jira authentication failed. Check JIRA_EMAIL/JIRA_API_TOKEN",
            )
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="Jira access denied")
        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Jira resource not found: {path}. Response: {response.text}",
            )
        if not response.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Jira API error: {response.status_code} {response.text}",
            )

        return response.json()

    def _description_to_text(self, description: dict | str | None) -> str:
        if description is None:
            return ""

        if isinstance(description, str):
            return description

        parts: list[str] = []

        def walk(node):
            if isinstance(node, dict):
                node_type = node.get("type")

                if node_type == "text":
                    parts.append(node.get("text", ""))

                if node_type == "hardBreak":
                    parts.append("\n")

                for child in node.get("content", []):
                    walk(child)

                if node_type in {
                    "paragraph",
                    "heading",
                    "bulletList",
                    "orderedList",
                    "listItem",
                }:
                    parts.append("\n")

            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(description)

        return "\n".join(
            line.rstrip()
            for line in "".join(parts).splitlines()
            if line.strip()
        )

    def _extract_reviewers(self, value: list[dict] | None) -> list[dict]:
        if not value:
            return []

        reviewers = []

        for user in value:
            reviewers.append(
                {
                    "account_id": user.get("accountId", ""),
                    "display_name": user.get("displayName", ""),
                    "email": user.get("emailAddress"),
                    "active": user.get("active", False),
                }
            )

        return reviewers

    def get_task(self, issue_key: str) -> dict:
        issue = self._get(
            f"/issue/{issue_key}",
            params={
                "fields": ",".join(
                    [
                        "summary",
                        "description",
                        "status",
                        "customfield_10040",
                        "customfield_10039",
                        "customfield_10041",
                    ]
                )
            },
        )

        fields = issue.get("fields") or {}

        task_type = fields.get("customfield_10041") or {}
        status = fields.get("status") or {}

        return {
            "id": issue.get("key", issue_key),
            "type": task_type.get("value", ""),
            "title": fields.get("summary", ""),
            "description": self._description_to_text(fields.get("description")),
            "status": status.get("name", ""),
            "mr_url": fields.get("customfield_10040"),
            "reviewers": self._extract_reviewers(fields.get("customfield_10039")),
        }
