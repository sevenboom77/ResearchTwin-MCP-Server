from pathlib import Path
import pytest
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.candidate_intelligence import record_candidate_intelligence, update_candidate_status
from researchtwin_mcp.tools.project_knowledge import prepare_project_knowledge, sync_project_knowledge_to_bailian, list_project_knowledge

class FakeAdapter:
    def __init__(self, result=None, error=None): self.result, self.error, self.calls = result or {"remote_workspace_id":"w","remote_index_id":"i","remote_file_id":"f","remote_job_id":"j"}, error, 0
    def sync(self, path):
        self.calls += 1
        if self.error: raise self.error
        return self.result

def _promoted(store, title="Lead"):
    c = record_candidate_intelligence(store, title=title, source_type="paper", summary="summary", relevance_reason="reason")
    cid = str(c["candidate_id"])
    for status in ("shortlisted", "validated", "promoted"):
        assert update_candidate_status(store, candidate_id=cid, status=status)["status"] == "success"
    return cid

def test_prepare_requires_promoted_and_writes_provenance(tmp_path: Path):
    store = JsonStore(tmp_path); c = record_candidate_intelligence(store, title="Lead", source_type="paper", summary="summary", relevance_reason="reason")
    assert prepare_project_knowledge(store, candidate_id=str(c["candidate_id"]), project_name="P", title="K", knowledge_type="note", knowledge_content="Formal content")["error_code"] == "candidate_not_promoted"
    cid = str(c["candidate_id"])
    for status in ("shortlisted", "validated", "promoted"):
        assert update_candidate_status(store, candidate_id=cid, status=status)["status"] == "success"
    result = prepare_project_knowledge(store, candidate_id=cid, project_name="P", title="K", knowledge_type="note", knowledge_content="Formal content")
    assert result["status"] == "success"; artifact = tmp_path / result["knowledge"]["local_artifact_path"]; assert artifact.is_file(); assert "candidate_id: " + cid in artifact.read_text(encoding="utf-8")
    assert prepare_project_knowledge(store, candidate_id=cid, project_name="P", title="K2", knowledge_type="note", knowledge_content="Other")["error_code"] == "duplicate_project_knowledge"

def test_sync_confirmation_failure_and_mocked_success_are_persistent(tmp_path: Path, monkeypatch):
    store = JsonStore(tmp_path); cid = _promoted(store); prepared = prepare_project_knowledge(store, candidate_id=cid, project_name="P", title="K", knowledge_type="reference", knowledge_content="Content"); kid = str(prepared["knowledge"]["knowledge_id"])
    assert sync_project_knowledge_to_bailian(store, knowledge_id=kid, confirm_write=False)["error_code"] == "explicit_confirmation_required"
    env = {"ALIBABA_CLOUD_ACCESS_KEY_ID":"x", "ALIBABA_CLOUD_ACCESS_KEY_SECRET":"y", "RESEARCHTWIN_BAILIAN_WORKSPACE_ID":"w", "RESEARCHTWIN_BAILIAN_INDEX_ID":"i"}
    for key, value in env.items(): monkeypatch.setenv(key, value)
    adapter = FakeAdapter(); synced = sync_project_knowledge_to_bailian(store, knowledge_id=kid, confirm_write=True, adapter=adapter)
    assert synced["status"] == "success" and synced["knowledge"]["sync_status"] == "synced" and adapter.calls == 1
    again = sync_project_knowledge_to_bailian(store, knowledge_id=kid, confirm_write=True, adapter=adapter); assert again["knowledge"]["remote_job_id"] == "j" and adapter.calls == 1
    assert list_project_knowledge(JsonStore(tmp_path), include_content=False)["knowledge"][0]["knowledge_content"] == "Content"

@pytest.mark.parametrize("status", ["discovered", "shortlisted", "validated", "rejected"])
def test_prepare_rejects_non_promoted_statuses(tmp_path: Path, status):
    store=JsonStore(tmp_path); c=record_candidate_intelligence(store,title="Lead"+status,source_type="paper",summary="s",relevance_reason="r"); cid=str(c["candidate_id"])
    for next_status in ({"shortlisted":["shortlisted"],"validated":["shortlisted","validated"],"rejected":["rejected"],"discovered":[]}[status]): update_candidate_status(store,candidate_id=cid,status=next_status)
    result=prepare_project_knowledge(store,candidate_id=cid,project_name="P",title="K",knowledge_type="note",knowledge_content="C")
    assert result["error_code"] == "candidate_not_promoted"
