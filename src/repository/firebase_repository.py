"""
Purpose:
    Provides an object-oriented repository layer for Firebase Realtime Database
    (RTDB) along with a deterministic `InMemoryFirebaseAdapter` strictly for
    automated `pytest` unit and integration testing without polluting production data.
Inputs:
    Database URL, service account credentials path, and mock toggle flag.
Outputs:
    `FirebaseRepository` and `InMemoryFirebaseAdapter` classes supporting hierarchical
    CRUD operations (`get`, `set`, `update`, `push`, `delete`, `clear`).
"""

from __future__ import annotations

import copy
import os
import threading
import time
from typing import Any

import requests


class InMemoryFirebaseAdapter:
    """
    Purpose:
        Thread-safe, deterministic in-memory simulation of Firebase Realtime Database
        hierarchical JSON tree semantics. Used strictly in automated `pytest` tests
        to ensure fast, isolated execution without touching production Firebase.
    Inputs:
        initial_data (dict[str, Any] | None): Optional seed tree for testing.
    Outputs:
        In-memory adapter instance supporting path-based CRUD operations.
    """

    def __init__(self, initial_data: dict[str, Any] | None = None) -> None:
        """
        Purpose:
            Initialize the in-memory hierarchical JSON store and synchronization lock.
        Inputs:
            initial_data (dict[str, Any] | None): Optional dictionary to seed the tree.
        Outputs:
            None.
        """
        self._lock = threading.RLock()
        self._store: dict[str, Any] = copy.deepcopy(initial_data) if initial_data else {}
        self._push_counter: int = 0

    @staticmethod
    def normalize_path(path: str) -> list[str]:
        """
        Purpose:
            Split a slash-delimited Firebase RTDB path (e.g., `/users/lecturers/1`)
            into clean non-empty path segments.
        Inputs:
            path (str): Raw RTDB path string.
        Outputs:
            list[str]: Ordered list of path segment keys.
        """
        if not path:
            return []
        return [segment.strip() for segment in path.strip("/").split("/") if segment.strip()]

    def get(self, path: str) -> Any:
        """
        Purpose:
            Retrieve a deep copy of the data stored at the specified RTDB path.
        Inputs:
            path (str): Hierarchical RTDB path (e.g., `/students/stu_01`).
        Outputs:
            Any: Deep-copied value at the path, or `None` if the path does not exist.
        """
        segments = self.normalize_path(path)
        with self._lock:
            current: Any = self._store
            for segment in segments:
                if not isinstance(current, dict) or segment not in current:
                    return None
                current = current[segment]
            return copy.deepcopy(current)

    def set(self, path: str, value: Any) -> Any:
        """
        Purpose:
            Write or overwrite data at the given RTDB path. Setting `None` deletes
            the key, matching Firebase RTDB behavior.
        Inputs:
            path (str): Target RTDB path.
            value (Any): JSON-serializable payload to store.
        Outputs:
            Any: Deep copy of the stored value.
        """
        if value is None:
            self.delete(path)
            return None

        segments = self.normalize_path(path)
        with self._lock:
            if not segments:
                if not isinstance(value, dict):
                    raise ValueError("Root Firebase RTDB node must be a dictionary.")
                self._store = copy.deepcopy(value)
                return copy.deepcopy(self._store)

            current = self._store
            # Traverse or create intermediate dictionary nodes up to the parent
            for segment in segments[:-1]:
                if segment not in current or not isinstance(current[segment], dict):
                    current[segment] = {}
                current = current[segment]

            leaf_key = segments[-1]
            current[leaf_key] = copy.deepcopy(value)
            return copy.deepcopy(current[leaf_key])

    def update(self, path: str, updates: dict[str, Any]) -> dict[str, Any]:
        """
        Purpose:
            Perform a non-destructive shallow merge of fields at the target RTDB path,
            matching Firebase `ref.update()` / HTTP `PATCH` semantics.
        Inputs:
            path (str): Target RTDB dictionary node path.
            updates (dict[str, Any]): Key-value fields to merge (keys with value `None` are deleted).
        Outputs:
            dict[str, Any]: Updated dictionary state at the target path.
        """
        if not isinstance(updates, dict):
            raise TypeError("updates payload for Firebase update() must be a dict.")

        with self._lock:
            existing = self.get(path)
            if not isinstance(existing, dict):
                existing = {}

            for key, val in updates.items():
                if val is None:
                    existing.pop(key, None)
                else:
                    existing[key] = copy.deepcopy(val)

            self.set(path, existing)
            return copy.deepcopy(existing)

    def push(self, path: str, value: Any) -> str:
        """
        Purpose:
            Append a new child node under `path` using a chronological, unique
            push identifier and return the generated child key.
        Inputs:
            path (str): Collection path (e.g., `/comments` or `/polls`).
            value (Any): Payload to store under the generated key.
        Outputs:
            str: Generated unique child key (e.g., `push_1730000000_0001`).
        """
        with self._lock:
            self._push_counter += 1
            timestamp_ms = int(time.time() * 1000)
            generated_key = f"push_{timestamp_ms}_{self._push_counter:04d}"
            base_path = path.rstrip("/")
            full_path = f"{base_path}/{generated_key}" if base_path else generated_key
            self.set(full_path, value)
            return generated_key

    def delete(self, path: str) -> bool:
        """
        Purpose:
            Remove the node at `path` from the in-memory tree if present.
        Inputs:
            path (str): Target RTDB path to remove.
        Outputs:
            bool: `True` if an existing node was deleted, `False` if not found.
        """
        segments = self.normalize_path(path)
        with self._lock:
            if not segments:
                had_data = bool(self._store)
                self._store.clear()
                return had_data

            current: Any = self._store
            for segment in segments[:-1]:
                if not isinstance(current, dict) or segment not in current:
                    return False
                current = current[segment]

            leaf_key = segments[-1]
            if isinstance(current, dict) and leaf_key in current:
                del current[leaf_key]
                return True
            return False

    def clear(self) -> None:
        """
        Purpose:
            Reset the entire in-memory database tree and push counter.
        Inputs:
            None.
        Outputs:
            None.
        """
        with self._lock:
            self._store.clear()
            self._push_counter = 0


class FirebaseRepository:
    """
    Purpose:
        Unified data access repository for Firebase Realtime Database. Supports:
        1) Live Firebase Admin SDK mode (when `serviceAccountKey.json` is available),
        2) Live Firebase REST JSON API mode against `FIREBASE_DB_URL`, and
        3) Deterministic in-memory mock mode (`use_mock=True`) for `pytest` suites.
    Inputs:
        db_url (str): Firebase RTDB base URL.
        credentials_path (str): Path to `serviceAccountKey.json`.
        use_mock (bool): Whether to route all operations to `InMemoryFirebaseAdapter`.
        mock_adapter (InMemoryFirebaseAdapter | None): Optional pre-configured mock adapter.
    Outputs:
        `FirebaseRepository` instance providing clean CRUD methods across all RTDB collections.
    """

    def __init__(
        self,
        db_url: str = "https://ai-teaching-assistant-web-default-rtdb.firebaseio.com/",
        credentials_path: str = "serviceAccountKey.json",
        use_mock: bool = False,
        mock_adapter: InMemoryFirebaseAdapter | None = None,
        http_timeout: float = 10.0,
    ) -> None:
        """
        Purpose:
            Configure the FirebaseRepository connection strategy (Mock, Admin SDK, or REST).
        Inputs:
            db_url (str): Firebase RTDB URL.
            credentials_path (str): File path to `serviceAccountKey.json`.
            use_mock (bool): True to use `InMemoryFirebaseAdapter`.
            mock_adapter (InMemoryFirebaseAdapter | None): Optional existing mock adapter.
            http_timeout (float): Timeout in seconds for REST requests.
        Outputs:
            None.
        """
        self.db_url = db_url.rstrip("/")
        self.credentials_path = credentials_path
        self.use_mock = use_mock
        self.http_timeout = http_timeout
        self._mock = mock_adapter or InMemoryFirebaseAdapter()
        self._admin_db: Any = None

        if not self.use_mock and os.path.isfile(self.credentials_path):
            self._try_init_firebase_admin()

    def _try_init_firebase_admin(self) -> None:
        """
        Purpose:
            Attempt to initialize the official `firebase_admin` SDK using the
            service account JSON file if installed and valid.
        Inputs:
            None.
        Outputs:
            None (populates `self._admin_db` when successful).
        """
        try:
            import firebase_admin
            from firebase_admin import credentials, db

            if not firebase_admin._apps:
                cred = credentials.Certificate(self.credentials_path)
                firebase_admin.initialize_app(cred, {"databaseURL": self.db_url})
            self._admin_db = db
        except Exception:
            # Gracefully fall back to the Firebase RTDB REST API if firebase_admin is unavailable
            self._admin_db = None

    def _build_rest_url(self, path: str) -> str:
        """
        Purpose:
            Build a valid Firebase RTDB REST endpoint URL ending with `.json`.
        Inputs:
            path (str): Hierarchical RTDB node path.
        Outputs:
            str: Full HTTPS endpoint URL for Firebase REST API calls.
        """
        clean_path = "/".join(InMemoryFirebaseAdapter.normalize_path(path))
        if clean_path:
            return f"{self.db_url}/{clean_path}.json"
        return f"{self.db_url}/.json"

    def get(self, path: str) -> Any:
        """
        Purpose:
            Fetch the JSON node at `path` from Firebase RTDB (or the mock store).
        Inputs:
            path (str): RTDB path (e.g., `/students`, `/polls/poll_1`).
        Outputs:
            Any: Deserialized Python value at `path`, or `None` if absent.
        """
        if self.use_mock:
            return self._mock.get(path)

        if self._admin_db is not None:
            clean_path = "/" + "/".join(InMemoryFirebaseAdapter.normalize_path(path))
            return self._admin_db.reference(clean_path).get()

        response = requests.get(self._build_rest_url(path), timeout=self.http_timeout)
        response.raise_for_status()
        return response.json()

    def set(self, path: str, value: Any) -> Any:
        """
        Purpose:
            Overwrite the node at `path` with `value` in Firebase RTDB (or mock store).
        Inputs:
            path (str): Target RTDB path.
            value (Any): JSON-serializable object to store.
        Outputs:
            Any: Stored payload.
        """
        if self.use_mock:
            return self._mock.set(path, value)

        if self._admin_db is not None:
            clean_path = "/" + "/".join(InMemoryFirebaseAdapter.normalize_path(path))
            self._admin_db.reference(clean_path).set(value)
            return value

        response = requests.put(
            self._build_rest_url(path), json=value, timeout=self.http_timeout
        )
        response.raise_for_status()
        return response.json()

    def update(self, path: str, updates: dict[str, Any]) -> dict[str, Any]:
        """
        Purpose:
            Merge the dictionary `updates` into the existing node at `path`.
        Inputs:
            path (str): Target RTDB path.
            updates (dict[str, Any]): Dictionary of child keys to update.
        Outputs:
            dict[str, Any]: Merged dictionary result.
        """
        if self.use_mock:
            return self._mock.update(path, updates)

        if self._admin_db is not None:
            clean_path = "/" + "/".join(InMemoryFirebaseAdapter.normalize_path(path))
            self._admin_db.reference(clean_path).update(updates)
            return updates

        response = requests.patch(
            self._build_rest_url(path), json=updates, timeout=self.http_timeout
        )
        response.raise_for_status()
        return response.json()

    def push(self, path: str, value: Any) -> str:
        """
        Purpose:
            Create a new uniquely-keyed child entry under `path` and return its key.
        Inputs:
            path (str): Collection path in RTDB.
            value (Any): JSON-serializable entry payload.
        Outputs:
            str: Generated Firebase child key.
        """
        if self.use_mock:
            return self._mock.push(path, value)

        if self._admin_db is not None:
            clean_path = "/" + "/".join(InMemoryFirebaseAdapter.normalize_path(path))
            new_ref = self._admin_db.reference(clean_path).push(value)
            return str(new_ref.key)

        response = requests.post(
            self._build_rest_url(path), json=value, timeout=self.http_timeout
        )
        response.raise_for_status()
        payload = response.json() or {}
        return str(payload.get("name", ""))

    def delete(self, path: str) -> bool:
        """
        Purpose:
            Delete the node at `path` from Firebase RTDB (or mock store).
        Inputs:
            path (str): Target RTDB path to delete.
        Outputs:
            bool: `True` if deleted (or command succeeded), `False` if absent in mock.
        """
        if self.use_mock:
            return self._mock.delete(path)

        if self._admin_db is not None:
            clean_path = "/" + "/".join(InMemoryFirebaseAdapter.normalize_path(path))
            self._admin_db.reference(clean_path).delete()
            return True

        response = requests.delete(self._build_rest_url(path), timeout=self.http_timeout)
        response.raise_for_status()
        return True

    def reset_mock(self) -> None:
        """
        Purpose:
            Clear all data in the underlying `InMemoryFirebaseAdapter`. Raises a
            `RuntimeError` if called when `use_mock` is False to protect live data.
        Inputs:
            None.
        Outputs:
            None.
        """
        if not self.use_mock:
            raise RuntimeError("reset_mock() is only permitted when use_mock=True.")
        self._mock.clear()
