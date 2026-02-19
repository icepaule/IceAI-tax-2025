from datetime import date
from pathlib import Path
from typing import Dict, Optional

import requests

from config import SETTINGS


class EcoDMSClient:
    def __init__(self) -> None:
        configured = SETTINGS.ecodms_base_url.rstrip("/")
        # Accept both "...:8180" and "...:8180/api" in .env.
        self.base_url = configured[:-4] if configured.endswith("/api") else configured
        self.auth = (SETTINGS.ecodms_username, SETTINGS.ecodms_password)
        self.verify = not SETTINGS.ecodms_skip_tls_verify
        self.version_controlled = str(SETTINGS.ecodms_version_controlled).lower()
        self._limit_status = None

    def enabled(self) -> bool:
        return (
            SETTINGS.ecodms_auto_upload
            and bool(self.base_url)
            and bool(SETTINGS.ecodms_username)
            and bool(SETTINGS.ecodms_password)
        )

    def _url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        if not normalized.startswith("/api/"):
            normalized = f"/api{normalized}"
        return f"{self.base_url}{normalized}"

    def _upload(self, file_path: Path, upload_filename: Optional[str] = None) -> int:
        endpoint = self._url(f"/uploadFile/{self.version_controlled}")
        form_name = upload_filename or file_path.name
        with file_path.open("rb") as handle:
            files = {"file": (form_name, handle, "application/octet-stream")}
            response = requests.post(
                endpoint,
                auth=self.auth,
                files=files,
                timeout=120,
                verify=self.verify,
            )
        if response.status_code >= 300:
            snippet = response.text[:300].replace("\n", " ")
            raise RuntimeError(f"upload_failed:{response.status_code}:{snippet}")
        return int(response.json())

    def _check_api_limit(self) -> Optional[str]:
        if self._limit_status is not None:
            return self._limit_status
        try:
            response = requests.get(
                self._url("/apiStatistics"),
                auth=self.auth,
                timeout=15,
                verify=self.verify,
            )
            response.raise_for_status()
            data = response.json()
            upload_count = int(str(data.get("uploadCount", "0")))
            max_count = int(str(data.get("maxCount", "0")))
            if max_count > 0 and upload_count >= max_count:
                self._limit_status = f"api_limit_reached:{upload_count}/{max_count}"
            else:
                self._limit_status = None
        except Exception:
            self._limit_status = None
        return self._limit_status

    def _get_document_info(self, doc_id: int) -> Dict:
        response = requests.get(
            self._url(f"/documentInfo/{doc_id}"),
            auth=self.auth,
            timeout=30,
            verify=self.verify,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            raise RuntimeError(f"No documentInfo for docId={doc_id}")
        return rows[0]

    def _classify(self, payload: Dict) -> int:
        response = requests.post(
            self._url("/classifyDocument"),
            auth=self.auth,
            json=payload,
            timeout=30,
            verify=self.verify,
        )
        response.raise_for_status()
        return int(response.json())

    def upload_and_classify(self, file_path: Path, metadata: Dict[str, str]) -> str:
        if not self.enabled():
            return "disabled"
        limit = self._check_api_limit()
        if limit:
            return f"error:{limit}"

        try:
            doc_id = self._upload(file_path, metadata.get("upload_filename"))
            info = self._get_document_info(doc_id)

            attrs = dict(info.get("classifyAttributes", {}))
            attrs["mainfolder"] = metadata.get("ecodms_mainfolder_oid") or SETTINGS.ecodms_mainfolder_oid
            attrs["folder"] = metadata.get("ecodms_folder_oid") or SETTINGS.ecodms_default_folder_oid
            attrs["bemerkung"] = metadata.get("remark") or file_path.name

            invoice_date = metadata.get("invoice_date", "")
            attrs["cdate"] = invoice_date if invoice_date else date.today().isoformat()

            classify_payload = {
                "docId": info.get("docId"),
                "clDocId": info.get("clDocId"),
                "archiveName": info.get("archiveName", "1"),
                "classifyAttributes": attrs,
                "editRoles": info.get("editRoles", []),
                "readRoles": info.get("readRoles", []),
            }
            cl_doc_id = self._classify(classify_payload)
            return f"ok:doc={doc_id},cl={cl_doc_id}"
        except Exception as exc:
            return f"error:{exc}"

    def upload_stub(self, file_path: Path, metadata: Dict[str, str]) -> str:
        # Compatibility wrapper used by existing pipeline call site.
        return self.upload_and_classify(file_path, metadata)
