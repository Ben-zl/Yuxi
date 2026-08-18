"""Unity Profiler Analyzer - Configuration Management"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict


@dataclass
class Config:
    """Application configuration"""

    # API Configuration
    base_url: str = "http://10.11.10.173:8080"
    project_id: str = "pcavpt6w"
    timeout: int = 30
    max_retries: int = 3
    verify_ssl: bool = False

    # Cache Configuration
    cache_enabled: bool = True
    cache_dir: str = "./cache"
    cache_ttl: int = 86400  # 24 hours
    auto_cleanup: bool = True
    cleanup_days: int = 7

    # Directory Configuration
    temp_dir: str = ".upa_temp"
    reports_dir: str = "reports"

    # Preprocessing Configuration
    preprocess_enabled: bool = True
    big_data_thresholds: dict = field(default_factory=lambda: {
        "function_list": 1000,
        "jank_frames": 100,
        "frame_samples": 50,
    })

    # Loading Configuration
    progressive_loading: bool = True
    parallel_loading: bool = True
    max_parallel: int = 4

    # Output Configuration
    output_format: str = "markdown"  # markdown, json
    verbose: bool = False
    debug: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        """Create configuration from environment variables"""
        return cls(
            base_url=os.getenv("UBOX_BASE_URL", "http://10.11.10.173:8080"),
            project_id=os.getenv("UBOX_PROJECT_ID", "pcavpt6w"),
            timeout=int(os.getenv("UBOX_TIMEOUT", "30")),
            cache_dir=os.getenv("UBOX_CACHE_DIR", "./cache"),
            cache_ttl=int(os.getenv("UBOX_CACHE_TTL", "86400")),
            temp_dir=os.getenv("UPA_TEMP_DIR", ".upa_temp"),
            reports_dir=os.getenv("UPA_REPORTS_DIR", "reports"),
        )

    @classmethod
    def from_config_file(cls, config_path: Optional[str] = None) -> "Config":
        """Create configuration from JSON config file

        Args:
            config_path: Path to config file. If None, searches in:
                        1. Current directory: .upa-config.json
                        2. User home: ~/.upa/config.json
                        3. Skill directory: scripts/.upa-config.json

        Returns:
            Config instance with values from config file
        """
        config_data = {}

        # Search paths for config file
        search_paths = []
        if config_path:
            search_paths.append(Path(config_path))
        else:
            # Current working directory
            search_paths.append(Path.cwd() / ".upa-config.json")
            # User home directory
            home_config = Path.home() / ".upa" / "config.json"
            search_paths.append(home_config)
            # Skill directory
            skill_dir = Path(__file__).parent.parent.parent
            search_paths.append(skill_dir / ".upa-config.json")

        # Try to load config file
        for path in search_paths:
            if path.exists():
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    if cls.verbose:
                        print(f"Loaded configuration from: {path}")
                    break
                except (json.JSONDecodeError, IOError) as e:
                    print(f"Warning: Failed to load config from {path}: {e}")

        # Create config instance
        if config_data:
            # Extract directory configuration
            directories = config_data.get("directories", {})

            return cls(
                # API config
                base_url=config_data.get("base_url", "http://10.11.10.173:8080"),
                project_id=config_data.get("project_id", "pcavpt6w"),
                timeout=config_data.get("timeout", 30),
                # Cache config
                cache_dir=config_data.get("cache_dir", directories.get("cache", "./cache")),
                cache_ttl=config_data.get("cache_ttl", 86400),
                # Directory config
                temp_dir=directories.get("temp", ".upa_temp"),
                reports_dir=directories.get("reports", "reports"),
                # Other config
                verbose=config_data.get("verbose", False),
                debug=config_data.get("debug", False),
            )
        else:
            # No config file found, use defaults
            return cls()

    def get_temp_dir(self, case_id: str) -> Path:
        """Get temporary file directory for a specific case

        Args:
            case_id: Case ID

        Returns:
            Path to temporary directory
        """
        temp_path = Path(self.temp_dir) / case_id
        temp_path.mkdir(parents=True, exist_ok=True)
        return temp_path

    def get_reports_dir(self, case_id: str) -> Path:
        """Get report directory for a specific case

        Args:
            case_id: Case ID

        Returns:
            Path to reports directory
        """
        report_path = Path(self.reports_dir) / case_id
        report_path.mkdir(parents=True, exist_ok=True)
        return report_path

    def to_dict(self) -> Dict:
        """Convert config to dictionary"""
        return {
            "base_url": self.base_url,
            "project_id": self.project_id,
            "timeout": self.timeout,
            "cache_dir": self.cache_dir,
            "cache_ttl": self.cache_ttl,
            "directories": {
                "temp": self.temp_dir,
                "reports": self.reports_dir,
                "cache": self.cache_dir,
            },
            "verbose": self.verbose,
            "debug": self.debug,
        }
