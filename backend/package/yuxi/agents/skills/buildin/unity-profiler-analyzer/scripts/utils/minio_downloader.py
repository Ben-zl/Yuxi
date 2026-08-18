"""Unity Profiler Analyzer - MinIO Data Downloader

Utility for downloading and processing data from MinIO URLs.
"""

import gzip
import json
import tempfile
import os
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests


class MinIODownloader:
    """Downloader for MinIO hosted data files

    Handles downloading gzip compressed JSON files from MinIO URLs,
    decompressing them, and parsing the JSON data.
    """

    def __init__(self, timeout: int = 30, max_retries: int = 3, verify_ssl: bool = False):
        """Initialize the downloader

        Args:
            timeout: Request timeout in seconds
            max_retries: Maximum number of download retries
            verify_ssl: Whether to verify SSL certificates (default: False for internal networks)
        """
        # Ensure timeout is an integer, not a Config object or other type
        if hasattr(timeout, 'timeout'):
            # If a Config object was passed, extract the timeout value
            self.timeout = int(timeout.timeout)
        elif isinstance(timeout, (int, float)):
            self.timeout = int(timeout)
        else:
            # Fallback to default
            self.timeout = 30

        self.max_retries = max_retries
        self.verify_ssl = verify_ssl

    def download_json(
        self,
        url: str,
        use_cache: bool = True,
        cache_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """Download and parse gzip compressed JSON from MinIO URL

        Args:
            url: MinIO URL (gzip compressed JSON)
            use_cache: Whether to use cached data
            cache_dir: Cache directory path

        Returns:
            Parsed JSON data as dictionary

        Raises:
            Exception: If download or parsing fails
        """
        # Check cache first
        if use_cache and cache_dir:
            cached = self._load_from_cache(url, cache_dir)
            if cached is not None:
                return cached

        # Download with retries
        for attempt in range(self.max_retries):
            try:
                # Ensure timeout is a valid integer (defensive programming)
                timeout_val = int(self.timeout) if not isinstance(self.timeout, int) else self.timeout
                response = requests.get(
                    url,
                    timeout=timeout_val,
                    stream=True,
                    verify=self.verify_ssl
                )
                response.raise_for_status()

                # Decompress and parse
                data = self._decompress_json(response.content)

                # Save to cache
                if use_cache and cache_dir:
                    self._save_to_cache(url, cache_dir, data)

                return data

            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    raise Exception(
                        f"Failed to download after {self.max_retries} attempts: {e}"
                    )
                continue

            except Exception as e:
                raise Exception(f"Failed to process data: {e}")

    def _decompress_json(self, compressed_data: bytes) -> Dict[str, Any]:
        """Decompress gzip data and parse JSON

        Args:
            compressed_data: Gzip compressed data

        Returns:
            Parsed JSON data

        Raises:
            Exception: If decompression or parsing fails
        """
        try:
            # Decompress gzip
            decompressed = gzip.decompress(compressed_data)

            # Parse JSON
            data = json.loads(decompressed.decode('utf-8'))

            return data

        except gzip.BadGzipFile:
            # Not gzip compressed, try direct JSON parsing
            try:
                return json.loads(compressed_data.decode('utf-8'))
            except json.JSONDecodeError as e:
                raise Exception(f"Failed to parse JSON: {e}")

        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse JSON: {e}")

        except Exception as e:
            raise Exception(f"Failed to decompress data: {e}")

    def _get_cache_path(self, url: str, cache_dir: str) -> str:
        """Get cache file path for URL

        Args:
            url: MinIO URL
            cache_dir: Cache directory

        Returns:
            Cache file path
        """
        # Create a unique filename from URL
        parsed = urlparse(url)
        path_parts = parsed.path.split('/')
        filename = path_parts[-1] if path_parts else 'data.json'

        # Use case_id from path for better organization
        if 'starsandisland' in path_parts:
            case_idx = path_parts.index('starsandisland')
            if case_idx + 1 < len(path_parts):
                case_id = path_parts[case_idx + 1]
                filename = f"{case_id}_{filename}"

        cache_path = os.path.join(cache_dir, filename)
        return cache_path

    def _load_from_cache(
        self,
        url: str,
        cache_dir: str
    ) -> Optional[Dict[str, Any]]:
        """Load data from cache

        Args:
            url: MinIO URL
            cache_dir: Cache directory

        Returns:
            Cached data if available and valid, None otherwise
        """
        try:
            cache_path = self._get_cache_path(url, cache_dir)

            if os.path.exists(cache_path):
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data

        except Exception:
            # Cache corrupted or unreadable, ignore
            pass

        return None

    def _save_to_cache(
        self,
        url: str,
        cache_dir: str,
        data: Dict[str, Any]
    ) -> None:
        """Save data to cache

        Args:
            url: MinIO URL
            cache_dir: Cache directory
            data: Data to cache
        """
        try:
            os.makedirs(cache_dir, exist_ok=True)

            cache_path = self._get_cache_path(url, cache_dir)

            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception:
            # Failed to cache, but this is not critical
            pass
