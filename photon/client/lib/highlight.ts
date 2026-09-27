/** A tiny, dependency-free tokenizer for rendering code evidence.
 *
 * Why not shiki/prism/highlight.js: this renders inside a live call panel
 * that updates while people are talking, and every one of those pulls in
 * hundreds of KB plus a grammar per language. The evidence snippets here
 * are a handful of lines, and the goal is legibility — telling a string
 * from a keyword from a comment — not editor-grade accuracy.
 *
 * Tokens are returned as data, never as HTML. The caller renders React
 * elements, so repository content (which is effectively untrusted input)
 * can never become markup — building highlighted HTML with regexes and
 * injecting it is a genuine XSS path, and one this deliberately avoids.
 */
export type TokenKind = "plain" | "keyword" | "string" | "comment" | "number" | "function";
export type Token = { text: string; kind: TokenKind };

const KEYWORDS: Record<string, string[]> = {
  python: "def class return if elif else for while import from as try except finally raise with lambda yield async await pass break continue in is not and or None True False global nonlocal assert del".split(" "),
  javascript: "function class return if else for while import from as try catch finally throw new typeof instanceof const let var async await yield export default extends super this null undefined true false switch case break continue delete in of".split(" "),
  go: "func type struct interface return if else for range import package var const go defer chan map select switch case break continue nil true false".split(" "),
  java: "public private protected class interface extends implements return if else for while import package new try catch finally throw throws static final void int long double boolean String null true false this super".split(" "),
  ruby: "def class module return if elsif else end for while require include attr_accessor do yield begin rescue ensure raise nil true false self unless case when".split(" "),
  rust: "fn struct enum impl trait return if else for while let mut match use pub mod crate self super where async await unsafe const static true false None Some Ok Err".split(" "),
};

const EXT_LANG: Record<string, string> = {
  py: "python", pyi: "python",
  js: "javascript", jsx: "javascript", ts: "javascript", tsx: "javascript", mjs: "javascript",
  go: "go", java: "java", kt: "java", rb: "ruby", rs: "rust",
};

const LINE_COMMENT: Record<string, string> = {
  python: "#", ruby: "#", javascript: "//", go: "//", java: "//", rust: "//",
};

export function languageFor(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LANG[ext] ?? "";
}

/** Split one line into tokens. Line-at-a-time on purpose: it keeps line
 * numbering exact and means an unterminated string can never swallow the
 * rest of the file. */
export function tokenizeLine(line: string, language: string): Token[] {
  const keywords = new Set(KEYWORDS[language] ?? []);
  const comment = LINE_COMMENT[language];
  const tokens: Token[] = [];
  let i = 0;
  let buffer = "";

  const flush = () => {
    if (!buffer) return;
    // A bare word is a keyword, a number, or plain text. Checked here so
    // the scanner below stays about delimiters only.
    if (keywords.has(buffer)) tokens.push({ text: buffer, kind: "keyword" });
    else if (/^\d[\d_.]*$/.test(buffer)) tokens.push({ text: buffer, kind: "number" });
    else tokens.push({ text: buffer, kind: "plain" });
    buffer = "";
  };

  while (i < line.length) {
    const rest = line.slice(i);

    if (comment && rest.startsWith(comment)) {
      flush();
      tokens.push({ text: rest, kind: "comment" });
      return tokens;
    }

    const char = line[i];
    if (char === '"' || char === "'" || char === "`") {
      flush();
      let j = i + 1;
      while (j < line.length) {
        if (line[j] === "\\") j += 2;
        else if (line[j] === char) { j += 1; break; }
        else j += 1;
      }
      tokens.push({ text: line.slice(i, j), kind: "string" });
      i = j;
      continue;
    }

    if (/[A-Za-z0-9_$]/.test(char)) {
      buffer += char;
      i += 1;
      continue;
    }

    flush();
    // A word immediately followed by "(" reads as a call — the single most
    // useful thing to pick out when someone says "look at this function".
    if (char === "(" && tokens.length && tokens[tokens.length - 1].kind === "plain") {
      tokens[tokens.length - 1].kind = "function";
    }
    tokens.push({ text: char, kind: "plain" });
    i += 1;
  }
  flush();
  return tokens;
}

/** Parse "path/to/file.py:L42-L58" into its parts. */
export function parseLocator(locator: string): { path: string; startLine: number | null } {
  const match = locator.match(/^(.*?):L(\d+)(?:-L?\d+)?$/);
  if (!match) return { path: locator, startLine: null };
  return { path: match[1], startLine: Number(match[2]) };
}
