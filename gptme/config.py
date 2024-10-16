import glob
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import tomlkit
from pydantic import BaseModel, Field, HttpUrl
from tomlkit import TOMLDocument, comment
from tomlkit.container import Container

from .util import console, path_with_tilde

logger = logging.getLogger(__name__)


@dataclass
class Config:
    prompt: dict
    env: dict

    def get_env(self, key: str, default: str | None = None) -> str | None:
        """Gets an environment variable, checks the config file if it's not set in the environment."""
        return os.environ.get(key) or self.env.get(key) or default

    def get_env_required(self, key: str) -> str:
        """Gets an environment variable, checks the config file if it's not set in the environment."""
        if val := os.environ.get(key) or self.env.get(key):
            return val
        raise KeyError(  # pragma: no cover
            f"Environment variable {key} not set in env or config, see README."
        )

    def dict(self) -> dict:
        return {
            "prompt": self.prompt,
            "env": self.env,
        }


@dataclass
class ProjectConfig:
    """Project-level configuration, such as which files to include in the context by default."""

    files: list[str] = field(default_factory=list)


ABOUT_ACTIVITYWATCH = """ActivityWatch is a free and open-source automated time-tracker that helps you track how you spend your time on your devices."""
ABOUT_GPTME = "gptme is a CLI to interact with large language models in a Chat-style interface, enabling the assistant to execute commands and code on the local machine, letting them assist in all kinds of development and terminal-based work."


default_config = Config(
    prompt={
        "about_user": "I am a curious human programmer.",
        "response_preference": "Basic concepts don't need to be explained.",
        "project": {
            "activitywatch": ABOUT_ACTIVITYWATCH,
            "gptme": ABOUT_GPTME,
        },
    },
    env={
        # toml doesn't support None
        # "OPENAI_API_KEY": None
    },
)

# Define the path to the config file
config_path = os.path.expanduser("~/.config/gptme/config.toml")

# Global variable to store the config
_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def load_config() -> Config:
    config = _load_config()
    assert "prompt" in config, "prompt key missing in config"
    assert "env" in config, "env key missing in config"
    prompt = config.pop("prompt")
    env = config.pop("env")
    if config:
        logger.warning(f"Unknown keys in config: {config.keys()}")
    return Config(prompt=prompt, env=env)


def _load_config() -> tomlkit.TOMLDocument:
    # Check if the config file exists
    if not os.path.exists(config_path):
        # If not, create it and write some default settings
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        toml = tomlkit.dumps(default_config.dict())
        with open(config_path, "w") as config_file:
            config_file.write(toml)
        console.log(f"Created config file at {config_path}")
        doc = tomlkit.loads(toml)
        return doc
    else:
        with open(config_path) as config_file:
            doc = tomlkit.load(config_file)
        return doc


def set_config_value(key: str, value: str) -> None:  # pragma: no cover
    doc: TOMLDocument | Container = _load_config()

    # Set the value
    keypath = key.split(".")
    d = doc
    for key in keypath[:-1]:
        d = d.get(key, {})
    d[keypath[-1]] = value

    # Write the config
    with open(config_path, "w") as config_file:
        tomlkit.dump(doc, config_file)

    # Reload config
    global _config
    _config = load_config()


def comment_out(key: str, extra_comment: str):  # progma: no cover
    doc: TOMLDocument | Container = _load_config()

    # Set the value
    keypath = key.split(".")
    d = doc
    for key in keypath[:-1]:
        d = d.get(key, {})

    _key = keypath[-1]
    if value := d.get(_key, None):
        # drop old
        del d[_key]
        # comment out
        d.add(comment(f"{_key} = {value} # {extra_comment}"))

    # Write the config
    with open(config_path, "w") as config_file:
        tomlkit.dump(doc, config_file)

    # Reload config
    global _config
    _config = load_config()


def get_workspace_prompt(workspace: str) -> str:
    project_config_paths = [
        p
        for p in (
            Path(workspace) / "gptme.toml",
            Path(workspace) / ".github" / "gptme.toml",
        )
        if p.exists()
    ]
    if project_config_paths:
        project_config_path = project_config_paths[0]
        console.log(
            f"Using project configuration at {path_with_tilde(project_config_path)}"
        )
        # load project config
        with open(project_config_path) as f:
            project_config = tomlkit.load(f)
            project = ProjectConfig(**project_config)  # type: ignore
            # expand with glob
            files = [p for file in project.files for p in glob.glob(file)]
            for file in files:
                if not Path(file).exists():
                    logger.error(
                        f"File {file} specified in project config does not exist"
                    )
                    exit(1)
        return "\n\nSelected project files, read more with cat:\n" + "\n\n".join(
            [f"```{Path(file).name}\n{Path(file).read_text()}\n```" for file in files]
        )
    return ""


class Provider(str, Enum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure"
    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    LOCAL = "local"

    def is_openrouter(self) -> bool: 
        return self == Provider.OPENROUTER
    def is_openai_alike(self) -> bool:
        return self in [
            Provider.OPENAI,
            Provider.AZURE_OPENAI,
            Provider.OPENROUTER,
            Provider.LOCAL,
        ]

    def is_anthropic_alike(self) -> bool:
        return self == Provider.ANTHROPIC

    def __repr__(self) -> str:
        return self.value


class LLMAPIConfig(BaseModel):
    endpoint: HttpUrl | None = Field(default=None)
    token: str
    provider: Provider
    model: str | None

    _envvar_api_key: str = "API_KEY"
    _envvar_provider: str = "API_PROVIDER"
    _envvar_model: str = "API_MODEL"

    @property
    def _envvar_endpoint(self) -> str:
        if self.provider == Provider.OPENAI or self.provider == Provider.AZURE_OPENAI:
            return "API_ENDPOINT"
        return ""

    def save_to_config(self):
        set_config_value(f"env.{self._envvar_api_key}", self.token)
        set_config_value(f"env.{self._envvar_provider}", self.provider.value)
        if not self._envvar_endpoint:
            logger.warning(
                f"Provider {self.provider.value} has no custom endpoint, skipping saving to config"
            )
            return
        if self.model:
            set_config_value(f"env.{self._envvar_model}", self.model)
        if self.endpoint:
            set_config_value(f"env.{self._envvar_endpoint}", str(self.endpoint))


if __name__ == "__main__":
    config = get_config()
    print(config)
