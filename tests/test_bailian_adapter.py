from pathlib import Path
import pytest
from researchtwin_mcp.knowledge.bailian import BailianKnowledgeAdapter, BailianSyncError

class Resp:
    def __init__(self, status_code=200): self.status_code = status_code
class Client:
    def __init__(self, fail=None, statuses=("INIT", "PARSING", "PARSE_SUCCESS"), job_statuses=("RUNNING", "COMPLETED")):
        self.fail, self.statuses, self.jobs, self.calls = fail, iter(statuses), iter(job_statuses), []
    def _call(self, name):
        self.calls.append(name)
        if self.fail == name: raise RuntimeError("safe failure")
    def apply_file_upload_lease(self, category, workspace, req): self._call("lease"); return {"data":{"lease_id":"lease","file_upload_url":"https://example.invalid/mock-upload","headers":{"x-test":"1"}}}
    def add_file(self, workspace, req): self._call("add"); return {"data":{"file_id":"file"}}
    def describe_file(self, workspace, file, req): self._call("describe"); return {"data":{"status":next(self.statuses)}}
    def submit_index_add_documents_job(self, workspace, req): self._call("submit"); return {"data":{"job_id":"job"}}
    def get_index_job_status(self, workspace, req): self._call("job"); return {"data":{"status":next(self.jobs)}}
class HTTP:
    def __init__(self, status=200): self.status=status; self.seen=None
    def put(self, url, **kwargs): self.seen=(url,kwargs); return Resp(self.status)

def _adapter(tmp_path, client, http=None):
    p=tmp_path/"k.md"; p.write_bytes(b"content")
    env={"RESEARCHTWIN_BAILIAN_WORKSPACE_ID":"workspace","RESEARCHTWIN_BAILIAN_INDEX_ID":"index","RESEARCHTWIN_BAILIAN_CATEGORY_ID":"category"}
    return BailianKnowledgeAdapter(env=env, client=client, http_client=http or HTTP(), sleep=lambda _: None), p

def test_adapter_happy_path_order_and_parameters(tmp_path):
    client=Client(); http=HTTP(); adapter,p=_adapter(tmp_path,client,http)
    assert adapter.sync(p)=={"remote_workspace_id":"workspace","remote_index_id":"index","remote_file_id":"file","remote_job_id":"job"}
    assert client.calls==["lease","add","describe","describe","describe","submit","job","job"]
    assert http.seen[0].endswith("mock-upload") and http.seen[1]["headers"]=={"x-test":"1"} and http.seen[1]["content"]==b"content"

@pytest.mark.parametrize("fail", ["lease","add","describe","submit","job"])
def test_adapter_stage_failures_are_safe(tmp_path, fail):
    adapter,p=_adapter(tmp_path,Client(fail=fail))
    with pytest.raises(BailianSyncError): adapter.sync(p)

def test_adapter_put_failure(tmp_path):
    adapter,p=_adapter(tmp_path,Client(),HTTP(status=500))
    with pytest.raises(BailianSyncError, match="HTTP 500"): adapter.sync(p)

@pytest.mark.parametrize("mode", ["missing_lease", "missing_url", "none_response"])
def test_adapter_malformed_lease_responses_are_safe(tmp_path, mode):
    client=Client()
    if mode == "missing_lease": client.apply_file_upload_lease=lambda *a: {"data": {"file_upload_url": "https://example.invalid/mock-upload"}}
    elif mode == "missing_url": client.apply_file_upload_lease=lambda *a: {"data": {"lease_id": "lease"}}
    else: client.apply_file_upload_lease=lambda *a: None
    adapter,p=_adapter(tmp_path,client)
    with pytest.raises(BailianSyncError): adapter.sync(p)

def test_adapter_parse_and_job_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHTWIN_BAILIAN_MAX_WAIT", "0")
    adapter,p=_adapter(tmp_path,Client())
    with pytest.raises(BailianSyncError, match="timed out"): adapter.sync(p)
