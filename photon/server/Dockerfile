FROM python:3.12-slim

# System deps for Tree-sitter + git
RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download Tree-sitter language grammars
RUN python -c "\
import subprocess, sys; \
grammars = [ \
    'https://github.com/tree-sitter/tree-sitter-python', \
    'https://github.com/tree-sitter/tree-sitter-javascript', \
    'https://github.com/tree-sitter/tree-sitter-typescript', \
    'https://github.com/tree-sitter/tree-sitter-go', \
    'https://github.com/tree-sitter/tree-sitter-rust', \
    'https://github.com/tree-sitter/tree-sitter-java', \
]; \
[subprocess.run(['git', 'clone', '--depth=1', g, f'/tmp/{g.split(\"/\")[-1]}'], check=True) for g in grammars] \
"

COPY . .

EXPOSE 8000
