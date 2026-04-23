import logging
from urllib.parse import quote

import requests
from fastapi import HTTPException


logger = logging.getLogger(__name__)

class GitLabClient:
    def __init__(self, base_url: str, token: str, project_id: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.project_id = project_id

    def _headers(self) -> dict:
        return {"PRIVATE-TOKEN": self.token}

    def _project_id_encoded(self) -> str:
        return quote(str(self.project_id), safe="")

    def _get(self, path: str, params: dict | None = None):
        logger.debug("GitLab GET request: path=%s, params=%s", path, params)

        url = f"{self.base_url}/api/v4{path}"

        try:
            response = requests.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=30,
            )
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"GitLab request failed: {e}")

        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="GitLab authentication failed. Check GITLAB_TOKEN")
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="GitLab access denied")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"GitLab resource not found: {path}")
        if not response.ok:
            raise HTTPException(
                status_code=502,
                detail=f"GitLab API error: {response.status_code} {response.text}",
            )

        return response

    def _post(self, path: str, data: dict | None = None):
        logger.debug("GitLab POST request: path=%s", path)

        url = f"{self.base_url}/api/v4{path}"

        try:
            response = requests.post(
                url,
                headers=self._headers(),
                data=data,
                timeout=30,
            )
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"GitLab request failed: {e}")

        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="GitLab authentication failed. Check GITLAB_TOKEN")
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="GitLab access denied")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"GitLab resource not found: {path}")
        if not response.ok:
            raise HTTPException(
                status_code=502,
                detail=f"GitLab API error: {response.status_code} {response.text}",
            )

        return response

    def create_merge_request_note(self, mr_iid: int, body: str) -> dict:
        path = f"/projects/{self._project_id_encoded()}/merge_requests/{mr_iid}/discussions"
        response = self._post(path, data={"body": body})
        discussion = response.json()

        notes = discussion.get("notes", [])
        first_note = notes[0] if notes else {}

        return {
            "id": str(discussion.get("id")),
            "body": first_note.get("body", body),
            "web_url": first_note.get("web_url"),
        }

    def get_raw_file(self, file_path: str, ref: str) -> str:
        encoded_file_path = quote(file_path, safe="")
        path = (
            f"/projects/{self._project_id_encoded()}/repository/files/"
            f"{encoded_file_path}/raw"
        )

        response = self._get(path, params={"ref": ref})
        return response.text

    def get_merge_request(self, mr_iid: int) -> dict:
        path = f"/projects/{self._project_id_encoded()}/merge_requests/{mr_iid}"
        response = self._get(path)
        return response.json()

    def get_merge_request_versions(self, mr_iid: int) -> list[dict]:
        path = f"/projects/{self._project_id_encoded()}/merge_requests/{mr_iid}/versions"
        response = self._get(path)
        return response.json()

    def get_latest_merge_request_version(self, mr_iid: int) -> dict:
        versions = self.get_merge_request_versions(mr_iid)
        if not versions:
            raise HTTPException(
                status_code=404,
                detail=f"No merge request versions found for MR IID {mr_iid}",
            )

        return versions[0]

    def get_merge_request_diffs(self, mr_iid: int) -> list[dict]:
        path = f"/projects/{self._project_id_encoded()}/merge_requests/{mr_iid}/diffs"

        page = 1
        per_page = 100
        all_diffs = []

        while True:
            response = self._get(
                path,
                params={
                    "page": page,
                    "per_page": per_page,
                },
            )

            chunk = response.json()
            if not chunk:
                break

            all_diffs.extend(chunk)

            next_page = response.headers.get("X-Next-Page")
            if not next_page:
                break

            page = int(next_page)

        return all_diffs

    def build_unified_diff_text(self, diffs: list[dict]) -> str:
        parts = []

        for item in diffs:
            old_path = item.get("old_path") or item.get("new_path") or "unknown"
            new_path = item.get("new_path") or item.get("old_path") or "unknown"
            diff_body = item.get("diff", "")

            header = (
                f"diff --git a/{old_path} b/{new_path}\n"
                f"--- a/{old_path}\n"
                f"+++ b/{new_path}\n"
            )

            parts.append(header + diff_body)

        return "\n".join(parts)

    def get_merge_request_data(self, mr_iid: int) -> dict:
        mr = self.get_merge_request(mr_iid)
        diffs = self.get_merge_request_diffs(mr_iid)
        diff_text = self.build_unified_diff_text(diffs)

        author = mr.get("author") or {}

        return {
            "id": mr.get("id"),
            "iid": mr.get("iid"),
            "title": mr.get("title", ""),
            "description": mr.get("description", ""),
            "author": {
                "id": author.get("id"),
                "username": author.get("username", ""),
                "name": author.get("name", ""),
                "web_url": author.get("web_url"),
            },
            "created_at": mr.get("created_at", ""),
            "source_branch": mr.get("source_branch", ""),
            "target_branch": mr.get("target_branch", ""),
            "diff": diff_text,
            "diffs": diffs,
        }

    def create_inline_comment(
        self,
        mr_iid: int,
        body: str,
        new_path: str,
        new_line: int,
    ) -> dict:
        mr = self.get_merge_request(mr_iid)
        version = self.get_latest_merge_request_version(mr_iid)

        old_path = new_path

        diffs = self.get_merge_request_diffs(mr_iid)
        for item in diffs:
            if item.get("new_path") == new_path:
                old_path = item.get("old_path") or new_path
                break

        path = f"/projects/{self._project_id_encoded()}/merge_requests/{mr_iid}/discussions"

        payload = {
            "body": body,
            "position[position_type]": "text",
            "position[base_sha]": version["base_commit_sha"],
            "position[start_sha]": version["start_commit_sha"],
            "position[head_sha]": version["head_commit_sha"],
            "position[old_path]": old_path,
            "position[new_path]": new_path,
            "position[new_line]": str(new_line),
        }

        response = self._post(path, data=payload)
        discussion = response.json()

        notes = discussion.get("notes", [])
        first_note = notes[0] if notes else {}

        return {
            "id": str(discussion.get("id")),
            "individual_note": discussion.get("individual_note", False),
            "notes_count": len(notes),
            "web_url": first_note.get("noteable_iid")
            and f"{mr.get('web_url')}#note_{first_note.get('id')}",
        }
