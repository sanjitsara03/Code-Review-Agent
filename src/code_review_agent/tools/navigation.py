import fnmatch
import os
import subprocess

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

MAX_BYTES = 4096

#Converts a relative path to a full absolute path and ensures it is within the repo directory.
def _safe_path(repo_path: str, relative: str) -> str | None:
    """Resolve relative path inside repo_path. Returns None if it escapes the repo."""
    full = os.path.realpath(os.path.join(repo_path, relative))
    if not full.startswith(os.path.realpath(repo_path)):
        return None
    return full

# When a file isn't found, this function looks for files with similar names to help the agent recover.
def _suggest_similar(repo_path: str, path: str) -> str:
    """Return a short list of files with similar names to help Claude recover."""
    target = os.path.basename(path)
    matches = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".venv", "node_modules")]
        for f in files:
            if target.lower() in f.lower():
                matches.append(os.path.relpath(os.path.join(root, f), repo_path))
        if len(matches) >= 5:
            break
    return ", ".join(matches) if matches else "no similar files found"

# This tool reads a file from the repository and returns its contents with line numbers. The agent can specify line ranges to read large files in chunks.
@tool
def read_file(path: str, config: RunnableConfig, start_line: int | None = None, end_line: int | None = None) -> str:
    """Read a file from the repository with line numbers.

    Args:
        path: Relative path from repo root, e.g. 'src/utils.py'.
        start_line: First line to return (1-indexed, inclusive). Omit to start from line 1.
        end_line: Last line to return (1-indexed, inclusive). Omit to read to end of file.
    """
    repo_path = config["configurable"]["repo_path"]
    full_path = _safe_path(repo_path, path)

    if full_path is None:
        return f"ERROR: path '{path}' escapes the repository root."
    if not os.path.isfile(full_path):
        similar = _suggest_similar(repo_path, path)
        return f"ERROR: file not found: '{path}'. Similar files: {similar}"

    with open(full_path, errors="replace") as f:
        lines = f.readlines()

    s = (start_line - 1) if start_line else 0
    e = end_line if end_line else len(lines)
    selected = lines[s:e]

    numbered = [f"{s + i + 1:4d} | {line}" for i, line in enumerate(selected)]
    result = "".join(numbered)

    if len(result) > MAX_BYTES:
        result = result[:MAX_BYTES]
        result += f"\n... [truncated — use start_line/end_line to read specific ranges. File has {len(lines)} lines total.]"

    return result

# This tool lists files and directories at a given path, which helps the agent explore the repo structure and find files to read or search.
@tool
def list_directory(path: str, config: RunnableConfig) -> str:
    """List files and folders at a path in the repository.

    Args:
        path: Relative directory path, e.g. 'src/' or '.' for the repo root.
    """
    repo_path = config["configurable"]["repo_path"]
    full_path = _safe_path(repo_path, path)

    if full_path is None:
        return f"ERROR: path '{path}' escapes the repository root."
    if not os.path.isdir(full_path):
        return f"ERROR: directory not found: '{path}'"

    entries = sorted(os.scandir(full_path), key=lambda e: (not e.is_dir(), e.name))
    lines = []
    for entry in entries:
        kind = "dir " if entry.is_dir() else "file"
        lines.append(f"  [{kind}] {entry.name}")

    return "\n".join(lines) if lines else "(empty directory)"

# This tool allows the agent to search for specific strings or patterns across the codebase.
@tool
def search_code(query: str, config: RunnableConfig, file_pattern: str | None = None) -> str:
    """Search for a string or pattern across the repository source files.

    Args:
        query: The search string or regex pattern.
        file_pattern: Optional glob to restrict search, e.g. '*.py' or 'src/**/*.ts'.
    """
    repo_path = config["configurable"]["repo_path"]

    cmd = ["rg", "--line-number", "--with-filename", "--max-count", "5", query, repo_path]
    if file_pattern:
        cmd += ["--glob", file_pattern]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        # rg exit code 0 = matches found, 1 = no matches, 2 = error
        if result.returncode == 2:
            raise FileNotFoundError("rg not available")
        output = result.stdout[:MAX_BYTES]
        return output if output.strip() else f"No matches found for '{query}'."
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Fallback: pure Python search
        return _python_search(repo_path, query, file_pattern)


def _python_search(repo_path: str, query: str, file_pattern: str | None) -> str:
    """Fallback code search using Python (used when ripgrep is not installed)."""
    matches = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".venv", "node_modules")]
        for fname in files:
            if file_pattern and not fnmatch.fnmatch(fname, file_pattern):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if query.lower() in line.lower():
                            rel = os.path.relpath(fpath, repo_path)
                            matches.append(f"{rel}:{i}: {line.rstrip()}")
                            if len(matches) >= 20:
                                break
            except OSError:
                continue
            if len(matches) >= 20:
                break

    if not matches:
        return f"No matches found for '{query}'."
    result = "\n".join(matches)
    if len(result) > MAX_BYTES:
        result = result[:MAX_BYTES] + "\n... [truncated]"
    return result

# This tool finds files in the repository by name pattern.
@tool
def find_file(name_pattern: str, config: RunnableConfig) -> str:
    """Find files in the repository by name pattern.

    Args:
        name_pattern: Glob pattern to match filenames, e.g. '*.py', 'test_*.py', 'config.*'.
    """
    repo_path = config["configurable"]["repo_path"]
    matches = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".venv", "node_modules")]
        for fname in files:
            if fnmatch.fnmatch(fname, name_pattern):
                matches.append(os.path.relpath(os.path.join(root, fname), repo_path))
        if len(matches) >= 50:
            break

    if not matches:
        return f"No files matching '{name_pattern}'."
    return "\n".join(sorted(matches))
