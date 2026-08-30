"""Official Alibaba Cloud Bailian knowledge-file sync adapter."""
from __future__ import annotations
import hashlib, os, time
from pathlib import Path
import httpx2
from alibabacloud_bailian20231229.client import Client
from alibabacloud_bailian20231229 import models
from alibabacloud_tea_openapi.models import Config

class BailianSyncError(RuntimeError): pass

def _value(obj, *names):
    for name in names:
        if isinstance(obj, dict) and name in obj: return obj[name]
        if hasattr(obj, name): return getattr(obj, name)
    return None

class BailianKnowledgeAdapter:
    def __init__(self, *, env=None, client=None, http_client=None, sleep=time.sleep):
        values = env or os.environ
        self.workspace_id = values.get("RESEARCHTWIN_BAILIAN_WORKSPACE_ID")
        self.index_id = values.get("RESEARCHTWIN_BAILIAN_INDEX_ID")
        self.category_id = values.get("RESEARCHTWIN_BAILIAN_CATEGORY_ID", "default")
        self.endpoint = values.get("RESEARCHTWIN_BAILIAN_ENDPOINT", "bailian.cn-beijing.aliyuncs.com")
        if client is not None: self.client = client
        else:
            self.client = Client(Config(access_key_id=values.get("ALIBABA_CLOUD_ACCESS_KEY_ID"), access_key_secret=values.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET"), endpoint=self.endpoint))
        self.http_client = http_client or httpx2.Client(timeout=20.0, follow_redirects=True)
        self.sleep = sleep

    def sync(self, artifact: Path) -> dict[str, str]:
        try:
            return self._sync_impl(artifact)
        except BailianSyncError:
            raise
        except Exception as exc:
            raise BailianSyncError("Bailian synchronization request failed safely.") from exc

    def _sync_impl(self, artifact: Path) -> dict[str, str]:
        data = artifact.read_bytes(); digest = hashlib.md5(data).hexdigest()
        lease = self.client.apply_file_upload_lease(self.category_id, self.workspace_id, models.ApplyFileUploadLeaseRequest(category_type="DATA_CENTER_FILE", file_name=artifact.name, md_5=digest, size_in_bytes=str(len(data))))
        lease_data = _value(_value(lease, "body"), "data") or _value(lease, "data")
        url = _value(lease_data, "file_upload_url", "upload_url", "url"); lease_id = _value(lease_data, "lease_id"); upload_headers = _value(lease_data, "headers", "upload_headers") or {}
        if not url or not lease_id: raise BailianSyncError("Bailian upload lease response was incomplete.")
        response = self.http_client.put(url, content=data, headers=upload_headers)
        if response.status_code >= 300: raise BailianSyncError(f"Bailian presigned upload returned HTTP {response.status_code}.")
        added = self.client.add_file(self.workspace_id, models.AddFileRequest(category_id=self.category_id, category_type="DATA_CENTER_FILE", lease_id=lease_id, parser="DASHSCOPE_DOCMIND"))
        added_data = _value(added, "data") or _value(_value(added, "body"), "data")
        file_id = _value(added_data, "file_id") or _value(added, "file_id")
        if not file_id: raise BailianSyncError("Bailian AddFile response was incomplete.")
        self._poll_file(file_id)
        job = self.client.submit_index_add_documents_job(self.workspace_id, models.SubmitIndexAddDocumentsJobRequest(index_id=self.index_id, document_ids=[file_id], source_type="DATA_CENTER_FILE"))
        job_data = _value(job, "data") or _value(_value(job, "body"), "data")
        job_id = _value(job_data, "job_id") or _value(job, "job_id")
        if not job_id: raise BailianSyncError("Bailian index job response was incomplete.")
        self._poll_job(job_id)
        return {"remote_workspace_id": self.workspace_id, "remote_index_id": self.index_id, "remote_file_id": str(file_id), "remote_job_id": str(job_id)}

    def _poll_file(self, file_id):
        deadline = time.monotonic() + float(os.getenv("RESEARCHTWIN_BAILIAN_MAX_WAIT", "60"))
        while time.monotonic() < deadline:
            response = self.client.describe_file(self.workspace_id, file_id, models.DescribeFileRequest()); data = _value(_value(response, "body"), "data") or _value(response, "data"); status = _value(data, "status", "file_status", "parse_status")
            if status == "PARSE_SUCCESS": return
            if status in {"PARSE_FAILED", "FAILED", "ERROR"}: raise BailianSyncError("Bailian file parsing failed.")
            self.sleep(float(os.getenv("RESEARCHTWIN_BAILIAN_POLL_INTERVAL", "1")))
        raise BailianSyncError("Bailian file parsing timed out.")

    def _poll_job(self, job_id):
        deadline = time.monotonic() + float(os.getenv("RESEARCHTWIN_BAILIAN_MAX_WAIT", "60"))
        while time.monotonic() < deadline:
            response = self.client.get_index_job_status(self.workspace_id, models.GetIndexJobStatusRequest(index_id=self.index_id, job_id=job_id)); data = _value(_value(response, "body"), "data") or _value(response, "data"); status = _value(data, "status", "job_status")
            if status == "COMPLETED": return
            if status in {"FAILED", "ERROR"}: raise BailianSyncError("Bailian index job failed.")
            self.sleep(float(os.getenv("RESEARCHTWIN_BAILIAN_POLL_INTERVAL", "1")))
        raise BailianSyncError("Bailian index job timed out.")
