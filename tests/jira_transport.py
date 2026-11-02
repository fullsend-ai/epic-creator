"""Repository submission against a synthetic Jira HTTP boundary."""
from copy import deepcopy
from email.parser import BytesParser
from email.policy import default
import io
import json
from pathlib import Path
import re
import shutil
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse


class Response(io.BytesIO):
    status = 200
    headers = {}


class Jira:
    """Persist synthetic issues across reruns; fail before/after an HTTP effect."""

    def __init__(self):
        self.issues = {}
        self.links = set()
        self.writes = []
        self.attempts = []
        self.failure = None
        self.hidden = False
        self.incomplete = False
        self.next_key = 1

    def urlopen(self, request, **kwargs):
        path = urlparse(request.full_url).path.removeprefix("/rest/api/3")
        method = request.get_method()
        operation = (method, path)
        self.attempts.append(operation)
        if self.failure == (operation, "before"):
            self.failure = None
            raise URLError("synthetic transport failure")
        result = self.handle(method, path, request)
        if method != "GET" and path != "/search/jql":
            self.writes.append(operation)
        if self.failure == (operation, "after"):
            self.failure = None
            raise URLError("synthetic response lost after commit")
        return Response(json.dumps(result).encode())

    def handle(self, method, path, request):
        if method == "GET" and path.startswith("/issue/"):
            return deepcopy(self.issues[path.split("/")[2]])
        if path == "/search/jql":
            body = json.loads(request.data)
            marker = re.search(r'labels = "([^"]+)"', body["jql"]).group(1)
            issues = [deepcopy(i) for i in self.issues.values()
                      if marker in i["fields"].get("labels", [])]
            result = {"issues": [] if self.hidden else issues, "isLast": True}
            if self.incomplete:
                result.update(isLast=False, nextPageToken="more")
            return result
        if method == "POST" and path == "/issue":
            fields = json.loads(request.data)["fields"]
            key = f"{fields['project']['key']}-{self.next_key}"
            self.next_key += 1
            fields.update(attachment=[], issuelinks=[])
            self.issues[key] = {"key": key, "id": str(self.next_key), "fields": fields}
            return {"key": key, "id": str(self.next_key)}
        if method == "PUT" and path.startswith("/issue/"):
            issue = self.issues[path.split("/")[2]]
            body = json.loads(request.data)
            issue["fields"].update(body.get("fields", {}))
            for label in body.get("update", {}).get("labels", []):
                if label["add"] not in issue["fields"]["labels"]:
                    issue["fields"]["labels"].append(label["add"])
            return None
        if method == "POST" and path == "/issueLink":
            body = json.loads(request.data)
            inward, outward = body["inwardIssue"]["key"], body["outwardIssue"]["key"]
            name = body["type"]["name"]
            self.links.add((name, inward, outward))
            self.issues[inward]["fields"]["issuelinks"].append(
                {"type": {"name": name}, "outwardIssue": {"key": outward}})
            self.issues[outward]["fields"]["issuelinks"].append(
                {"type": {"name": name}, "inwardIssue": {"key": inward}})
            return None
        if method == "POST" and path.endswith("/attachments"):
            key = path.split("/")[2]
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {request.get_header('Content-type')}\r\n\r\n".encode()
                + request.data)
            part = next(message.iter_parts())
            attachment = {"id": str(len(self.writes) + 1),
                          "filename": part.get_filename(),
                          "content": "https://jira.example.test/attachment/content/1"}
            self.issues[key]["fields"]["attachment"].append(attachment)
            return [attachment]
        raise AssertionError(f"Unexpected HTTP request: {method} {path}")
