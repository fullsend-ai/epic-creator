"""Local HTTPS Jira transport for CLI integration tests."""
from copy import deepcopy
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml


class LocalJira:
    """A wire-level Jira substitute, including ambiguous transport outcomes."""

    def __init__(self):
        self.issues = {}
        self.contents = {}
        self.requests = []
        self.links = set()
        self.drop_after = None
        self.next_key = 1
        self.components = ["Dashboard"]

    @property
    def writes(self):
        return [r for r in self.requests
                if r["method"] != "GET" and r["path"] != "/rest/api/3/search/jql"]

    def handle(self, method, url, headers, raw):
        parsed = urlsplit(url)
        path = parsed.path.removeprefix("/rest/api/3")
        body = json.loads(raw) if raw and "application/json" in headers.get("Content-Type", "") else None
        self.requests.append({"method": method, "path": parsed.path, "body": deepcopy(body)})
        if method == "GET" and path == "/myself":
            return {"accountId": "synthetic-account", "active": True}
        if method == "GET" and path.startswith("/attachment/"):
            return self.contents[path.rsplit("/", 1)[-1]].encode()
        if method == "GET" and path.startswith("/project/"):
            return [{"name": name} for name in self.components]
        if path == "/search/jql":
            query = body or {k: v[0] for k, v in parse_qs(parsed.query).items()}
            marker = re.search(r'labels = "([^"]+)"', query["jql"])
            matches = [i for i in self.issues.values()
                       if (marker and marker[1] in i["fields"].get("labels", []))
                       or (not marker and i["fields"]["issuetype"]["name"] != "Epic")]
            return {"issues": deepcopy(matches), "isLast": True}
        if path.endswith("/comment"):
            fields = self.issues[path.split("/")[2]]["fields"]
            comments = fields.setdefault("comment", [])
            if method == "POST":
                comment = {"id": str(len(comments) + 1), **body}
                comments.append(comment)
                return comment
            return {"comments": deepcopy(comments), "total": len(comments)}
        if method == "GET" and path.startswith("/issue/"):
            return deepcopy(self.issues[path.split("/")[2]])
        if method == "POST" and path == "/issue":
            fields = body["fields"]
            fields["issuetype"]["name"] = fields["issuetype"].pop("name", None) or "Epic"
            key = f"{fields['project']['key']}-{self.next_key}"
            self.next_key += 1
            fields.update(attachment=[], issuelinks=[])
            self.issues[key] = {"key": key, "id": str(self.next_key), "fields": fields}
            return {"key": key, "id": str(self.next_key)}
        if method == "PUT" and path.startswith("/issue/"):
            fields = self.issues[path.split("/")[2]]["fields"]
            fields.update(body.get("fields", {}))
            for change in body.get("update", {}).get("labels", []):
                if change["add"] not in fields["labels"]:
                    fields["labels"].append(change["add"])
            return None
        if method == "POST" and path == "/issueLink":
            inward, outward = body["inwardIssue"]["key"], body["outwardIssue"]["key"]
            name = body["type"]["name"]
            self.links.add((name, inward, outward))
            self.issues[inward]["fields"]["issuelinks"].append(
                {"type": {"name": name}, "outwardIssue": {"key": outward}})
            self.issues[outward]["fields"]["issuelinks"].append(
                {"type": {"name": name}, "inwardIssue": {"key": inward}})
            return None
        if method == "POST" and path.endswith("/attachments"):
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {headers['Content-Type']}\r\n\r\n".encode() + raw)
            part = next(message.iter_parts())
            key = path.split("/")[2]
            attachment = {"id": str(len(self.requests)), "filename": part.get_filename()}
            self.issues[key]["fields"]["attachment"].append(attachment)
            return [attachment]
        raise AssertionError(f"Unexpected request: {method} {path}")


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    directory = tmp_path_factory.mktemp("e2e-tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   capture_output=True, text=True, check=True)
    return cert, key


@pytest.fixture
def local_jira(certificate, tmp_path):
    jira = LocalJira()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                result = jira.handle(self.command, self.path, self.headers, raw)
            except Exception as exc:
                self.send_error(400, str(exc))
                return
            operation = (self.command, urlsplit(self.path).path.removeprefix("/rest/api/3"))
            if jira.drop_after == operation:
                jira.drop_after = None
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            data = result if isinstance(result, bytes) else json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    jira.url = f"https://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield jira
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    (tmp_path / "http-requests.json").write_text(json.dumps(jira.requests, indent=2))
