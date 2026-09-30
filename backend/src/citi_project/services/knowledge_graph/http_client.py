"""Verified HTTPS Query API transport with explicit, atomic transactions.

No automatic retries: a lost commit response has an unknown outcome. Callers
must reconcile before retrying. Server response text is never exposed in errors.
"""

import base64
import json
import re
import ssl
from http.client import HTTPException
from threading import RLock
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .client import GraphConnectionError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Basic credentials to a redirect target.
        return None


class QueryResult:
    def __init__(self, body):
        data = body.get("data")
        if not isinstance(data, dict):
            raise GraphConnectionError("Neo4j Query API returned malformed result data")
        fields, values = data.get("fields"), data.get("values")
        if (not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)
                or len(set(fields)) != len(fields) or not isinstance(values, list)
                or any(not isinstance(row, list) or len(row) != len(fields) for row in values)):
            raise GraphConnectionError("Neo4j Query API returned malformed result data")
        self.rows = [dict(zip(fields, row)) for row in values]

    def __iter__(self):
        return iter(self.rows)

    def single(self):
        if len(self.rows) > 1:
            raise GraphConnectionError("Neo4j Query API expected a single result")
        return self.rows[0] if self.rows else None

    def consume(self):
        return None  # responses are fully consumed before returning


class _Transaction:
    def __init__(self, client, path, affinity):
        self.client, self.path, self.affinity = client, path, affinity
        self.active = True

    def run(self, query, **parameters):
        if not self.active:
            raise GraphConnectionError("Neo4j Query API transaction is closed")
        body, _ = self.client._request("POST", self.path,
                                      {"statement": query, "parameters": parameters}, self.affinity)
        return QueryResult(body)


class QueryAPIClient:
    def __init__(self, config, *, opener=None):
        self.config = config
        self._endpoint = config.http_endpoint
        self._opener = opener
        self._closed = False
        self._lock = RLock()
        self._bookmarks = []

    def _request(self, method, path, payload=None, affinity=None):
        if self._closed:
            raise GraphConnectionError("Neo4j client is closed")
        try:
            if self._opener is None:
                self._opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
            auth = base64.b64encode((self.config.username + ":" + self.config.password).encode()).decode("ascii")
            headers = {"Authorization": "Basic " + auth, "Content-Type": "application/json", "Accept": "application/json"}
            if affinity:
                headers["neo4j-cluster-affinity"] = affinity
            data = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
            request = Request(self._endpoint + path, data=data, headers=headers, method=method)
            with self._opener.open(request, timeout=60) as response:
                if response.status != 202:
                    raise GraphConnectionError("Neo4j Query API returned an unexpected HTTP status")
                raw = response.read()
                body = json.loads(raw) if raw else {}
                affinity = response.headers.get("neo4j-cluster-affinity")
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise GraphConnectionError(f"Neo4j Query API HTTP request failed (status {status})") from None
        except (OSError, HTTPException, ValueError, TypeError):
            raise GraphConnectionError("Neo4j Query API request failed; check HTTPS connectivity, trusted certificates and configuration") from None
        if not isinstance(body, dict):
            raise GraphConnectionError("Neo4j Query API returned malformed JSON")
        if body.get("errors"):
            raise GraphConnectionError("Neo4j Query API rejected the operation; check permissions, query compatibility and database availability")
        return body, affinity

    def _execute(self, mode, callback, *args):
        # Serialize callbacks so bookmarks always describe the last completed transaction.
        with self._lock:
            body, affinity = self._request("POST", "/tx", {"accessMode": mode, "bookmarks": self._bookmarks})
            transaction = body.get("transaction")
            tid = transaction.get("id") if isinstance(transaction, dict) else None
            if not isinstance(tid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", tid):
                raise GraphConnectionError("Neo4j Query API returned an invalid transaction identifier")
            tx = _Transaction(self, "/tx/" + tid, affinity)
            try:
                result = callback(tx, *args)
            except BaseException:
                try:
                    self._request("DELETE", tx.path, affinity=affinity)
                except GraphConnectionError:
                    pass  # preserve original failure; abandoned transaction expires server-side
                raise
            finally:
                tx.active = False
            try:
                committed, _ = self._request("POST", tx.path + "/commit", {}, affinity)
                bookmarks = committed.get("bookmarks")
                if not isinstance(bookmarks, list) or not all(isinstance(b, str) for b in bookmarks):
                    raise GraphConnectionError("Missing commit acknowledgement")
                self._bookmarks = bookmarks
            except GraphConnectionError:
                raise GraphConnectionError("Neo4j Query API commit was not confirmed; outcome may be unknown. Reconcile before retrying") from None
            return result

    def read(self, callback, *args):
        return self._execute("Read", callback, *args)

    def write(self, callback, *args):
        return self._execute("Write", callback, *args)

    def check_connectivity(self):
        with self._lock:
            body, _ = self._request("POST", "", {"statement": "RETURN 1 AS ok", "parameters": {}, "accessMode": "Read"})
            row = QueryResult(body).single()
            if row != {"ok": 1}:
                raise GraphConnectionError("Neo4j Query API connectivity check returned an unexpected result")
            return True

    def close(self):
        with self._lock:
            self._closed = True
            self._opener = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
