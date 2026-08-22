"""Provider-neutral, offline-first memory data planes.

Only content/query/scope cross this boundary; evaluation labels are not an ABI.
"""
from __future__ import annotations
from dataclasses import dataclass
import importlib.metadata
from pathlib import Path
import re
from typing import Any, Mapping, Protocol
from cmd_audit.core.state_codec import append_jsonl_fsync, canonical_json, content_sha256, require_closed_mapping

@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    content: str
    source_hash: str
    scope: str

class MemoryDataPlane(Protocol):
    backend_name: str
    backend_version: str
    def add(self, *, content: str, scope: str) -> MemoryRecord: ...
    def search(self, *, query: str, scope: str, limit: int) -> tuple[MemoryRecord, ...]: ...
    def root(self, scope: str) -> str: ...

class AuditedInMemoryDataPlane:
    backend_name = "in-memory"
    backend_version = "deterministic-v1"
    def __init__(self, audit_path: Path, *, sync_each_event: bool = True) -> None:
        self.audit_path=Path(audit_path); self.rows: dict[str,list[MemoryRecord]]={}; self.head="0"*64; self.index=0
        self.sync_each_event=sync_each_event; self.events: list[dict[str,object]]=[]; self.roots: dict[str,str]={}
    def _audit(self, operation: str, payload: Mapping[str,object]) -> None:
        require_closed_mapping(payload,{"scope","request_sha256","response_sha256","pre_root","post_root"},"data-plane audit")
        event={"schema_version":"cmd-memory-data-plane-audit-v2","event_index":self.index+1,"previous_hash":self.head,"operation":operation,**payload}
        event["event_hash"]=content_sha256(event,ensure_ascii=False,allow_nan=False)
        if self.sync_each_event: append_jsonl_fsync(self.audit_path,event,ensure_ascii=False,allow_nan=False)
        else: self.events.append(event)
        self.index+=1; self.head=str(event["event_hash"])
    def add(self, *, content: str, scope: str) -> MemoryRecord:
        if not isinstance(content,str) or not content or not isinstance(scope,str) or not scope: raise ValueError("content and scope required")
        pre=self.root(scope); source=content_sha256(content,ensure_ascii=False,allow_nan=False)
        record=MemoryRecord(f"m-{len(self.rows.get(scope,()))}-{source[:12]}",content,source,scope); self.rows.setdefault(scope,[]).append(record)
        post=content_sha256({"previous_root":pre,"record":record.__dict__},ensure_ascii=False,allow_nan=False); self.roots[scope]=post
        self._audit("add",{"scope":scope,"request_sha256":source,"response_sha256":content_sha256(record.__dict__),"pre_root":pre,"post_root":post}); return record
    def search(self, *, query: str, scope: str, limit: int) -> tuple[MemoryRecord,...]:
        if not isinstance(query,str) or not query or not isinstance(scope,str) or not scope or isinstance(limit,bool) or limit<1: raise ValueError("query, scope and positive limit required")
        pre=self.root(scope); terms=set(re.findall(r"[\w]+",query.lower()))
        found=sorted(self.rows.get(scope,()),key=lambda r:(-len(terms & set(re.findall(r"[\w]+",r.content.lower()))),r.memory_id))[:limit]
        self._audit("search",{"scope":scope,"request_sha256":content_sha256(query),"response_sha256":content_sha256([r.__dict__ for r in found]),"pre_root":pre,"post_root":pre}); return tuple(found)
    def root(self,scope:str)->str: return self.roots.get(scope,content_sha256([],ensure_ascii=False,allow_nan=False))
    def flush(self)->None:
        if self.sync_each_event or not self.events: return
        self.audit_path.parent.mkdir(parents=True,exist_ok=True)
        self.audit_path.write_text("".join(canonical_json(x,ensure_ascii=False,allow_nan=False)+"\n" for x in self.events),encoding="utf-8"); self.events.clear()

def _records_from_mem0(response: Any, scope: str) -> tuple[MemoryRecord,...]:
    rows=response.get("results",response.get("memories",response)) if isinstance(response,Mapping) else response
    if not isinstance(rows,(list,tuple)): raise RuntimeError("Mem0 search returned an unsupported response shape")
    result=[]
    for index,row in enumerate(rows):
        if not isinstance(row,Mapping): raise RuntimeError("Mem0 search row is not a mapping")
        ident=row.get("id",row.get("memory_id",f"sdk-{index}")); content=row.get("memory",row.get("content",row.get("text")))
        if not isinstance(ident,(str,int)) or not isinstance(content,str): raise RuntimeError("Mem0 search row lacks id/content")
        result.append(MemoryRecord(str(ident),content,content_sha256(content,ensure_ascii=False,allow_nan=False),scope))
    return tuple(result)

class Mem0DataPlane:
    """Explicit Mem0 boundary; client injection avoids SDK/network use in tests."""
    backend_name="mem0"
    def __init__(self, *, namespace:str, user_id:str, config:Mapping[str,object], audit_path:Path, client:Any|None=None, sdk_version:str|None=None)->None:
        if not namespace or not user_id or not isinstance(config,Mapping): raise ValueError("mem0 requires explicit namespace, user_id and config")
        self.namespace,self.user_id,self.config=namespace,user_id,dict(config)
        if client is None:
            try: from mem0 import Memory
            except ImportError as exc: raise RuntimeError("mem0 backend requested but the pinned mem0 SDK is unavailable") from exc
            try: client=Memory.from_config(dict(config))
            except Exception as exc: raise RuntimeError("mem0 backend configuration/client initialization failed") from exc
        self.client=client
        try: self.backend_version=sdk_version or importlib.metadata.version("mem0ai")
        except importlib.metadata.PackageNotFoundError: self.backend_version=sdk_version or "injected-client"
        self.audit=AuditedInMemoryDataPlane(audit_path)
    def _scope(self,scope:str)->None:
        if scope!=self.namespace: raise ValueError("cross-namespace Mem0 operation forbidden")
    def add(self, *, content:str, scope:str)->MemoryRecord:
        self._scope(scope)
        try: self.client.add(content,user_id=self.user_id)
        except Exception as exc: raise RuntimeError("Mem0 add failed") from exc
        return self.audit.add(content=content,scope=scope)
    def search(self, *, query:str, scope:str, limit:int)->tuple[MemoryRecord,...]:
        self._scope(scope)
        try: records=_records_from_mem0(self.client.search(query,user_id=self.user_id,limit=limit),scope)
        except RuntimeError: raise
        except Exception as exc: raise RuntimeError("Mem0 search failed") from exc
        pre=self.audit.root(scope); self.audit._audit("search",{"scope":scope,"request_sha256":content_sha256(query),"response_sha256":content_sha256([r.__dict__ for r in records]),"pre_root":pre,"post_root":pre}); return records
    def root(self,scope:str)->str: self._scope(scope); return self.audit.root(scope)
    @property
    def config_root(self)->str: return content_sha256(self.config,ensure_ascii=False,allow_nan=False)
    def flush(self)->None: self.audit.flush()

RealMem0DataPlane=Mem0DataPlane
