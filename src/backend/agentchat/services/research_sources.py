"""有大小/时间限制的论文与网页读取；所有来源保留获取地址、时间和内容哈希。"""
import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote, urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from agentchat.settings import app_settings

MAX_PAPERS = 6
MAX_WEB = 2
_arxiv_lock = asyncio.Lock()
_arxiv_last = 0.0


def plain(text):
    return unescape(re.sub(r"<[^>]*>", " ", text or "")).strip()


def source_id(value):
    return "S" + hashlib.sha256(value.encode()).hexdigest()[:12]


def checked_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("来源地址必须是无凭据的公开 HTTP(S) 地址")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("来源地址端口不被允许")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname.lower() == "localhost" or parsed.hostname.lower().endswith((".localhost", ".local", ".internal")):
            raise ValueError("不能读取本机或内部来源地址")
    else:
        if not address.is_global:
            raise ValueError("不能读取非公开来源地址")
    return value


class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM, family=family)
        if not records or any(not ipaddress.ip_address(row[4][0]).is_global for row in records):
            raise ValueError("来源域名解析到非公开地址")
        # 连接器直接使用这些已验证地址，避免校验后另一次 DNS 查询。
        return [{"hostname": host, "host": row[4][0], "port": port, "family": row[0],
                 "proto": socket.IPPROTO_TCP, "flags": socket.AI_NUMERICHOST} for row in records]

    async def close(self):
        pass


async def fetch_public(url, *, params=None, headers=None, json_body=None, limit=2_000_000):
    checked_url(url)
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=45), trust_env=False) as client:
        for _ in range(5):
            async with client.request("POST" if json_body is not None else "GET", url, params=params,
                    json=json_body, headers={"User-Agent": "AgentChat-Research/1.0", **(headers or {})}, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    if json_body is not None or headers:
                        raise ValueError("带服务凭据的请求不跟随重定向")
                    url = checked_url(urljoin(str(response.url), response.headers.get("Location", "")))
                    params = None
                    continue
                response.raise_for_status()
                if response.content_length and response.content_length > limit:
                    raise ValueError("来源文件超过大小限制")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(32768):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("来源文件超过大小限制")
                    chunks.append(chunk)
                body = b"".join(chunks)
                return body, {"url": str(response.url), "retrieved_at": datetime.now(timezone.utc).isoformat(),
                              "sha256": hashlib.sha256(body).hexdigest(), "bytes": size,
                              "content_type": response.headers.get("Content-Type", "")}
    raise ValueError("来源重定向次数过多")


def arxiv_items(body):
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("不支持包含实体声明的 XML")
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(body)
    results = []
    for item in root.findall("a:entry", ns):
        url = item.findtext("a:id", "", ns).replace("http://", "https://")
        identifier = url.split("/abs/")[-1]
        if not identifier or "/abs/" not in url:
            continue
        results.append({"id": source_id("arxiv:" + re.sub(r"v\d+$", "", identifier)), "kind": "paper", "provider": "arxiv",
            "title": " ".join(item.findtext("a:title", "", ns).split()), "url": url,
            "authors": [author.findtext("a:name", "", ns) for author in item.findall("a:author", ns)],
            "year": item.findtext("a:published", "", ns)[:4], "abstract": item.findtext("a:summary", "", ns).strip(),
            "doi": item.findtext("x:doi", "", ns), "arxiv_id": identifier,
            "pdf_url": "https://arxiv.org/pdf/" + identifier, "raw_metadata": ET.tostring(item, encoding="unicode")})
    return results


def crossref_item(item):
    doi = str(item.get("DOI", "")).lower()
    parts = item.get("published", {}).get("date-parts", [[]])
    pdf = next((link.get("URL", "") for link in item.get("link", []) if link.get("content-type") == "application/pdf"), "")
    return {"id": source_id("doi:" + doi), "kind": "paper", "provider": "crossref", "doi": doi,
        "title": plain((item.get("title") or [doi])[0]), "url": "https://doi.org/" + quote(doi, safe="/"),
        "year": str(parts[0][0]) if parts and parts[0] else "", "abstract": plain(item.get("abstract", "")),
        "authors": [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in item.get("author", [])],
        "pdf_url": pdf, "raw_metadata": item}


def parse_pdf(body):
    import fitz
    if not body.lstrip().startswith(b"%PDF-"):
        raise ValueError("来源未返回 PDF 文件")
    with fitz.open(stream=body, filetype="pdf") as document:
        if document.needs_pass:
            raise ValueError("无法读取加密 PDF")
        # Retain full page text for targeted retrieval. Resource limits are explicit;
        # model input is selected separately and is no longer the first 20 pages.
        pages, remaining, truncated = [], 2_000_000, len(document) > 300
        for index in range(min(len(document), 300)):
            original = document[index].get_text()
            retained = original[:remaining]
            pages.append({"page": index + 1, "text": retained})
            remaining -= len(retained)
            if len(retained) < len(original) or remaining == 0:
                truncated = True
                break
        return {"pages": pages, "total_pages": len(document), "truncated": truncated}


class ResearchSources:
    def __init__(self, sandbox=None):
        self.sandbox = sandbox
    async def arxiv(self, query, *, identifier=None):
        global _arxiv_last
        async with _arxiv_lock:
            await asyncio.sleep(max(0, 3 - (time.monotonic() - _arxiv_last)))
            try:
                body, provenance = await fetch_public("https://export.arxiv.org/api/query", params={
                    **({"id_list": identifier} if identifier else {"search_query": query}), "max_results": 3})
            finally:
                _arxiv_last = time.monotonic()
        return {"sources": arxiv_items(body), "provenance": provenance}

    async def crossref(self, query, *, doi=None):
        url = "https://api.crossref.org/works" + ("/" + quote(doi, safe="") if doi else "")
        body, provenance = await fetch_public(url, params=None if doi else {"query.bibliographic": query, "rows": 3})
        response = json.loads(body)["message"]
        items = [response] if doi else response.get("items", [])
        return {"sources": [crossref_item(item) for item in items if item.get("DOI")], "provenance": provenance}

    async def web(self, query):
        settings = app_settings.tools
        if settings and settings.tavily.get("api_key"):
            body, provenance = await fetch_public("https://api.tavily.com/search", json_body={
                "query": query, "max_results": 3, "search_depth": "basic"},
                headers={"Authorization": "Bearer " + settings.tavily["api_key"]})
            response = json.loads(body)
            items = response.get("results", [])
            provider = "tavily"
        elif settings and settings.bocha.get("api_key") and settings.bocha.get("endpoint"):
            body, provenance = await fetch_public(settings.bocha["endpoint"], json_body={"query": query, "count": 3, "summary": True},
                headers={"Authorization": "Bearer " + settings.bocha["api_key"]})
            response = json.loads(body)
            if response.get("code") != 200:
                raise ValueError("Bocha 未返回有效结果")
            items = [{"url": item.get("url"), "title": item.get("name"), "content": item.get("summary", "")}
                     for item in (response.get("data") or {}).get("webPages", {}).get("value", [])]
            provider = "bocha"
        else:
            return {"sources": [], "warning": "网页搜索未配置，已使用 arXiv / Crossref 学术检索；不能据此声称完成了全网检索。"}
        return {"sources": [{"id": source_id(item["url"]), "kind": "web", "provider": provider,
                    "title": item.get("title") or item["url"], "url": checked_url(item["url"]),
                    "abstract": item.get("content", "")[:8000], "raw_metadata": item}
                    for item in items if item.get("url")], "provenance": provenance, "raw_response": response}

    async def read(self, source):
        result = {"metadata_verified": False, "pages": [], "read_level": "metadata", "warnings": []}
        if source["kind"] == "paper":
            if source["provider"] == "arxiv":
                lookup = await self.arxiv("", identifier=source["arxiv_id"])
            else:
                lookup = await self.crossref("", doi=source["doi"])
            result["metadata_check"] = lookup
            result["provenance"] = lookup["provenance"]
            result["metadata_verified"] = any(row["id"] == source["id"] and row["title"].casefold() == source["title"].casefold() for row in lookup["sources"])
            if source.get("pdf_url"):
                body, provenance = await fetch_public(source["pdf_url"], limit=12_000_000)
                if self.sandbox is None:
                    from agentchat.services.sandbox import CURRENT_RUN, SandboxUnavailable
                    context = CURRENT_RUN.get()
                    if context is None:
                        raise SandboxUnavailable('PDF 解析必须通过持久化任务沙箱执行')
                    sandbox = context.get_sandbox()
                else:
                    sandbox = self.sandbox
                parsed = await sandbox.run('parse_pdf', 'pdf:' + source['id'], data=body)
                result.update(parsed['parsed'], provenance=provenance, read_level="pdf", sandbox_job_id=parsed['job_id'])
                normalize = lambda text: re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", text).casefold())
                first_page = normalize(result["pages"][0]["text"]) if result["pages"] else ""
                title, doi = normalize(source["title"]), normalize(source.get("doi", ""))
                result["pdf_identity_verified"] = bool((len(title) >= 8 and title in first_page) or (len(doi) >= 8 and doi in first_page))
                result["metadata_verified"] = result["metadata_verified"] and result["pdf_identity_verified"]
                if not result["pdf_identity_verified"]:
                    result["warnings"].append("PDF 首页未匹配到论文标题或 DOI，来源身份待人工核实；正文陈述不作为已核实结论。")
                if result["truncated"]:
                    result["warnings"].append("PDF 提取达到 300 页或 200 万字符的资源限制；本报告未覆盖全文。")
                if not any(page["text"].strip() for page in result["pages"]):
                    result["warnings"].append("PDF 没有可提取文字，需要 OCR，不能推断其正文结论。")
                return result
        else:
            body, provenance = await fetch_public(source["url"])
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(body, "html.parser")
            for element in soup(["script", "style", "nav", "footer", "noscript"]):
                element.decompose()
            result.update(pages=[{"page": None, "text": soup.get_text(" ", strip=True)[:24000]}], provenance=provenance, read_level="web")
            return result
        result["pages"] = [{"page": None, "text": source.get("abstract", "")}]
        result["warnings"].append("未读取 PDF，仅有元数据或摘要；方法、实验及局限性可能缺失。")
        return result
