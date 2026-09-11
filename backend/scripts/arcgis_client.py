"""Small, bounded ArcGIS FeatureServer client with deterministic caching."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable, Iterator, Mapping, Sequence
import uuid

import requests


class ArcGISError(RuntimeError):
    """Base error for an ArcGIS request or response failure."""


class ArcGISRequestError(ArcGISError):
    """The HTTP request failed or an offline cache entry was unavailable."""


class ArcGISResponseError(ArcGISError):
    """The service returned an error or a malformed payload."""


def validate_layer_metadata(
    metadata: Mapping[str, Any],
    *,
    geometry_type: str,
    required_fields: Sequence[str],
    require_m: bool = False,
) -> None:
    """Validate the source geometry and field contract before pagination."""
    if metadata.get("geometryType") != geometry_type:
        raise ArcGISResponseError(
            f"expected {geometry_type}, got {metadata.get('geometryType')!r}"
        )
    fields = metadata.get("fields")
    if not isinstance(fields, list):
        raise ArcGISResponseError("layer metadata lacks a fields array")
    field_names = {
        field.get("name") for field in fields if isinstance(field, Mapping)
    }
    missing = [field for field in required_fields if field not in field_names]
    if missing:
        raise ArcGISResponseError(f"layer metadata lacks required fields: {missing}")
    if require_m and metadata.get("hasM") is not True:
        raise ArcGISResponseError("road layer is not declared M-valued")


class ArcGISClient:
    """Read a single ArcGIS FeatureServer layer using offset pagination."""

    def __init__(
        self,
        layer_url: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10.0, 60.0),
        page_size: int | None = None,
        cache_dir: str | Path | None = None,
        offline: bool = False,
        refresh_cache: bool = False,
        max_retries: int = 3,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        if not layer_url.startswith(("https://", "http://")):
            raise ValueError("layer_url must be an HTTP(S) URL")
        if len(timeout) != 2 or any(value <= 0 for value in timeout):
            raise ValueError("timeout must contain positive connect/read seconds")
        if page_size is not None and page_size <= 0:
            raise ValueError("page_size must be positive")
        if offline and refresh_cache:
            raise ValueError("offline and refresh_cache cannot both be enabled")
        if max_retries < 0:
            raise ValueError("max_retries must be nonnegative")
        if retry_backoff_seconds < 0 or not math.isfinite(retry_backoff_seconds):
            raise ValueError("retry_backoff_seconds must be finite and nonnegative")

        self.layer_url = layer_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.page_size = page_size
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.offline = offline
        self.refresh_cache = refresh_cache
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    @staticmethod
    def _validate_payload(payload: Any, url: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ArcGISResponseError(f"ArcGIS response from {url} is not an object")
        error = payload.get("error")
        if error:
            if isinstance(error, Mapping):
                message = error.get("message") or json.dumps(error, sort_keys=True)
            else:
                message = str(error)
            raise ArcGISResponseError(f"ArcGIS service error: {message}")
        return payload

    @staticmethod
    def _is_transient(exc: requests.RequestException) -> bool:
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return False

    def _cache_path(self, url: str, params: Mapping[str, Any]) -> Path | None:
        if self.cache_dir is None:
            return None
        key = json.dumps(
            {"url": url, "params": sorted((str(key), str(value)) for key, value in params.items())},
            separators=(",", ":"),
        )
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _evict_cache(self, url: str, params: Mapping[str, Any]) -> None:
        cache_path = self._cache_path(url, params)
        if cache_path:
            cache_path.unlink(missing_ok=True)

    def _get_json(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        cache_path = self._cache_path(url, params)
        if cache_path and cache_path.exists() and not self.refresh_cache:
            try:
                payload = self._validate_payload(
                    json.loads(cache_path.read_text(encoding="utf-8")), url
                )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
                ArcGISResponseError,
            ) as exc:
                cache_path.unlink(missing_ok=True)
                if self.offline:
                    raise ArcGISResponseError(
                        f"invalid ArcGIS cache file {cache_path}: {exc}"
                    ) from exc
                return self._get_json(url, params)
        else:
            if self.offline:
                raise ArcGISRequestError(f"offline cache miss for {url}")
            for attempt in range(self.max_retries + 1):
                try:
                    response = self.session.get(url, params=dict(params), timeout=self.timeout)
                    response.raise_for_status()
                    payload = self._validate_payload(response.json(), url)
                    break
                except requests.RequestException as exc:
                    if attempt >= self.max_retries or not self._is_transient(exc):
                        raise ArcGISRequestError(f"ArcGIS request failed for {url}: {exc}") from exc
                    time.sleep(self.retry_backoff_seconds * (2**attempt))
                except (ValueError, OSError) as exc:
                    raise ArcGISResponseError(f"invalid ArcGIS response from {url}: {exc}") from exc
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache_path.with_name(
                    f"{cache_path.name}.{uuid.uuid4().hex}.tmp"
                )
                try:
                    temporary.write_text(json.dumps(payload), encoding="utf-8")
                    temporary.replace(cache_path)
                finally:
                    temporary.unlink(missing_ok=True)

        return self._validate_payload(payload, url)

    def metadata(
        self,
        *,
        geometry_type: str | None = None,
        required_fields: Sequence[str] = (),
        require_m: bool = False,
    ) -> dict[str, Any]:
        """Return layer metadata, raising on a malformed service response."""
        params = {"f": "json"}
        payload = self._get_json(self.layer_url, params)
        object_id_field = payload.get("objectIdField")
        maximum = payload.get("maxRecordCount")
        capabilities = payload.get("advancedQueryCapabilities")
        invalid_capabilities = (
            "advancedQueryCapabilities" in payload
            and not isinstance(capabilities, Mapping)
        )
        invalid_pagination = (
            isinstance(capabilities, Mapping)
            and "supportsPagination" in capabilities
            and not isinstance(capabilities["supportsPagination"], bool)
        )
        if not isinstance(object_id_field, str) or not object_id_field.strip():
            self._evict_cache(self.layer_url, params)
            raise ArcGISResponseError("layer metadata lacks objectIdField or it is invalid")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            self._evict_cache(self.layer_url, params)
            raise ArcGISResponseError("layer metadata has an invalid maxRecordCount")
        if invalid_capabilities or invalid_pagination:
            self._evict_cache(self.layer_url, params)
            raise ArcGISResponseError("layer metadata has invalid pagination capabilities")
        if geometry_type is not None:
            try:
                validate_layer_metadata(
                    payload,
                    geometry_type=geometry_type,
                    required_fields=required_fields,
                    require_m=require_m,
                )
            except ArcGISResponseError:
                self._evict_cache(self.layer_url, params)
                raise
        return payload

    def count(self, where: str = "1=1") -> int:
        """Return the source-side feature count for a query."""
        url = f"{self.layer_url}/query"
        params = {
            "f": "json",
            "where": where,
            "returnCountOnly": "true",
            "returnGeometry": "false",
        }
        payload = self._get_json(url, params)
        count = payload.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            self._evict_cache(url, params)
            raise ArcGISResponseError("count query returned an invalid count")
        return count

    @staticmethod
    def _feature_id(feature: Mapping[str, Any], object_id_field: str) -> Any:
        attributes = feature.get("attributes")
        if not isinstance(attributes, Mapping):
            attributes = feature.get("properties")
        return attributes.get(object_id_field) if isinstance(attributes, Mapping) else None

    def iter_features(
        self,
        *,
        out_fields: Sequence[str] | str,
        where: str = "1=1",
        output_format: str = "json",
        out_sr: int = 4326,
        return_geometry: bool = True,
        return_m: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Yield all matching features in deterministic object-ID order."""
        output_format = output_format.lower()
        if output_format not in {"json", "geojson"}:
            raise ValueError("output_format must be 'json' or 'geojson'")
        if output_format == "geojson" and return_m:
            raise ValueError("ArcGIS GeoJSON cannot preserve M values")
        if not where.strip():
            raise ValueError("where must not be empty")

        metadata = self.metadata()
        object_id_field = metadata["objectIdField"]
        maximum = metadata["maxRecordCount"]
        supports_pagination = metadata.get("advancedQueryCapabilities", {}).get(
            "supportsPagination", True
        )
        if not supports_pagination:
            raise ArcGISResponseError("layer does not support resultOffset pagination")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise ArcGISResponseError("layer maxRecordCount is invalid")
        page_size = min(self.page_size or maximum, maximum)
        fields = out_fields if isinstance(out_fields, str) else ",".join(out_fields)
        if not fields:
            raise ValueError("out_fields must not be empty")

        seen_ids: set[Any] = set()
        offset = 0
        while True:
            url = f"{self.layer_url}/query"
            params = {
                "f": output_format,
                "where": where,
                "outFields": fields,
                "returnGeometry": str(return_geometry).lower(),
                "returnM": str(return_m).lower(),
                "outSR": out_sr,
                "orderByFields": f"{object_id_field} ASC",
                "resultOffset": offset,
                "resultRecordCount": page_size,
            }
            payload = self._get_json(url, params)
            features = payload.get("features")
            if not isinstance(features, list):
                self._evict_cache(url, params)
                raise ArcGISResponseError("feature page lacks a features array")
            if not features and payload.get("exceededTransferLimit"):
                self._evict_cache(url, params)
                raise ArcGISResponseError("pagination made no progress")

            for feature in features:
                if not isinstance(feature, dict):
                    self._evict_cache(url, params)
                    raise ArcGISResponseError("feature page contains a non-object feature")
                feature_id = self._feature_id(feature, object_id_field)
                if feature_id is None:
                    self._evict_cache(url, params)
                    raise ArcGISResponseError(f"feature lacks object ID field {object_id_field}")
                if feature_id in seen_ids:
                    self._evict_cache(url, params)
                    raise ArcGISResponseError(f"duplicate object ID across pages: {feature_id}")
                seen_ids.add(feature_id)
                yield feature

            offset += len(features)
            if not payload.get("exceededTransferLimit", len(features) == page_size):
                break
            if not features:
                raise ArcGISResponseError("pagination made no progress")


def collect_features(features: Iterable[dict[str, Any]], *, expected_count: int) -> list[dict[str, Any]]:
    """Materialize a feature iterator and reject incomplete or surplus results."""
    collected = list(features)
    if len(collected) != expected_count:
        raise ArcGISResponseError(
            f"feature count mismatch: source reported {expected_count}, fetched {len(collected)}"
        )
    return collected
