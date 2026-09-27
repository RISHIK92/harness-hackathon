from __future__ import annotations
from pathlib import Path

# Extension → language name
_EXT_MAP: dict[str, str] = {
    # Python
    ".py": "python",
    # JavaScript / TypeScript
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    # Web
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    # Data / config
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".env": "dotenv",
    # Systems languages
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".cs": "csharp",
    # Shell / scripting
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".rb": "ruby",
    ".php": "php",
    ".lua": "lua",
    ".r": "r",
    ".R": "r",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    # Docs
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "rst",
    ".txt": "text",
    # SQL
    ".sql": "sql",
    # GraphQL
    ".graphql": "graphql",
    ".gql": "graphql",
    # Dockerfile
    "Dockerfile": "dockerfile",
}

# Languages that the tree-sitter parser can handle
PARSEABLE_LANGUAGES: frozenset[str] = frozenset({
    "python",
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "cpp",
    "c",
})


def detect_language(path: str) -> str:
    """
    Return the language identifier for a given file path.
    Returns 'unknown' if the extension is not recognised.
    """
    p = Path(path)

    # Handle bare filenames like 'Dockerfile'
    if p.name in _EXT_MAP:
        return _EXT_MAP[p.name]

    ext = p.suffix.lower()
    return _EXT_MAP.get(ext, "unknown")
