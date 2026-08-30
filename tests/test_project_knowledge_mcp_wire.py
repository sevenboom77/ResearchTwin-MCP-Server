import json
from pathlib import Path
import pytest, httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
from researchtwin_mcp.config import Settings
from researchtwin_mcp.server import create_server
from researchtwin_mcp.storage.json_store import JsonStore
from researchtwin_mcp.tools.candidate_intelligence import record_candidate_intelligence, update_candidate_status
from researchtwin_mcp.tools import project_knowledge

class FakeSDK:
    calls=[]
    def apply_file_upload_lease(self, category, workspace, request): self.calls.append("lease"); return {"data":{"lease_id":"lease","file_upload_url":"https://example.invalid/mock-upload","headers":{}}}
    def add_file(self, workspace, request): self.calls.append("add"); return {"data":{"file_id":"file"}}
    def describe_file(self, workspace, file_id, request): self.calls.append("describe"); return {"data":{"status":"PARSE_SUCCESS"}}
    def submit_index_add_documents_job(self, workspace, request): self.calls.append("submit"); return {"data":{"job_id":"job"}}
    def get_index_job_status(self, workspace, request): self.calls.append("job"); return {"data":{"status":"COMPLETED"}}
class FakeHTTP:
    def put(self, url, **kwargs): self.body=kwargs["content"]; return type("R",(),{"status_code":200})()

async def _wire(tmp_path, monkeypatch, calls):
    monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_ID", "x"); monkeypatch.setenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "y"); monkeypatch.setenv("RESEARCHTWIN_BAILIAN_WORKSPACE_ID", "workspace"); monkeypatch.setenv("RESEARCHTWIN_BAILIAN_INDEX_ID", "index")
    sdk=FakeSDK(); sdk.calls=calls; monkeypatch.setattr("researchtwin_mcp.knowledge.bailian.Client", lambda config: sdk); monkeypatch.setattr("researchtwin_mcp.knowledge.bailian.httpx2.Client", lambda **kwargs: FakeHTTP())
    server=create_server(Settings("127.0.0.1",8000,tmp_path,"WARNING")); app=server.streamable_http_app(streamable_http_path="/mcp",host="127.0.0.1",transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url="http://localhost")
    return server, app, client

def _promote(store):
    c=record_candidate_intelligence(store,title="wire lead",source_type="paper",summary="s",relevance_reason="r"); cid=str(c["candidate_id"])
    for s in ("shortlisted","validated","promoted"): update_candidate_status(store,candidate_id=cid,status=s)
    return cid

@pytest.mark.anyio
async def test_project_knowledge_prepare_sync_list_wire_and_idempotent(tmp_path: Path, monkeypatch):
    calls=[]; store=JsonStore(tmp_path); cid=_promote(store); _,app,http=await _wire(tmp_path,monkeypatch,calls)
    async with app.router.lifespan_context(app):
        async with http:
            async with streamable_http_client("http://localhost/mcp",http_client=http) as (r,w):
                async with ClientSession(r,w) as s:
                    await s.initialize(); prepared=await s.call_tool("prepare_project_knowledge",{"candidate_id":cid,"project_name":"ResearchTwin","title":"Wire knowledge","knowledge_type":"reference","knowledge_content":"Formal content"}); assert prepared.is_error is False; p=prepared.structured_content["knowledge"]; assert p["sync_status"]=="prepared"
                    synced=await s.call_tool("sync_project_knowledge_to_bailian",{"knowledge_id":p["knowledge_id"],"confirm_write":True}); assert synced.structured_content["knowledge"]["sync_status"]=="synced"; assert synced.structured_content["knowledge"]["remote_file_id"]=="file"
                    count=len(calls); again=await s.call_tool("sync_project_knowledge_to_bailian",{"knowledge_id":p["knowledge_id"],"confirm_write":True}); assert again.structured_content["knowledge"]["remote_job_id"]=="job" and len(calls)==count
                    listed=await s.call_tool("list_project_knowledge",{}); assert listed.structured_content["knowledge"][0]["knowledge_id"]==p["knowledge_id"]

@pytest.mark.anyio
async def test_project_knowledge_wire_confirmation_and_non_promoted_errors(tmp_path: Path, monkeypatch):
    store=JsonStore(tmp_path); c=record_candidate_intelligence(store,title="not promoted",source_type="paper",summary="s",relevance_reason="r"); _,app,http=await _wire(tmp_path,monkeypatch,[])
    async with app.router.lifespan_context(app):
        async with http:
            async with streamable_http_client("http://localhost/mcp",http_client=http) as (r,w):
                async with ClientSession(r,w) as s:
                    await s.initialize(); bad=await s.call_tool("prepare_project_knowledge",{"candidate_id":str(c["candidate_id"]),"project_name":"P","title":"K","knowledge_type":"note","knowledge_content":"C"}); assert bad.is_error is True and "candidate_not_promoted" in bad.content[0].text
                    unknown=await s.call_tool("sync_project_knowledge_to_bailian",{"knowledge_id":"missing","confirm_write":True}); assert unknown.is_error is True and "project_knowledge_not_found" in unknown.content[0].text

@pytest.mark.anyio
async def test_project_knowledge_wire_missing_config_and_tools_list(tmp_path: Path, monkeypatch):
    store=JsonStore(tmp_path); cid=_promote(store); _,app,http=await _wire(tmp_path,monkeypatch,[])
    for key in ("ALIBABA_CLOUD_ACCESS_KEY_ID","ALIBABA_CLOUD_ACCESS_KEY_SECRET","RESEARCHTWIN_BAILIAN_WORKSPACE_ID","RESEARCHTWIN_BAILIAN_INDEX_ID"): monkeypatch.delenv(key, raising=False)
    async with app.router.lifespan_context(app):
        async with http:
            async with streamable_http_client("http://localhost/mcp",http_client=http) as (r,w):
                async with ClientSession(r,w) as s:
                    await s.initialize(); tools=await s.list_tools(); assert len(tools.tools)==16
                    prepared=await s.call_tool("prepare_project_knowledge",{"candidate_id":cid,"project_name":"P","title":"K","knowledge_type":"note","knowledge_content":"C"}); kid=prepared.structured_content["knowledge"]["knowledge_id"]
                    bad=await s.call_tool("sync_project_knowledge_to_bailian",{"knowledge_id":kid,"confirm_write":True}); assert bad.is_error is True and "bailian_knowledge_config_missing" in bad.content[0].text
